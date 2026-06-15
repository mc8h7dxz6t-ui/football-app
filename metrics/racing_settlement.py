"""Write finish_position (+ preserve score) into hibs-racing feature_store.sqlite."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from metrics.feature_store_lock import feature_store_lock, lock_wait_enabled
from metrics.feature_store_migrations import apply_feature_store_migrations
from metrics.racing_sqlite import (
    MODEL_PROB_COLS,
    PLACE_POSITIONS_COLS,
    POSITION_COLS,
    RACE_ID_COLS,
    RUNNER_ID_COLS,
    VENUE_COLS,
    VENUE_MAPPED_COLS,
    _choose_source_table,
    _columns,
    _pick,
)

DEFAULT_BUSY_TIMEOUT_MS = 30_000


@dataclass
class SettlementResult:
    race_id: str
    rows_updated: int
    rows_missing: int
    table: str


def _column_exists(con: sqlite3.Connection, table: str, col: Optional[str]) -> bool:
    if not col:
        return False
    return col.lower() in {c.lower() for c in _columns(con, table)}


def resolve_writer_columns(con: sqlite3.Connection, table: str) -> Dict[str, Optional[str]]:
    cols = _columns(con, table)
    return {
        "race_id": _pick(cols, RACE_ID_COLS),
        "runner_id": _pick(cols, RUNNER_ID_COLS),
        "position": _pick(cols, POSITION_COLS) or "finish_position",
        "score": _pick(cols, MODEL_PROB_COLS),
        "venue_id": _pick(cols, VENUE_COLS),
        "venue_mapped": _pick(cols, VENUE_MAPPED_COLS),
        "place_positions": _pick(cols, PLACE_POSITIONS_COLS),
    }


def ensure_feature_store_schema(
    db_path: str | Path,
    *,
    table: Optional[str] = None,
    use_lock: bool = True,
) -> Dict[str, Any]:
    """Apply versioned migrations before read-only verification (additive DDL)."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"feature_store not found: {path}")

    def _run() -> Dict[str, Any]:
        con = _connect_write(path)
        try:
            src = _choose_source_table(con, table=table)
            return apply_feature_store_migrations(con, src)
        finally:
            con.close()

    if use_lock:
        with feature_store_lock(path):
            return _run()
    return _run()


def _connect_write(db_path: Path, *, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=busy_timeout_ms / 1000.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return con


def _prepare_writer(
    con: sqlite3.Connection,
    *,
    table: Optional[str] = None,
) -> Tuple[str, Dict[str, Optional[str]]]:
    src = _choose_source_table(con, table=table)
    apply_feature_store_migrations(con, src)
    wcols = resolve_writer_columns(con, src)
    race_col = wcols["race_id"]
    runner_col = wcols["runner_id"]
    if not race_col or not runner_col:
        raise ValueError(f"{src} missing race_id/runner_id columns")
    return src, wcols


def _apply_race_on_connection(
    con: sqlite3.Connection,
    src: str,
    wcols: Dict[str, Optional[str]],
    race_id: str,
    runners: Sequence[Dict[str, Any]],
    *,
    venue_id: Optional[str] = None,
    venue_mapped: Optional[bool] = None,
    place_positions: Optional[int] = None,
) -> SettlementResult:
    race_col = wcols["race_id"]
    runner_col = wcols["runner_id"]
    pos_col = wcols["position"]
    score_col = wcols["score"]

    updated = 0
    missing = 0
    for r in runners:
        rid = r.get("runner_id") or r.get("id")
        pos = r.get("finish_position", r.get("position", r.get("pos")))
        if rid is None or pos is None:
            missing += 1
            continue
        try:
            pos_i = int(float(pos))
        except (TypeError, ValueError):
            missing += 1
            continue

        sets = [f"[{pos_col}] = ?"]
        params: List[Any] = [pos_i]

        new_score = r.get("score", r.get("place_prob", r.get("p_place")))
        if new_score is not None and score_col:
            sets.append(f"[{score_col}] = ?")
            params.append(float(new_score))

        if venue_id and wcols["venue_id"]:
            sets.append(f"[{wcols['venue_id']}] = ?")
            params.append(str(venue_id))
        if venue_mapped is not None and wcols["venue_mapped"]:
            sets.append(f"[{wcols['venue_mapped']}] = ?")
            params.append(1 if venue_mapped else 0)
        if place_positions is not None and wcols["place_positions"]:
            sets.append(f"[{wcols['place_positions']}] = ?")
            params.append(int(place_positions))

        params.extend([str(race_id), str(rid)])
        cur = con.execute(
            f"""
            UPDATE [{src}]
            SET {", ".join(sets)}
            WHERE [{race_col}] = ? AND [{runner_col}] = ?
            """,
            params,
        )
        if cur.rowcount == 0:
            missing += 1
        else:
            updated += cur.rowcount

    return SettlementResult(race_id=str(race_id), rows_updated=updated, rows_missing=missing, table=src)


def apply_race_results(
    db_path: str | Path,
    race_id: str,
    runners: Sequence[Dict[str, Any]],
    *,
    table: Optional[str] = None,
    venue_id: Optional[str] = None,
    venue_mapped: Optional[bool] = None,
    place_positions: Optional[int] = None,
    use_lock: bool = True,
    wait_lock: bool | None = None,
) -> SettlementResult:
    """Idempotent settlement: set finish_position per runner; preserve existing score unless provided.

    Each runner dict:
      runner_id (required), finish_position | position | pos (required),
      score | place_prob | p_place (optional — omitted = keep existing DB value)
    """
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"feature_store not found: {path}")

    if wait_lock is None:
        wait_lock = lock_wait_enabled()

    def _run() -> SettlementResult:
        con = _connect_write(path)
        try:
            src, wcols = _prepare_writer(con, table=table)
            con.execute("BEGIN IMMEDIATE")
            try:
                result = _apply_race_on_connection(
                    con,
                    src,
                    wcols,
                    race_id,
                    runners,
                    venue_id=venue_id,
                    venue_mapped=venue_mapped,
                    place_positions=place_positions,
                )
                con.commit()
                return result
            except Exception:
                con.rollback()
                raise
        finally:
            con.close()

    if use_lock:
        with feature_store_lock(path, wait=wait_lock):
            return _run()
    return _run()


