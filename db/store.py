"""Postgres / SQLite persistence for line snapshots."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

_DB_URL = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'fve.db'))}",
)

_engine = None
_Session = None


def _ensure_engine():
    global _engine, _Session
    if _engine is not None:
        return
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    if _DB_URL.startswith("sqlite:///"):
        db_path = _DB_URL.replace("sqlite:///", "", 1)
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    _engine = create_engine(_DB_URL, pool_pre_ping=True)
    _Session = sessionmaker(bind=_engine)
    from db.models import Base

    Base.metadata.create_all(_engine)
    _migrate_paper_picks(_engine)


def _migrate_paper_picks(engine) -> None:
    """SQLite-safe add columns for CLV benchmark tier (idempotent)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "paper_picks" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("paper_picks")}
    with engine.begin() as conn:
        if "clv_benchmark_tier" not in cols:
            conn.execute(text("ALTER TABLE paper_picks ADD COLUMN clv_benchmark_tier VARCHAR(32)"))
        if "clv_benchmark_source" not in cols:
            conn.execute(text("ALTER TABLE paper_picks ADD COLUMN clv_benchmark_source VARCHAR(64)"))


def persist_snapshot(fixture_key: str, payload: Dict[str, Any]) -> None:
    _ensure_engine()
    from db.models import LineSnapshot

    session = _Session()
    try:
        row = LineSnapshot(
            fixture_key=fixture_key,
            payload_json=json.dumps(payload, default=str),
            tick_count=int(payload.get("tick_count") or 0),
            stale=bool(payload.get("stale")),
            created_at=time.time(),
        )
        session.add(row)
        session.commit()
    finally:
        session.close()


def latest_snapshots(limit: int = 50) -> List[Dict[str, Any]]:
    _ensure_engine()
    from db.models import LineSnapshot

    session = _Session()
    try:
        rows = session.query(LineSnapshot).order_by(LineSnapshot.id.desc()).limit(limit).all()
        out = []
        for r in rows:
            out.append(
                {
                    "fixture_key": r.fixture_key,
                    "tick_count": r.tick_count,
                    "stale": r.stale,
                    "created_at": r.created_at,
                    "payload": json.loads(r.payload_json),
                }
            )
        return out
    finally:
        session.close()
