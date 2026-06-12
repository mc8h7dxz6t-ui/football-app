"""Hands-off ingest: auto watchlist refresh + tiered async polling."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from feeds.registry import build_default_registry
from pipeline.async_scheduler import AsyncSchedulerGuard, AsyncTieredScheduler
from pipeline.watchlist import WatchlistState

log = logging.getLogger(__name__)

REFRESH_SEC = float(os.environ.get("FVE_WATCHLIST_REFRESH_SEC", "3600"))
HEARTBEAT_PATH = os.environ.get("FVE_WORKER_HEARTBEAT", "/tmp/fve_worker_heartbeat")


async def _refresh_loop(state: WatchlistState, refresh_sec: float) -> None:
    while True:
        try:
            n = await asyncio.to_thread(state.refresh)
            state.touch_heartbeat(HEARTBEAT_PATH)
            log.info("watchlist refresh complete fixtures=%d", n)
        except Exception:
            log.exception("watchlist refresh failed")
        await asyncio.sleep(refresh_sec)


async def run_auto_ingest_loop(*, max_cycles: Optional[int] = None) -> None:
    state = WatchlistState()
    n = state.refresh()
    state.touch_heartbeat(HEARTBEAT_PATH)
    if n == 0:
        log.warning(
            "watchlist empty — check API_SPORTS_KEY and upcoming fixtures; will retry every %.0fs",
            REFRESH_SEC,
        )

    registry = build_default_registry()
    scheduler = AsyncTieredScheduler([], {}, registry=registry)
    guard = scheduler.guard
    feeds = scheduler.feeds
    tick_sec = scheduler.tick_sec
    refresh_task = asyncio.create_task(_refresh_loop(state, REFRESH_SEC))
    cycles = 0
    _next_run: dict[tuple[str, str], float] = {}

    try:
        while max_cycles is None or cycles < max_cycles:
            now = time.time()
            keys, contexts = state.snapshot()
            scheduled = False
            for fk in keys:
                ctx = contexts.get(fk, {})
                for feed in feeds:
                    run_key = (fk, feed.name)
                    if now < _next_run.get(run_key, 0.0):
                        continue

                    async def _poll(f=feed, fixture_key=fk, c=ctx) -> None:
                        from pipeline.ingest import ingest_feed

                        tid = f"feed:{f.name}:{fixture_key}"
                        await guard.execute_safely(tid, ingest_feed, f, fixture_key, context=c)

                    asyncio.create_task(_poll())
                    _next_run[run_key] = now + feed.poll_interval_sec
                    scheduled = True
            if scheduled:
                cycles += 1
                state.touch_heartbeat(HEARTBEAT_PATH)
            await asyncio.sleep(tick_sec)
    finally:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass


def run_auto_ingest(*, max_cycles: Optional[int] = None) -> None:
    asyncio.run(run_auto_ingest_loop(max_cycles=max_cycles))
