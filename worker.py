#!/usr/bin/env python3
"""Background ingest worker — tiered per-feed polling into Redis + tick history.

Exchange feeds (Matchbook) default to 1s; soft aggregators (API-Football) to 5s.
Intra-window line moves are appended to a ring buffer; peak odds in the last
PEAK_ODDS_WINDOW_SEC seconds are used for line shopping.

Usage:
  python worker.py --fixtures "arsenal-v-chelsea:12345:67890"

  # legacy uniform 5s poll (all feeds together):
  python worker.py --fixtures "..." --sync          # blocking tiered loop
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
import os
import sys

from pipeline.ingest import run_ingest_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _paused() -> bool:
    v = os.environ.get("FVE_PAUSED", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def main() -> None:
    if _paused():
        print(
            "FVE worker paused (FVE_PAUSED=1). hibs-bet owns live APIs — see docs/PAUSED.md",
            file=sys.stderr,
        )
        sys.exit(0)
    ap = argparse.ArgumentParser(description="FVE tiered ingest worker")
    ap.add_argument(
        "--auto",
        action="store_true",
        help="auto-discover upcoming fixtures (API-Football); refreshes hourly",
    )
    ap.add_argument(
        "--fixtures",
        default="",
        help="fixture_key:fixture_id:matchbook_event_id,... (optional with --auto)",
    )
    ap.add_argument(
        "--uniform-interval",
        type=float,
        default=None,
        help="If set, poll ALL feeds on this fixed interval (legacy mode)",
    )
    ap.add_argument("--max-cycles", type=int, default=None)
    ap.add_argument(
        "--sync",
        action="store_true",
        help="use blocking sync scheduler instead of async 250ms loop",
    )
    args = ap.parse_args()
    if args.auto or os.environ.get("FVE_AUTO_WATCHLIST", "").strip().lower() in ("1", "true", "yes"):
        from pipeline.auto_ingest import run_auto_ingest

        run_auto_ingest(max_cycles=args.max_cycles)
        return
    from pipeline.watchlist import parse_fixture_spec

    spec = args.fixtures or os.environ.get("WATCHLIST_FIXTURES", "")
    keys, contexts = parse_fixture_spec(spec)
    if not keys:
        print("No fixtures — use --auto or --fixtures / WATCHLIST_FIXTURES", file=sys.stderr)
        sys.exit(1)
    tiered = args.uniform_interval is None
    run_ingest_loop(
        keys,
        interval_sec=args.uniform_interval or 5.0,
        contexts=contexts,
        max_cycles=args.max_cycles,
        tiered=tiered,
        async_mode=not args.sync,
    )


if __name__ == "__main__":
    main()
