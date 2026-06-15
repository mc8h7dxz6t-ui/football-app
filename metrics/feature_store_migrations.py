"""Versioned schema migrations for hibs-racing feature_store.sqlite.

Replaces ad-hoc runtime ``ALTER TABLE`` with a tracked ``schema_migrations`` ledger.
Each migration runs in its own ``BEGIN IMMEDIATE`` transaction; failure rolls back
both DDL (when SQLite allows) and the ledger row.

SQLite cannot drop columns — rollbacks are forward-only repair scripts, not automatic.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from metrics.racing_sqlite import (
    MODEL_PROB_COLS,
    POSITION_COLS,
    _columns,
    _pick,
)

MIGRATIONS_TABLE = "schema_migrations"


class MigrationDriftError(RuntimeError):
    """Applied migration checksum no longer matches registry (manual edit or skew)."""


class MigrationFailure(RuntimeError):
    """Migration upgrade raised; transaction rolled back."""


@dataclass(frozen=True)
class MigrationSpec:
    version: str
    name: str
    upgrade: Callable[[sqlite3.Connection, str], None]

    @property
    def checksum(self) -> str:
        payload = f"{self.version}:{self.name}:{self.upgrade.__doc__ or ''}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_migrations_table(con: sqlite3.Connection) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS [{MIGRATIONS_TABLE}] (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _applied_migrations(con: sqlite3.Connection) -> Dict[str, Dict[str, str]]:
    _ensure_migrations_table(con)
    rows = con.execute(
        f"SELECT version, name, checksum, applied_at FROM [{MIGRATIONS_TABLE}] ORDER BY version"
    ).fetchall()
    return {
        str(r[0]): {
            "name": str(r[1]),
            "checksum": str(r[2]),
            "applied_at": str(r[3]),
        }
        for r in rows
    }


def _column_names(con: sqlite3.Connection, table: str) -> set[str]:
    return {c.lower() for c in _columns(con, table)}


def _add_column_if_missing(
    con: sqlite3.Connection,
    table: str,
    column: str,
    ddl_type: str,
) -> bool:
    if column.lower() in _column_names(con, table):
        return False
    con.execute(f"ALTER TABLE [{table}] ADD COLUMN [{column}] {ddl_type}")
    return True


def _upgrade_001_settlement_columns(con: sqlite3.Connection, table: str) -> None:
    """Add finish_position and score columns required for settlement writes."""
    cols = _columns(con, table)
    pos_col = _pick(cols, POSITION_COLS)
    score_col = _pick(cols, MODEL_PROB_COLS)
    if not pos_col:
        _add_column_if_missing(con, table, "finish_position", "INTEGER")
    if not score_col:
        _add_column_if_missing(con, table, "score", "REAL")


FEATURE_STORE_MIGRATIONS: List[MigrationSpec] = [
    MigrationSpec("001", "settlement_columns", _upgrade_001_settlement_columns),
]


def apply_feature_store_migrations(
    con: sqlite3.Connection,
    table: str,
    *,
    migrations: Sequence[MigrationSpec] = FEATURE_STORE_MIGRATIONS,
) -> Dict[str, Any]:
    """Apply pending migrations; verify checksums for already-applied versions."""
    applied = _applied_migrations(con)
    ran: List[str] = []
    skipped: List[str] = []

    for spec in migrations:
        meta = applied.get(spec.version)
        if meta:
            if meta["checksum"] != spec.checksum:
                raise MigrationDriftError(
                    f"migration {spec.version} checksum mismatch "
                    f"(applied={meta['checksum']}, expected={spec.checksum})"
                )
            skipped.append(spec.version)
            continue

        con.execute("BEGIN IMMEDIATE")
        try:
            spec.upgrade(con, table)
            con.execute(
                f"""
                INSERT INTO [{MIGRATIONS_TABLE}] (version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (spec.version, spec.name, spec.checksum, _utc_iso()),
            )
            con.commit()
            ran.append(spec.version)
        except Exception as exc:
            con.rollback()
            raise MigrationFailure(f"migration {spec.version} failed: {exc}") from exc

    return {
        "table": table,
        "applied": ran,
        "already_applied": skipped,
        "head_version": migrations[-1].version if migrations else None,
    }


def migration_status(con: sqlite3.Connection) -> Dict[str, Any]:
    """Read-only ledger for ops / dashboards."""
    applied = _applied_migrations(con)
    pending = [m.version for m in FEATURE_STORE_MIGRATIONS if m.version not in applied]
    return {
        "ledger_table": MIGRATIONS_TABLE,
        "applied": [
            {"version": v, **applied[v]}
            for v in sorted(applied.keys())
        ],
        "pending": pending,
        "head_version": FEATURE_STORE_MIGRATIONS[-1].version if FEATURE_STORE_MIGRATIONS else None,
    }
