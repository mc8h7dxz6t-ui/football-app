"""Background ingest: tiered feed polling → history → cache → DB."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from engine.devig import devig_1x2
from feeds.base import FeedAdapter
from feeds.registry import FeedRegistry, build_default_registry
from odds_shopping import shop_lines
from pipeline.cache import LineCache, get_cache
from pipeline.circuit_breaker import breakers
from pipeline.tick import PriceTick

log = logging.getLogger(__name__)

INGEST_INTERVAL_SEC = float(os.environ.get("INGEST_INTERVAL_SEC", "5"))
SCHEDULER_TICK_SEC = float(os.environ.get("SCHEDULER_TICK_SEC", "0.25"))
USE_PEAK_WINDOW = os.environ.get("USE_PEAK_ODDS_WINDOW", "1") not in ("0", "false", "False")


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


def _fetch_feed(
    feed: FeedAdapter,
    fixture_key: str,
    ctx: Dict[str, Any],
) -> List[PriceTick]:
    br = breakers.get(feed.name)
    if not br.allow_call():
        log.warning("circuit open for %s — skipping", feed.name)
        return []
    br.call_started()
    try:
        ticks = feed.fetch_ticks(fixture_key, ctx)
        br.record_success()
        return ticks
    except Exception as exc:
        br.record_failure(str(exc))
        log.exception("feed %s failed: %s", feed.name, exc)
        return []


def ingest_feed(
    feed: FeedAdapter,
    fixture_key: str,
    *,
    cache: Optional[LineCache] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Poll a single feed and merge into cache + history."""
    cache = cache or get_cache()
    ctx = context or {}
    ticks = _fetch_feed(feed, fixture_key, ctx)
    merge_stats = {"appended": 0, "snapshot_count": 0, "changed": 0}
    if ticks:
        merge_stats = cache.merge_ticks(
            fixture_key,
            ticks,
            source=feed.name,
            feed_name=feed.name,
        )
    return {
        "feed": feed.name,
        "fixture_key": fixture_key,
        "fetched": len(ticks),
        **merge_stats,
        "poll_interval_sec": feed.poll_interval_sec,
    }


def build_line_view(cache: LineCache, fixture_key: str) -> Dict[str, Any]:
    """Snapshot + optional peak-window view for line shopping."""
    snapshot = cache.get_ticks(fixture_key)
    peak = cache.get_peak_ticks(fixture_key) if USE_PEAK_WINDOW else snapshot
    active = peak if peak else snapshot
    shopped = ticks_to_shopped(active)
    history = cache.get_tick_history(fixture_key, since=time.time() - cache.peak_window_sec)
    return {
        "fixture_key": fixture_key,
        "tick_count": len(active),
        "snapshot_count": len(snapshot),
        "intra_window_moves": len(history),
        "use_peak_window": USE_PEAK_WINDOW,
        "peak_window_sec": cache.peak_window_sec,
        "shopped": shopped,
        "sharp_fair_probs": build_fixture_1x2_sharp_line(shopped),
        "meta": cache.get_meta(fixture_key),
        "cache_backend": cache.backend,
        "breakers": breakers.all_status(),
    }


def ingest_fixture(
    registry: FeedRegistry,
    fixture_key: str,
    *,
    cache: Optional[LineCache] = None,
    context: Optional[Dict[str, Any]] = None,
    feeds: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Pull selected feeds (or all enabled) for one fixture."""
    cache = cache or get_cache()
    ctx = context or {}
    enabled = registry.enabled()
    if feeds:
        enabled = [f for f in enabled if f.name in feeds]

    feed_results = []
    any_fetched = False
    for feed in enabled:
        r = ingest_feed(feed, fixture_key, cache=cache, context=ctx)
        feed_results.append(r)
        if r.get("fetched", 0) > 0:
            any_fetched = True

    stale = not any_fetched and bool(cache.get_ticks(fixture_key))
    view = build_line_view(cache, fixture_key)
    result = {**view, "stale": stale, "feed_results": feed_results}

    try:
        from db.store import persist_snapshot

        persist_snapshot(fixture_key, result)
    except Exception:
        pass

    return result


def run_tiered_ingest_loop(
    fixture_keys: List[str],
    *,
    contexts: Optional[Dict[str, Dict[str, Any]]] = None,
    max_cycles: Optional[int] = None,
) -> None:
    """Per-feed poll cadence: exchange ~1s, sharp ~2s, soft ~5s (configurable)."""
    registry = build_default_registry()
    feeds = registry.enabled()
    contexts = contexts or {}
    last_poll: Dict[tuple[str, str], float] = {}
    cycles = 0
    intervals = {f.name: f.poll_interval_sec for f in feeds}
    log.info(
        "tiered ingest started fixtures=%d feeds=%s scheduler=%.2fs",
        len(fixture_keys),
        intervals,
        SCHEDULER_TICK_SEC,
    )
    while max_cycles is None or cycles < max_cycles:
        now = time.time()
        polled_any = False
        for fk in fixture_keys:
            ctx = contexts.get(fk, {})
            for feed in feeds:
                key = (fk, feed.name)
                due = now - last_poll.get(key, 0.0) >= feed.poll_interval_sec
                if not due:
                    continue
                ingest_feed(feed, fk, context=ctx)
                last_poll[key] = now
                polled_any = True
        if polled_any:
            cycles += 1
        time.sleep(SCHEDULER_TICK_SEC)


def run_ingest_loop(
    fixture_keys: List[str],
    *,
    interval_sec: float = INGEST_INTERVAL_SEC,
    contexts: Optional[Dict[str, Dict[str, Any]]] = None,
    max_cycles: Optional[int] = None,
    tiered: bool = True,
) -> None:
    """Blocking poll loop. Default: tiered per-feed scheduling."""
    if tiered:
        run_tiered_ingest_loop(fixture_keys, contexts=contexts, max_cycles=max_cycles)
        return
    registry = build_default_registry()
    contexts = contexts or {}
    cycles = 0
    log.info("uniform ingest loop interval=%.1fs fixtures=%d", interval_sec, len(fixture_keys))
    while max_cycles is None or cycles < max_cycles:
        for fk in fixture_keys:
            ingest_fixture(registry, fk, context=contexts.get(fk, {}))
        cycles += 1
        time.sleep(interval_sec)
