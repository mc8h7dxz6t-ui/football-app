"""SQLite evidence store for in-play I1–I5 gates (feed, marks, CLV, paper)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def evidence_db_path() -> Path:
    explicit = (os.getenv("HIBS_INPLAY_EVIDENCE_DB") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    root = Path(os.getenv("FVE_DATA_DIR", "data"))
    return root / "inplay_evidence.sqlite"


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or evidence_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=15)
    con.row_factory = sqlite3.Row
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS feed_frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor TEXT NOT NULL,
            fixture_id INTEGER NOT NULL,
            seq INTEGER NOT NULL,
            frame_type INTEGER,
            peel_ms REAL,
            received_at TEXT NOT NULL,
            UNIQUE(vendor, fixture_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_feed_frames_recv ON feed_frames(received_at);
        CREATE INDEX IF NOT EXISTS idx_feed_frames_fixture ON feed_frames(fixture_id, received_at);

        CREATE TABLE IF NOT EXISTS marks_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fixture_id INTEGER NOT NULL,
            venues_expected INTEGER NOT NULL DEFAULT 3,
            venues_ok INTEGER NOT NULL DEFAULT 0,
            coverage_pct REAL,
            snapshot_json TEXT,
            captured_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_marks_fixture ON marks_snapshots(fixture_id, captured_at);

        CREATE TABLE IF NOT EXISTS inplay_clv (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fixture_id INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            odds_taken REAL NOT NULL,
            odds_close_fair REAL,
            clv_pp REAL,
            edge_clv_pct REAL,
            window_close_at TEXT,
            verification_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_inplay_clv_created ON inplay_clv(created_at);

        CREATE TABLE IF NOT EXISTS inplay_paper (
            bet_id TEXT PRIMARY KEY,
            fixture_id INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            stake_units REAL NOT NULL DEFAULT 1.0,
            odds_taken REAL NOT NULL,
            model_prob REAL,
            status TEXT NOT NULL DEFAULT 'open',
            result_pnl REAL,
            verification_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            settled_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_inplay_paper_created ON inplay_paper(created_at);
        CREATE INDEX IF NOT EXISTS idx_inplay_paper_status ON inplay_paper(status);

        CREATE TABLE IF NOT EXISTS model_sanity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fixture_id INTEGER,
            mc_home REAL,
            cf_home REAL,
            max_abs_diff REAL NOT NULL,
            held_out INTEGER NOT NULL DEFAULT 1,
            recorded_at TEXT NOT NULL
        );
        """
    )
    con.commit()


