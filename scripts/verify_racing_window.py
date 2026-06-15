#!/usr/bin/env python3
"""Verify a rolling racing window (win or place) — institutional data room export.

Input JSONL (one race per line):
  {"race_id":"r1","target":"place","place_positions":3,"venue_id":"aintree",
   "venue_mapped":true,
   "runners":[{"runner_id":"h1","model_prob":0.3,"market_prob":0.28,"won":false,"placed":true}, ...]}

Usage:
  python scripts/verify_racing_window.py --input races.jsonl --output data_room_racing.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from metrics.racing import evaluate_racing_window, racing_record_from_dict


def load_races(path: Path):
    races = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        races.append(racing_record_from_dict(json.loads(line)))
    return races


def main() -> None:
    ap = argparse.ArgumentParser(description="Racing institutional verification window")
    ap.add_argument("--input", required=True, help="JSONL race records")
    ap.add_argument("--output", default="-")
    ap.add_argument("--min-races", type=int, default=1000)
    ap.add_argument("--train-cutoff", default=None)
    ap.add_argument("--in-sample", action="store_true")
    args = ap.parse_args()

    races = load_races(Path(args.input))
    export = evaluate_racing_window(
        races,
        min_races=args.min_races,
        oos_only=not args.in_sample,
        oos_declared=not args.in_sample,
        train_cutoff=args.train_cutoff,
    )
    text = json.dumps(export, indent=2)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output} gates={export.get('gates', {})}", file=sys.stderr)


if __name__ == "__main__":
    main()
