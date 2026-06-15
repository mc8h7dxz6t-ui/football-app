"""Async tiered scheduler — 250ms loop, thread-safe in-flight task guard."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set

from feeds.base import FeedAdapter
from feeds.registry import FeedRegistry, build_default_registry
from pipeline.ingest import SCHEDULER_TICK_SEC, ingest_feed

log = logging.getLogger(__name__)


class AsyncSchedulerGuard:
    """Thread-safe lifecycle wrapper for blocking feed polls off the event loop."""

    def __init__(self) -> None:
        self._in_flight: Set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def in_flight(self) -> Set[str]:
        return set(self._in_flight)

    async def execute_safely(
        self,
        task_id: str,
        blocking_func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        """Run blocking work in a thread pool; skip if task_id already running."""
        async with self._lock:
            if task_id in self._in_flight:
                log.debug("Task %s already running — skipping cycle", task_id)
                return False
            self._in_flight.add(task_id)

        try:
            await asyncio.to_thread(blocking_func, *args, **kwargs)
            return True
        except Exception as exc:
            log.error("Error executing background task %s: %s", task_id, exc)
            return False
        finally:
            async with self._lock:
                self._in_flight.discard(task_id)


def task_id_for_feed(feed_name: str, fixture_key: str) -> str:
    return f"feed:{feed_name}:{fixture_key}"


class AsyncTieredScheduler:
    """Schedule feed polls without blocking the core 250ms scheduler tick."""

    def __init__(
        self,
        fixture_keys: List[str],
        contexts: Dict[str, Dict[str, Any]],
        registry: Optional[FeedRegistry] = None,
        *,
        tick_sec: float = SCHEDULER_TICK_SEC,
        guard: Optional[AsyncSchedulerGuard] = None,
    ) -> None:
        self.fixture_keys = fixture_keys
        self.contexts = contexts
        self.registry = registry or build_default_registry()
        self.feeds = self.registry.enabled()
        self.tick_sec = tick_sec
        self.guard = guard or AsyncSchedulerGuard()
        self._next_run: Dict[tuple[str, str], float] = {}
        self._cycles = 0

    async def _poll_feed(self, feed: FeedAdapter, fixture_key: str, ctx: Dict[str, Any]) -> None:
        tid = task_id_for_feed(feed.name, fixture_key)
        await self.guard.execute_safely(tid, ingest_feed, feed, fixture_key, context=ctx)

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
