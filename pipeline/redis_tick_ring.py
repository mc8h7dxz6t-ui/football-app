"""High-performance rolling tick window via Redis ZSET (score = epoch timestamp)."""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pipeline.tick import PriceTick
from pipeline.tick_history import peak_ticks_in_window, selection_key


def _ring_key(fixture_key: str, market: str) -> str:
    return f"fve:zring:{fixture_key}:{market}"


class TickRing(ABC):
    window_sec: float

    @abstractmethod
    def append_tick(self, tick: PriceTick) -> None:
        ...

    @abstractmethod
    def purge_expired(self, *, now: Optional[float] = None) -> int:
        ...

    @abstractmethod
    def ticks_in_window(self, *, now: Optional[float] = None) -> List[PriceTick]:
        ...

    def peak_ticks(self, *, now: Optional[float] = None) -> List[PriceTick]:
        now = now or time.time()
        self.purge_expired(now=now)
        ticks = self.ticks_in_window(now=now)
        return peak_ticks_in_window(ticks, self.window_sec, now=now)

    def get_peak_odds(self, selection: str, *, now: Optional[float] = None) -> float:
        now = now or time.time()
        best = 0.0
        for t in self.ticks_in_window(now=now):
            if t.selection == selection or t.market == selection:
                best = max(best, t.odds)
        return best


class RedisTickRing(TickRing):
    """Redis sorted-set ring — O(log N) append + range purge."""

    def __init__(
        self,
        r_client: Any,
        fixture_key: str,
        market: str,
        *,
        window_sec: float = 5.0,
        ttl_sec: int = 120,
    ) -> None:
        self.r = r_client
        self.fixture_key = fixture_key
        self.market = market
        self.key = _ring_key(fixture_key, market)
        self.window_sec = window_sec
        self.ttl_sec = ttl_sec

    def append_tick(self, tick: PriceTick) -> None:
        now = time.time()
        payload = tick.to_dict()
        payload["_seq"] = now
        member = json.dumps(payload, separators=(",", ":"))
        pipe = self.r.pipeline()
        pipe.zadd(self.key, {member: now})
        pipe.zremrangebyscore(self.key, "-inf", now - self.window_sec)
        pipe.expire(self.key, self.ttl_sec)
        pipe.execute()

    def purge_expired(self, *, now: Optional[float] = None) -> int:
        now = now or time.time()
        return int(self.r.zremrangebyscore(self.key, "-inf", now - self.window_sec))

    def ticks_in_window(self, *, now: Optional[float] = None) -> List[PriceTick]:
        now = now or time.time()
        self.purge_expired(now=now)
        raw = self.r.zrangebyscore(self.key, now - self.window_sec, now)
        out: List[PriceTick] = []
        for member in raw or []:
            try:
                data = json.loads(member)
                data.pop("_seq", None)
                out.append(PriceTick.from_dict(data))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return out


class MemoryTickRing(TickRing):
    """In-process ZSET analogue when Redis is unavailable."""

    def __init__(self, fixture_key: str, market: str, *, window_sec: float = 5.0) -> None:
        self.fixture_key = fixture_key
        self.market = market
        self.window_sec = window_sec
        self._entries: List[tuple[float, PriceTick]] = []

    def append_tick(self, tick: PriceTick) -> None:
        now = time.time()
        self._entries.append((now, tick))
        self.purge_expired(now=now)

    def purge_expired(self, *, now: Optional[float] = None) -> int:
        now = now or time.time()
        cutoff = now - self.window_sec
        before = len(self._entries)
        self._entries = [(ts, t) for ts, t in self._entries if ts >= cutoff]
        return before - len(self._entries)

    def ticks_in_window(self, *, now: Optional[float] = None) -> List[PriceTick]:
        now = now or time.time()
        cutoff = now - self.window_sec
        return [t for ts, t in self._entries if ts >= cutoff]
