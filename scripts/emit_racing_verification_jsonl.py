#!/usr/bin/env python3
"""Emit settled-race JSONL from hibs-racing feature_store (LightGBM score column).

Wires the ranker output stored in SQLite into the institutional verification schema.
Run after ``fetch-cards --score`` / daily refresh settles results.

Usage:
  python scripts/emit_racing_verification_jsonl.py \\
    --feature-store /opt/hibs-racing/data/feature_store.sqlite \\
    --output data/verification/settled_races.jsonl

Then verify the rolling window:
  python scripts/verify_racing_window.py --input data/verification/settled_races.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from metrics.racing_sqlite import emit_jsonl_from_db, extract_settled_races_from_db
from metrics.racing import evaluate_racing_window, racing_record_from_dict


def _resolve_feature_store(arg: str) -> Path:
    p = Path(arg)
    if p.is_file():
        return p
    for cand in (
        Path(arg),
        Path.home() / "hibs-racing" / "data" / "feature_store.sqlite",
        Path("/opt/hibs-racing/data/feature_store.sqlite"),
    ):
        if cand.is_file():
            return cand
    raise SystemExit(f"feature store not found: {arg}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Emit settled race JSONL from feature_store.sqlite")
    ap.add_argument(
        "--feature-store",
        default="",
        help="path to feature_store.sqlite (default: HIBS_RACING_FEATURE_STORE or ~/hibs-racing/data/...)",
    )
    ap.add_argument("--output", default="data/verification/settled_races.jsonl")
    ap.add_argument("--target", choices=("place", "win"), default="place")
    ap.add_argument("--place-positions", type=int, default=3)
    ap.add_argument("--table", default=None, help="sqlite table override")
    ap.add_argument("--no-dedupe", action="store_true")
    ap.add_argument("--verify", action="store_true", help="run institutional window on output")
    ap.add_argument("--min-races", type=int, default=1000)
    args = ap.parse_args()

    import os

    fs_arg = (
        args.feature_store
        or os.environ.get("HIBS_RACING_FEATURE_STORE", "")
        or os.environ.get("FEATURE_STORE", "")
        or str(Path.home() / "hibs-racing/data/feature_store.sqlite")
    )
    db = _resolve_feature_store(fs_arg)

    summary = emit_jsonl_from_db(
        db,
        args.output,
        target=args.target,
        place_positions=args.place_positions,
        table=args.table,
        dedupe=not args.no_dedupe,
    )
    print(json.dumps({"feature_store": str(db), **summary}, indent=2), file=sys.stderr)

    if args.verify:
        lines = Path(args.output).read_text(encoding="utf-8").splitlines()
        races = [racing_record_from_dict(json.loads(ln)) for ln in lines if ln.strip()]
        export = evaluate_racing_window(races, min_races=args.min_races)
        print(json.dumps(export, indent=2))


if __name__ == "__main__":
    main()