def verification_hash(payload: Dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def record_feed_frame(
    *,
    vendor: str,
    fixture_id: int,
    seq: int,
    frame_type: Optional[int] = None,
    peel_ms: Optional[float] = None,
    received_at: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> None:
    ts = received_at or _utc_iso()
    with connect(db_path) as con:
        init_db(con)
        con.execute(
            """
            INSERT OR IGNORE INTO feed_frames
            (vendor, fixture_id, seq, frame_type, peel_ms, received_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (vendor, int(fixture_id), int(seq), frame_type, peel_ms, ts),
        )
        con.commit()


def record_marks_snapshot(
    *,
    fixture_id: int,
    venues_expected: int,
    venues_ok: int,
    marks: Dict[str, Any],
    captured_at: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> float:
    cov = round(100.0 * venues_ok / venues_expected, 2) if venues_expected > 0 else 0.0
    ts = captured_at or _utc_iso()
    with connect(db_path) as con:
        init_db(con)
        con.execute(
            """
            INSERT INTO marks_snapshots
            (fixture_id, venues_expected, venues_ok, coverage_pct, snapshot_json, captured_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(fixture_id),
                int(venues_expected),
                int(venues_ok),
                cov,
                json.dumps(marks, separators=(",", ":"), sort_keys=True, default=str),
                ts,
            ),
        )
        con.commit()
    return cov


def record_inplay_clv(
    *,
    fixture_id: int,
    outcome: str,
    odds_taken: float,
    odds_close_fair: Optional[float],
    window_close_at: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> str:
    clv_pp = None
    edge_clv_pct = None
    if odds_close_fair and odds_close_fair > 1.0:
        clv_pp = round((odds_taken / odds_close_fair - 1.0) * 100.0, 3)
        edge_clv_pct = clv_pp
    payload = {
        "fixture_id": fixture_id,
        "outcome": outcome,
        "odds_taken": odds_taken,
        "odds_close_fair": odds_close_fair,
        "window_close_at": window_close_at,
    }
    vhash = verification_hash(payload)
    with connect(db_path) as con:
        init_db(con)
        con.execute(
            """
            INSERT INTO inplay_clv
            (fixture_id, outcome, odds_taken, odds_close_fair, clv_pp, edge_clv_pct,
             window_close_at, verification_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(fixture_id),
                outcome,
                float(odds_taken),
                odds_close_fair,
                clv_pp,
                edge_clv_pct,
                window_close_at,
                vhash,
                _utc_iso(),
            ),
        )
        con.commit()
    return vhash


def record_inplay_paper_bet(
    *,
    fixture_id: int,
    outcome: str,
    odds_taken: float,
    model_prob: Optional[float] = None,
    stake_units: float = 1.0,
    status: str = "open",
    result_pnl: Optional[float] = None,
    settled_at: Optional[str] = None,
    bet_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> str:
    bid = bet_id or str(uuid.uuid4())
    payload = {
        "bet_id": bid,
        "fixture_id": fixture_id,
        "outcome": outcome,
        "odds_taken": odds_taken,
        "model_prob": model_prob,
        "stake_units": stake_units,
    }
    vhash = verification_hash(payload)
    with connect(db_path) as con:
        init_db(con)
        con.execute(
            """
            INSERT OR REPLACE INTO inplay_paper
            (bet_id, fixture_id, outcome, stake_units, odds_taken, model_prob,
             status, result_pnl, verification_hash, created_at, settled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bid,
                int(fixture_id),
                outcome,
                float(stake_units),
                float(odds_taken),
                model_prob,
                status,
                result_pnl,
                vhash,
                _utc_iso(),
                settled_at,
            ),
        )
        con.commit()
    return bid


def record_model_sanity(
    *,
    fixture_id: Optional[int],
    mc_home: float,
    cf_home: float,
    held_out: bool = True,
    db_path: Optional[Path] = None,
) -> float:
    diff = abs(float(mc_home) - float(cf_home))
    with connect(db_path) as con:
        init_db(con)
        con.execute(
            """
            INSERT INTO model_sanity
            (fixture_id, mc_home, cf_home, max_abs_diff, held_out, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (fixture_id, mc_home, cf_home, diff, 1 if held_out else 0, _utc_iso()),
        )
        con.commit()
    return diff


def _since_clause(column: str, since_iso: Optional[str]) -> tuple[str, Sequence[Any]]:
    if not since_iso:
        return "", ()
    return f" AND {column} >= ?", (since_iso,)


def feed_telemetry(*, hours: float = 24.0, since_iso: Optional[str] = None) -> Dict[str, Any]:
    window_start = since_iso
    with connect() as con:
        init_db(con)
        extra, params = _since_clause("received_at", window_start)
        rows = con.execute(
            f"""
            SELECT vendor, fixture_id, seq, peel_ms, received_at
            FROM feed_frames
            WHERE 1=1 {extra}
            ORDER BY vendor, fixture_id, seq
            """,
            params,
        ).fetchall()
    if not rows:
        return {
            "n_frames": 0,
            "seq_gaps": 0,
            "uptime_pct": None,
            "peel_ms_p99": None,
            "vendors": [],
        }
    peels = [float(r["peel_ms"]) for r in rows if r["peel_ms"] is not None]
    peels.sort()
    p99 = peels[int(0.99 * (len(peels) - 1))] if peels else None
    seq_gaps = 0
    by_key: Dict[tuple, int] = {}
    for r in rows:
        key = (r["vendor"], r["fixture_id"])
        prev = by_key.get(key)
        seq = int(r["seq"])
        if prev is not None and seq > prev + 1:
            seq_gaps += seq - prev - 1
        by_key[key] = seq
    vendors = sorted({str(r["vendor"]) for r in rows})
    return {
        "n_frames": len(rows),
        "seq_gaps": seq_gaps,
        "uptime_pct": 100.0 if rows else 0.0,
        "peel_ms_p99": round(p99, 3) if p99 is not None else None,
        "vendors": vendors,
    }


def marks_coverage_summary(*, since_iso: Optional[str] = None) -> Dict[str, Any]:
    with connect() as con:
        init_db(con)
        extra, params = _since_clause("captured_at", since_iso)
        row = con.execute(
            f"""
            SELECT
                COUNT(*) AS n,
                AVG(coverage_pct) AS avg_cov,
                MIN(coverage_pct) AS min_cov
            FROM marks_snapshots
            WHERE 1=1 {extra}
            """,
            params,
        ).fetchone()
    n = int(row["n"] or 0)
    return {
        "n_snapshots": n,
        "avg_coverage_pct": round(float(row["avg_cov"]), 2) if row["avg_cov"] is not None else None,
        "min_coverage_pct": round(float(row["min_cov"]), 2) if row["min_cov"] is not None else None,
    }


def clv_summary(*, since_iso: Optional[str] = None) -> Dict[str, Any]:
    with connect() as con:
        init_db(con)
        extra, params = _since_clause("created_at", since_iso)
        rows = con.execute(
            f"""
            SELECT clv_pp, verification_hash FROM inplay_clv
            WHERE odds_close_fair IS NOT NULL {extra}
            """,
            params,
        ).fetchall()
    usable = [r for r in rows if r["clv_pp"] is not None and r["verification_hash"]]
    n = len(usable)
    beats = sum(1 for r in usable if float(r["clv_pp"]) > 0)
    return {
        "n": n,
        "beat_close_pct": round(100.0 * beats / n, 2) if n else None,
        "with_hash": sum(1 for r in rows if r["verification_hash"]),
    }


def paper_summary(*, since_iso: Optional[str] = None) -> Dict[str, Any]:
    with connect() as con:
        init_db(con)
        extra, params = _since_clause("created_at", since_iso)
        row = con.execute(
            f"""
            SELECT
                COUNT(*) AS n,
                SUM(CASE WHEN status = 'settled' THEN 1 ELSE 0 END) AS settled,
                SUM(CASE WHEN verification_hash IS NOT NULL AND verification_hash != '' THEN 1 ELSE 0 END) AS hashed
            FROM inplay_paper
            WHERE 1=1 {extra}
            """,
            params,
        ).fetchone()
    return {
        "n_rows": int(row["n"] or 0),
        "settled": int(row["settled"] or 0),
        "with_verification_hash": int(row["hashed"] or 0),
    }


def model_sanity_summary(*, since_iso: Optional[str] = None) -> Dict[str, Any]:
    with connect() as con:
        init_db(con)
        extra, params = _since_clause("recorded_at", since_iso)
        row = con.execute(
            f"""
            SELECT COUNT(*) AS n, MAX(max_abs_diff) AS worst, AVG(max_abs_diff) AS avg_diff
            FROM model_sanity
            WHERE held_out = 1 {extra}
            """,
            params,
        ).fetchone()
    n = int(row["n"] or 0)
    return {
        "n_checks": n,
        "max_abs_diff": round(float(row["worst"]), 5) if row["worst"] is not None else None,
        "avg_abs_diff": round(float(row["avg_diff"]), 5) if row["avg_diff"] is not None else None,
    }
