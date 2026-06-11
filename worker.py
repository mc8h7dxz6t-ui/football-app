#!/usr/bin/env python3
"""Background ingest worker — tiered per-feed polling into Redis + tick history.

Exchange feeds (Matchbook) default to 1s; soft aggregators (API-Football) to 5s.
Intra-window line moves are appended to a ring buffer; peak odds in the last
PEAK_ODDS_WINDOW_SEC seconds are used for line shopping.

Usage:
  python worker.py --fixtures "arsenal-v-chelsea:12345:67890"

  # legacy uniform 5s poll (all feeds together):
  python worker.py --fixtures "..." --uniform-interval 5

Fixture spec (comma-separated):
  fixture_key:api_football_fixture_id:matchbook_event_id

Env:
  FEED_POLL_SEC_MATCHBOOK=0.5   # faster exchange poll
  PEAK_ODDS_WINDOW_SEC=5
  TICK_HISTORY_MAX=2000
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
    ap = argparse.ArgumentParser(description="FVE tiered ingest worker")
    ap.add_argument("--fixtures", required=True, help="fixture_key:fixture_id:matchbook_event_id,...")
    ap.add_argument(
        "--uniform-interval",
        type=float,
        default=None,
        help="If set, poll ALL feeds on this fixed interval (legacy mode)",
    )
    ap.add_argument("--max-cycles", type=int, default=None)
    args = ap.parse_args()
    keys, contexts = _parse_fixtures(args.fixtures)
    if not keys:
        print("No fixtures", file=sys.stderr)
        sys.exit(1)
    tiered = args.uniform_interval is None
    run_ingest_loop(
        keys,
        interval_sec=args.uniform_interval or 5.0,
        contexts=contexts,
        max_cycles=args.max_cycles,
        tiered=tiered,
    )


if __name__ == "__main__":
    main()
