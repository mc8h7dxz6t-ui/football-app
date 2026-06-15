#!/usr/bin/env python3
"""Scan cached lines for arb and optionally execute on Matchbook (dry-run default).

Usage:
  python arb_worker.py --fixtures "Arsenal v Chelsea:12345:67890"

Live trading (REAL MONEY — small stakes only):
  export MATCHBOOK_AUTO_TRADE=1
  export MATCHBOOK_CONFIRM_LIVE=YES
  export MATCHBOOK_MAX_STAKE=2.00
  export MATCHBOOK_MAX_OUTLAY=6.00
  python arb_worker.py --fixtures "..."
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from engine.arb import scan_arbitrage
from execution.matchbook_executor import execute_matchbook_arb
from execution.risk import RiskConfig
from pipeline.cache import get_cache
from pipeline.ingest import ingest_fixture
from feeds.registry import build_default_registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("arb_worker")


def _parse_fixtures(spec: str):
    keys, contexts = [], {}
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
            ctx["home_team"], ctx["away_team"] = h.strip(), a.strip()
        contexts[fk] = ctx
    return keys, contexts


def main() -> None:
    ap = argparse.ArgumentParser(description="Matchbook arb scanner + executor")
    ap.add_argument("--fixtures", default=os.environ.get("WATCHLIST_FIXTURES", ""))
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--max-cycles", type=int, default=None)
    ap.add_argument("--execute", action="store_true", help="attempt execution (still respects dry-run gates)")
    args = ap.parse_args()

    if not args.fixtures.strip():
        sys.exit("no fixtures — pass --fixtures or set WATCHLIST_FIXTURES")

    keys, contexts = _parse_fixtures(args.fixtures)
    if not keys:
        sys.exit("no fixtures parsed from --fixtures / WATCHLIST_FIXTURES")

    risk = RiskConfig()
    registry = build_default_registry()
    cache = get_cache()
    cycles = 0

    log.info("arb worker start live=%s risk=%s", risk.live_enabled(), risk.status())

    while args.max_cycles is None or cycles < args.max_cycles:
        for fk in keys:
            ingest_fixture(registry, fk, cache=cache, context=contexts[fk])
            ticks = cache.get_peak_ticks(fk)
            opps = scan_arbitrage(ticks, fixture_key=fk, min_profit_pct=risk.min_profit_pct)
            for opp in opps:
                shadow = os.environ.get("ARB_SHADOW_LOG", "").strip().lower() in ("1", "true", "yes", "on")
                prefix = "SHADOW ARB" if shadow else "ARB"
                log.info("%s %s %.2f%% %s", prefix, opp.kind, opp.profit_pct, opp.notes)
                if args.execute or risk.auto_trade:
                    result = execute_matchbook_arb(opp, risk=risk)
                    if result.error:
                        log.warning("exec: %s", result.error)
                    elif result.dry_run:
                        log.info("dry-run offers: %s", result.offers_sent)
                    else:
                        log.info("LIVE placed: %s", result.api_response)
        cycles += 1
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
