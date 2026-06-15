#!/usr/bin/env python3
"""Export institutional data room JSON for football or racing verification.

Football (records JSON from run_backtest or custom):
  python scripts/export_data_room.py --product football --records path/to/records.json

Racing (JSONL — one race per line, see docs/INSTITUTIONAL_VERIFICATION.md):
  python scripts/verify_racing_window.py --input path/to/races.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest as bt


def main() -> None:
    ap = argparse.ArgumentParser(description="Export football institutional data room JSON")
    ap.add_argument("--records", required=True, help="JSON file: list of prediction records")
    ap.add_argument("--output", default="-", help="output path or - for stdout")
    ap.add_argument("--min-events", type=int, default=1000)
    ap.add_argument("--train-cutoff", default=None, help="OOS train cutoff ISO date")
    ap.add_argument("--in-sample", action="store_true", help="mark window as not OOS (gates fail)")
    args = ap.parse_args()

    raw = json.loads(Path(args.records).read_text(encoding="utf-8"))
    records = raw if isinstance(raw, list) else raw.get("records", [])
    export = bt.export_data_room(
        records,
        min_events=args.min_events,
        oos_only=not args.in_sample,
        oos_declared=not args.in_sample,
        train_cutoff=args.train_cutoff,
        extra={"source": str(args.records)},
    )
    text = json.dumps(export, indent=2)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
