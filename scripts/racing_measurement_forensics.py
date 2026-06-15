#!/usr/bin/env python3
"""Institutional++ measurement forensics for racing verification JSONL + SQLite."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from metrics.racing import evaluate_racing_window, racing_record_from_dict
from metrics.racing_measurement import filter_races_after_cutoff, measurement_contract_summary
from metrics.racing_sqlite import (
    MARKET_PLACE_COLS,
    POSITION_COLS,
    WIN_DECIMAL_COLS,
    _columns,
    _pick,
    extract_settled_races_from_db,
)


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def sqlite_place_odds_stats(db: Path, *, table: str = "scored_runner_snapshots") -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if not _table_exists(con, table):
            return {"ok": False, "error": f"table_missing:{table}"}

        cols = _columns(con, table)
        pos_col = _pick(cols, POSITION_COLS)
        place_col = _pick(cols, MARKET_PLACE_COLS)
        win_col = _pick(cols, WIN_DECIMAL_COLS)

        if not pos_col:
            return {
                "ok": False,
                "error": "no_position_column",
                "table": table,
                "columns_sample": cols[:20],
            }

        total = int(
            con.execute(
                f"SELECT COUNT(*) FROM [{table}] "
                f"WHERE [{pos_col}] IS NOT NULL AND CAST([{pos_col}] AS REAL) > 0"
            ).fetchone()[0]
        )

        place_odds = 0
        if place_col:
            place_odds = int(
                con.execute(
                    f"""
                    SELECT COUNT(*) FROM [{table}]
                    WHERE [{pos_col}] IS NOT NULL AND CAST([{pos_col}] AS REAL) > 0
                      AND [{place_col}] IS NOT NULL AND CAST([{place_col}] AS REAL) > 1
                    """
                ).fetchone()[0]
            )

        win_odds = 0
        if win_col:
            win_odds = int(
                con.execute(
                    f"""
                    SELECT COUNT(*) FROM [{table}]
                    WHERE [{pos_col}] IS NOT NULL AND CAST([{pos_col}] AS REAL) > 0
                      AND [{win_col}] IS NOT NULL AND CAST([{win_col}] AS REAL) > 1
                    """
                ).fetchone()[0]
            )

        return {
            "ok": True,
            "table": table,
            "position_column": pos_col,
            "place_odds_column": place_col,
            "win_odds_column": win_col,
            "settled_rows": total,
            "with_place_odds": place_odds,
            "with_win_odds": win_odds,
            "place_odds_pct": round(100.0 * place_odds / total, 2) if total else 0.0,
            "paired_export_viable": bool(place_col and place_odds > 0),
        }
    finally:
        con.close()


def _load_jsonl(path: Path) -> list:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(json.loads(line))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Racing measurement forensics")
    ap.add_argument("--jsonl", type=Path, help="settled_races.jsonl path")
    ap.add_argument("--feature-store", type=Path, help="feature_store.sqlite path")
    ap.add_argument("--train-cutoff", default="", help="OOS cutoff YYYY-MM-DD")
    ap.add_argument("--min-races", type=int, default=1000)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report: dict = {}

    if args.feature_store and args.feature_store.is_file():
        report["sqlite"] = sqlite_place_odds_stats(args.feature_store)
        sqlite_ok = report["sqlite"].get("paired_export_viable", False)
        sample = extract_settled_races_from_db(
            args.feature_store,
            table="scored_runner_snapshots",
            target="place",
            require_paired_place_market=sqlite_ok,
        )
        report["extract_paired_sample"] = {
            "require_paired_place_market": sqlite_ok,
            "races": len(sample),
            "contract": measurement_contract_summary(sample[: min(500, len(sample))]),
        }

    if args.jsonl and args.jsonl.is_file():
        rows = _load_jsonl(args.jsonl)
        report["jsonl_contract"] = measurement_contract_summary(rows)
        cutoff = args.train_cutoff.strip()[:10] or None
        kept, oos = filter_races_after_cutoff(rows, cutoff)
        report["oos_filter"] = oos
        races = []
        for row in kept:
            try:
                races.append(racing_record_from_dict(row))
            except ValueError:
                continue
        if races:
            report["evaluate"] = evaluate_racing_window(
                races,
                min_races=args.min_races,
                oos_declared=True,
                oos_enforced=bool(oos.get("oos_enforced")),
                train_cutoff=cutoff,
                require_paired_market=True,
            )

    text = json.dumps(report, indent=2)
    if args.json:
        print(text)
    else:
        print(text)


if __name__ == "__main__":
    main()
