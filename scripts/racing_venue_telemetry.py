#!/usr/bin/env python3
"""Venue mapping telemetry — institutional ≥95% gate vs current state.

Reads feature_store.sqlite (upcoming_runners) and/or verification JSONL.

Usage:
  python scripts/racing_venue_telemetry.py --feature-store /opt/hibs-racing/data/feature_store.sqlite
  python scripts/racing_venue_telemetry.py --jsonl data/verification/settled_races.jsonl
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from metrics.data_room import institutional_gates
from metrics.racing_sqlite import VENUE_COLS, VENUE_MAPPED_COLS, _columns, _pick


def venue_report_from_db(db_path: Path) -> dict:
    if not db_path.is_file():
        return {"ok": False, "error": f"missing {db_path}"}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=20)
    try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        table = "upcoming_runners" if "upcoming_runners" in tables else (tables[0] if tables else None)
        if not table:
            return {"ok": False, "error": "no tables"}
        cols = _columns(con, table)
        venue_col = _pick(cols, VENUE_COLS)
        mapped_col = _pick(cols, VENUE_MAPPED_COLS)
        race_col = _pick(cols, ("race_id", "race_key", "event_id"))
        if not venue_col:
            return {"ok": False, "error": f"no venue column on {table}", "columns": cols}

        if race_col and mapped_col:
            rows = con.execute(
                f"""
                SELECT [{venue_col}], MAX([{mapped_col}]), COUNT(DISTINCT [{race_col}])
                FROM [{table}]
                GROUP BY [{venue_col}]
                """
            ).fetchall()
            by_venue = []
            n_races = 0
            n_mapped = 0
            for venue, mapped, race_n in rows:
                venue_s = str(venue or "unknown")
                is_mapped = str(mapped).strip().lower() in ("1", "true", "yes", "on")
                rn = int(race_n or 0)
                n_races += rn
                if is_mapped:
                    n_mapped += rn
                by_venue.append(
                    {"venue_id": venue_s, "mapped": is_mapped, "n_races": rn}
                )
        else:
            rows = con.execute(f"SELECT [{venue_col}] FROM [{table}]").fetchall()
            c = Counter(str(r[0] or "unknown") for r in rows)
            by_venue = [{"venue_id": k, "mapped": None, "n_runners": v} for k, v in c.most_common()]
            n_races = len(rows)
            n_mapped = n_races

        mapped_pct = round(n_mapped / n_races, 4) if n_races else None
        failing = [v for v in by_venue if v.get("mapped") is False]
        gates = institutional_gates(
            n_events=n_races,
            model_brier=0.0,
            market_brier=0.0,
            venue_mapped_pct=mapped_pct,
            target_kind="place",
        )
        # venue-only check — ignore brier missing reasons for display
        venue_gate_fail = mapped_pct is not None and mapped_pct < 0.95
        return {
            "ok": True,
            "source": "sqlite",
            "table": table,
            "n_races": n_races,
            "n_mapped_races": n_mapped,
            "mapped_pct": mapped_pct,
            "gate_min_mapped_pct": 0.95,
            "institutional_venue_pass": not venue_gate_fail,
            "failing_venues": failing[:50],
            "n_failing_venues": len(failing),
            "gates_venue_reasons": [r for r in gates["reasons"] if "venue" in r],
            "by_venue": by_venue[:100],
        }
    finally:
        con.close()


def venue_report_from_jsonl(jsonl_path: Path) -> dict:
    if not jsonl_path.is_file():
        return {"ok": False, "error": f"missing {jsonl_path}"}
    n = 0
    n_mapped = 0
    failing: list = []
    by_venue: dict = {}
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        n += 1
        vid = str(rec.get("venue_id") or "unknown")
        mapped = bool(rec.get("venue_mapped", True))
        by_venue.setdefault(vid, {"venue_id": vid, "mapped": mapped, "n_races": 0})
        by_venue[vid]["n_races"] += 1
        if mapped:
            n_mapped += 1
        else:
            failing.append({"venue_id": vid, "race_id": rec.get("race_id")})
    mapped_pct = round(n_mapped / n, 4) if n else None
    return {
        "ok": True,
        "source": "jsonl",
        "n_races": n,
        "n_mapped_races": n_mapped,
        "mapped_pct": mapped_pct,
        "gate_min_mapped_pct": 0.95,
        "institutional_venue_pass": mapped_pct is not None and mapped_pct >= 0.95,
        "failing_venues": failing[:50],
        "n_failing_venues": len({f["venue_id"] for f in failing}),
        "by_venue": list(by_venue.values()),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Racing venue mapping telemetry")
    ap.add_argument("--feature-store", default="")
    ap.add_argument("--jsonl", default="")
    args = ap.parse_args()

    import os

    reports = []
    fs = args.feature_store or os.environ.get("HIBS_RACING_FEATURE_STORE", "")
    if not fs:
        fs = str(Path.home() / "hibs-racing/data/feature_store.sqlite")
    if fs:
        reports.append(venue_report_from_db(Path(fs)))
    if args.jsonl:
        reports.append(venue_report_from_jsonl(Path(args.jsonl)))

    out = {"reports": reports}
    print(json.dumps(out, indent=2))
    if any(r.get("ok") and not r.get("institutional_venue_pass") for r in reports):
        raise SystemExit(2)
    if not any(r.get("ok") for r in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
