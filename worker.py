#!/usr/bin/env python3
"""Background ingest worker — polls feeds into Redis/memory cache.

Usage:
  python worker.py --fixtures "arsenal-v-chelsea:12345:67890" --interval 5

Fixture spec (comma-separated):
  fixture_key:api_football_fixture_id:matchbook_event_id
"""

from __future__ import annotations

import argparse
import logging
import sys

from pipeline.ingest import run_ingest_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _parse_fixtures(spec: str):
    keys = []
    contexts = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":")
        fk = bits[0]
        keys.append(fk)
        ctx = {"event_label": fk}
        if len(bits) > 1 and bits[1]:
            ctx["fixture_id"] = int(bits[1])
        if len(bits) > 2 and bits[2]:
            ctx["matchbook_event_id"] = int(bits[2])
        if " v " in fk:
            h, a = fk.split(" v ", 1)
            ctx["home_team"] = h.strip()
            ctx["away_team"] = a.strip()
        contexts[fk] = ctx
    return keys, contexts


def main() -> None:
    ap = argparse.ArgumentParser(description="FVE ingest worker")
    ap.add_argument("--fixtures", required=True, help="fixture_key:fixture_id:matchbook_event_id,...")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--max-cycles", type=int, default=None)
    args = ap.parse_args()
    keys, contexts = _parse_fixtures(args.fixtures)
    if not keys:
        print("No fixtures", file=sys.stderr)
        sys.exit(1)
    run_ingest_loop(keys, interval_sec=args.interval, contexts=contexts, max_cycles=args.max_cycles)


if __name__ == "__main__":
    main()
