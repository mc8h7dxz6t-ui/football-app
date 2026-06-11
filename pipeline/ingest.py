"""Background ingest: poll feeds → dedupe → cache → optional DB persist."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from engine.devig import devig_1x2
from feeds.registry import FeedRegistry, build_default_registry
from odds_shopping import shop_lines
from pipeline.cache import LineCache, get_cache
from pipeline.circuit_breaker import breakers
from pipeline.tick import PriceTick

log = logging.getLogger(__name__)

INGEST_INTERVAL_SEC = float(os.environ.get("INGEST_INTERVAL_SEC", "5"))


def ticks_to_shopped(ticks: List[PriceTick]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    from odds_shopping import OddsOffer

    offers = [
        OddsOffer(
            market=t.market,
            odds=t.odds,
            bookmaker=t.bookmaker,
            source=t.source,
            category=t.category,
            bet_url=str(t.meta.get("bet_url", "")),
            event_label=t.fixture_key,
            selection_label=t.selection,
        )
        for t in ticks
    ]
    return shop_lines(offers)


def build_fixture_1x2_sharp_line(shopped: Dict[str, Dict[str, Dict[str, Any]]]) -> Optional[Dict[str, float]]:
    """Build 1X2 synthetic zero-vig line from best exchange/sharp per leg, Shin de-vig."""
    combined: Dict[str, float] = {}
    for leg in ("Home", "Draw", "Away"):
        best = 0.0
        for ch in ("exchange", "sharp", "all"):
            o = float(shopped.get(leg, {}).get(ch, {}).get("odds") or 0)
            best = max(best, o)
        if best > 1.0:
            combined[leg] = best
    if len(combined) < 3:
        return None
    return devig_1x2(combined["Home"], combined["Draw"], combined["Away"], method="shin")


def ingest_fixture(
    registry: FeedRegistry,
    fixture_key: str,
    *,
    cache: Optional[LineCache] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pull all feeds for one fixture, cache ticks, return shopped + sharp fair."""
    cache = cache or get_cache()
    ctx = context or {}
    all_ticks: List[PriceTick] = []

    for feed in registry.enabled():
        br = breakers.get(feed.name)
        if not br.allow_call():
            log.warning("circuit open for %s — skipping", feed.name)
            continue
        br.call_started()
        try:
            ticks = feed.fetch_ticks(fixture_key, ctx)
            br.record_success()
            all_ticks.extend(ticks)
        except Exception as exc:
            br.record_failure(str(exc))
            log.exception("feed %s failed: %s", feed.name, exc)

    cached = cache.get_ticks(fixture_key) if not all_ticks else []
    if not all_ticks and cached:
        all_ticks = cached
        stale = True
    else:
        stale = False
        if all_ticks:
            cache.put_ticks(fixture_key, all_ticks, source=",".join({t.source for t in all_ticks}))

    shopped = ticks_to_shopped(all_ticks)
    sharp_fair = build_fixture_1x2_sharp_line(shopped)

    result = {
        "fixture_key": fixture_key,
        "stale": stale,
        "tick_count": len(all_ticks),
        "shopped": shopped,
        "sharp_fair_probs": sharp_fair,
        "cache_backend": cache.backend,
        "breakers": breakers.all_status(),
    }

    try:
        from db.store import persist_snapshot

        persist_snapshot(fixture_key, result)
    except Exception:
        pass

    return result


def run_ingest_loop(
    fixture_keys: List[str],
    *,
    interval_sec: float = INGEST_INTERVAL_SEC,
    contexts: Optional[Dict[str, Dict[str, Any]]] = None,
    max_cycles: Optional[int] = None,
) -> None:
    """Blocking poll loop for worker process."""
    registry = build_default_registry()
    contexts = contexts or {}
    cycles = 0
    log.info("ingest loop started interval=%.1fs fixtures=%d", interval_sec, len(fixture_keys))
    while max_cycles is None or cycles < max_cycles:
        for fk in fixture_keys:
            ingest_fixture(registry, fk, context=contexts.get(fk, {}))
        cycles += 1
        time.sleep(interval_sec)