def apply_results_batch(
    db_path: str | Path,
    races: Sequence[Dict[str, Any]],
    *,
    table: Optional[str] = None,
    use_lock: bool = True,
    wait_lock: bool | None = None,
) -> Dict[str, Any]:
    """Apply many races in one SQLite transaction under the shared feature_store flock.

    Payload shape per race (same as JSONL):
      race_id, runners[{runner_id, finish_position, score?}], venue_id, venue_mapped, place_positions

    On any failure the entire batch rolls back (no partial multi-race commits).
    """
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"feature_store not found: {path}")

    if wait_lock is None:
        wait_lock = lock_wait_enabled()

    def _run() -> Dict[str, Any]:
        results: List[Dict[str, Any]] = []
        total_updated = 0
        total_missing = 0
        con = _connect_write(path)
        try:
            src, wcols = _prepare_writer(con, table=table)
            con.execute("BEGIN IMMEDIATE")
            try:
                for race in races:
                    rid = race.get("race_id")
                    if not rid:
                        continue
                    runners = race.get("runners") or []
                    sr = _apply_race_on_connection(
                        con,
                        src,
                        wcols,
                        str(rid),
                        runners,
                        venue_id=race.get("venue_id"),
                        venue_mapped=race.get("venue_mapped"),
                        place_positions=race.get("place_positions"),
                    )
                    total_updated += sr.rows_updated
                    total_missing += sr.rows_missing
                    results.append(
                        {
                            "race_id": sr.race_id,
                            "rows_updated": sr.rows_updated,
                            "rows_missing": sr.rows_missing,
                        }
                    )
                con.commit()
            except Exception:
                con.rollback()
                raise
        finally:
            con.close()
        return {
            "races": len(results),
            "rows_updated": total_updated,
            "rows_missing": total_missing,
            "transaction": "single",
            "details": results,
        }

    if use_lock:
        with feature_store_lock(path, wait=wait_lock):
            return _run()
    return _run()


def settlement_coverage(db_path: str | Path, *, table: Optional[str] = None) -> Dict[str, Any]:
    """Post-settlement telemetry: scored vs positioned runners."""
    path = Path(db_path)
    if not path.is_file():
        return {"ok": False, "error": "missing_db"}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    try:
        src = _choose_source_table(con, table=table)
        w = resolve_writer_columns(con, src)
        pos_col = w["position"] if _column_exists(con, src, w["position"]) else None
        score_col = w["score"] if _column_exists(con, src, w["score"]) else None
        total = int(con.execute(f"SELECT COUNT(*) FROM [{src}]").fetchone()[0])
        with_pos = 0
        with_score = 0
        if pos_col:
            with_pos = int(
                con.execute(
                    f"SELECT COUNT(*) FROM [{src}] WHERE [{pos_col}] IS NOT NULL AND [{pos_col}] > 0"
                ).fetchone()[0]
            )
        if score_col:
            with_score = int(
                con.execute(
                    f"SELECT COUNT(*) FROM [{src}] WHERE [{score_col}] IS NOT NULL"
                ).fetchone()[0]
            )
        races_settled = 0
        if w["race_id"] and pos_col:
            races_settled = int(
                con.execute(
                    f"""
                    SELECT COUNT(DISTINCT [{w['race_id']}]) FROM [{src}]
                    WHERE [{pos_col}] IS NOT NULL AND [{pos_col}] > 0
                    """
                ).fetchone()[0]
            )
        return {
            "ok": True,
            "table": src,
            "runners_total": total,
            "runners_with_position": with_pos,
            "runners_with_score": with_score,
            "races_with_results": races_settled,
            "position_pct": round(100.0 * with_pos / total, 2) if total else 0.0,
            "score_pct": round(100.0 * with_score / total, 2) if total else 0.0,
        }
    finally:
        con.close()
