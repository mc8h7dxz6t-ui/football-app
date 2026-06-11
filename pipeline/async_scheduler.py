"""Async tiered scheduler — 250ms loop, non-blocking feed polls."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Set

from feeds.base import FeedAdapter
from feeds.registry import FeedRegistry, build_default_registry
from pipeline.ingest import SCHEDULER_TICK_SEC, ingest_feed

log = logging.getLogger(__name__)


class AsyncTieredScheduler:
    """Schedule feed polls without blocking the core scheduler tick."""

    def __init__(
        self,
        fixture_keys: List[str],
        contexts: Dict[str, Dict[str, Any]],
        registry: Optional[FeedRegistry] = None,
        *,
        tick_sec: float = SCHEDULER_TICK_SEC,
        skip_if_inflight: bool = True,
    ) -> None:
        self.fixture_keys = fixture_keys
        self.contexts = contexts
        self.registry = registry or build_default_registry()
        self.feeds = self.registry.enabled()
        self.tick_sec = tick_sec
        self.skip_if_inflight = skip_if_inflight
        self._next_run: Dict[tuple[str, str], float] = {}
        self._inflight: Set[str] = set()
        self._cycles = 0

    def _task_key(self, fixture_key: str, feed_name: str) -> str:
        return f"{fixture_key}:{feed_name}"

    async def _poll_feed(self, feed: FeedAdapter, fixture_key: str, ctx: Dict[str, Any]) -> None:
        key = self._task_key(fixture_key, feed.name)
        if self.skip_if_inflight and key in self._inflight:
            log.debug("skip inflight %s", key)
            return
        self._inflight.add(key)
        try:
            await asyncio.to_thread(ingest_feed, feed, fixture_key, context=ctx)
        except Exception:
            log.exception("async ingest failed %s", key)
        finally:
            self._inflight.discard(key)

    async def start_loop(self, max_cycles: Optional[int] = None) -> None:
        intervals = {f.name: f.poll_interval_sec for f in self.feeds}
        log.info(
            "async tiered scheduler fixtures=%d feeds=%s tick=%.2fs",
            len(self.fixture_keys),
            intervals,
            self.tick_sec,
        )
        while max_cycles is None or self._cycles < max_cycles:
            now = time.time()
            scheduled = False
            for fk in self.fixture_keys:
                ctx = self.contexts.get(fk, {})
                for feed in self.feeds:
                    run_key = (fk, feed.name)
                    if now < self._next_run.get(run_key, 0.0):
                        continue
                    asyncio.create_task(self._poll_feed(feed, fk, ctx))
                    self._next_run[run_key] = now + feed.poll_interval_sec
                    scheduled = True
            if scheduled:
                self._cycles += 1
            await asyncio.sleep(self.tick_sec)


async def run_async_tiered_loop(
    fixture_keys: List[str],
    *,
    contexts: Optional[Dict[str, Dict[str, Any]]] = None,
    max_cycles: Optional[int] = None,
) -> None:
    scheduler = AsyncTieredScheduler(fixture_keys, contexts or {})
    await scheduler.start_loop(max_cycles=max_cycles)


def run_async_tiered_ingest_loop(
    fixture_keys: List[str],
    *,
    contexts: Optional[Dict[str, Dict[str, Any]]] = None,
    max_cycles: Optional[int] = None,
) -> None:
    """Sync entrypoint for worker CLI."""
    asyncio.run(run_async_tiered_loop(fixture_keys, contexts=contexts, max_cycles=max_cycles))
