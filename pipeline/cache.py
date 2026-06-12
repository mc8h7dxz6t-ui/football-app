"""Redis ZSET tick rings + snapshot cache."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from pipeline.redis_tick_ring import MemoryTickRing, RedisTickRing, TickRing
from pipeline.tick import PriceTick
from pipeline.tick_history import merge_snapshot, peak_ticks_in_window

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_TTL_SEC = int(os.environ.get("LINE_CACHE_TTL_SEC", "120"))
_PEAK_WINDOW_SEC = float(os.environ.get("PEAK_ODDS_WINDOW_SEC", "5"))


class LineCache:
    """Fixture snapshot + per-market rolling ZSET rings for peak-window odds."""

    def __init__(self, redis_url: str = _REDIS_URL, ttl_sec: int = _TTL_SEC) -> None:
        self.ttl_sec = ttl_sec
        self.peak_window_sec = _PEAK_WINDOW_SEC
        self._memory: Dict[str, Dict[str, Any]] = {}
        self._rings_mem: Dict[str, TickRing] = {}
        self._redis = None
        self._redis_ok = False
        try:
            import redis

            self._redis = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=1)
            self._redis.ping()
            self._redis_ok = True
        except Exception:
            self._redis = None
            self._redis_ok = False

    @property
    def backend(self) -> str:
        return "redis-zset" if self._redis_ok else "memory-zset"

    def _key_ticks(self, fixture_key: str) -> str:
        return f"fve:ticks:{fixture_key}"

    def _key_meta(self, fixture_key: str) -> str:
        return f"fve:meta:{fixture_key}"

    def _key_sports(self, fixture_key: str) -> str:
        return f"fve:sports:{fixture_key}"

    def _ring(self, fixture_key: str, market: str) -> TickRing:
        rk = f"{fixture_key}:{market}"
        if self._redis_ok and self._redis:
            return RedisTickRing(
                self._redis,
                fixture_key,
                market,
                window_sec=self.peak_window_sec,
                ttl_sec=self.ttl_sec,
            )
        if rk not in self._rings_mem:
            self._rings_mem[rk] = MemoryTickRing(fixture_key, market, window_sec=self.peak_window_sec)
        return self._rings_mem[rk]

    def merge_ticks(
        self,
        fixture_key: str,
        incoming: List[PriceTick],
        *,
        source: str = "",
        feed_name: str = "",
    ) -> Dict[str, Any]:
        if not incoming:
            return {"appended": 0, "snapshot_count": len(self.get_ticks(fixture_key))}

        existing = self.get_ticks(fixture_key)
        snapshot, changed = merge_snapshot(existing, incoming)
        appended = 0
        for tick in changed:
            if tick.market in ("Home", "Draw", "Away", "Over2.5", "BTTS"):
                self._ring(fixture_key, tick.market).append_tick(tick)
                appended += 1

        meta = {
            "updated_at": time.time(),
            "source": source,
            "last_feed": feed_name,
            "snapshot_count": len(snapshot),
            "history_appended": appended,
            "peak_window_sec": self.peak_window_sec,
            "history_backend": self.backend,
        }
        payload = [t.to_dict() for t in snapshot]
        if self._redis_ok and self._redis:
            pipe = self._redis.pipeline()
            pipe.setex(self._key_ticks(fixture_key), self.ttl_sec, json.dumps(payload))
            pipe.setex(self._key_meta(fixture_key), self.ttl_sec, json.dumps(meta))
            pipe.execute()
        else:
            self._memory[self._key_ticks(fixture_key)] = {
                "data": payload,
                "expires": time.time() + self.ttl_sec,
            }
            self._memory[self._key_meta(fixture_key)] = {
                "data": meta,
                "expires": time.time() + self.ttl_sec,
            }
        return {"appended": appended, "snapshot_count": len(snapshot), "changed": len(changed)}

    def put_ticks(self, fixture_key: str, ticks: List[PriceTick], *, source: str = "") -> int:
        return self.merge_ticks(fixture_key, ticks, source=source)["snapshot_count"]

    def get_ticks(self, fixture_key: str) -> List[PriceTick]:
        raw = self._get_json(self._key_ticks(fixture_key))
        if not raw:
            return []
        return [PriceTick.from_dict(d) for d in raw if isinstance(d, dict)]

    def get_tick_history(self, fixture_key: str, *, since: Optional[float] = None) -> List[PriceTick]:
        """All ticks across market rings within window (or since timestamp)."""
        now = time.time()
        since_ts = since if since is not None else now - self.peak_window_sec
        markets = ("Home", "Draw", "Away", "Over2.5", "BTTS")
        out: List[PriceTick] = []
        for m in markets:
            ring = self._ring(fixture_key, m)
            out.extend([t for t in ring.ticks_in_window(now=now) if t.received_at >= since_ts])
        out.sort(key=lambda t: t.received_at)
        return out

    def get_peak_ticks(self, fixture_key: str, window_sec: Optional[float] = None) -> List[PriceTick]:
        w = window_sec if window_sec is not None else self.peak_window_sec
        now = time.time()
        markets = ("Home", "Draw", "Away", "Over2.5", "BTTS")
        peaks: List[PriceTick] = []
        for m in markets:
            ring = self._ring(fixture_key, m)
            if window_sec is not None and window_sec != ring.window_sec:
                ticks = ring.ticks_in_window(now=now)
                peaks.extend(peak_ticks_in_window(ticks, w, now=now))
            else:
                peaks.extend(ring.peak_ticks(now=now))
        if peaks:
            return peaks
        return self.get_ticks(fixture_key)

    def get_peak_odds(self, fixture_key: str, market: str, selection: str = "") -> float:
        sel = selection or market
        return self._ring(fixture_key, market).get_peak_odds(sel)

    def get_meta(self, fixture_key: str) -> Dict[str, Any]:
        return self._get_json(self._key_meta(fixture_key)) or {}

    def put_sports(self, fixture_key: str, sports: Dict[str, Any], *, ttl_sec: Optional[int] = None) -> None:
        ttl = int(ttl_sec or sports.get("ttl_sec") or int(os.environ.get("SPORTS_CACHE_TTL_SEC", "3600")))
        if self._redis_ok and self._redis:
            self._redis.setex(self._key_sports(fixture_key), ttl, json.dumps(sports, default=str))
        else:
            self._memory[self._key_sports(fixture_key)] = {
                "data": sports,
                "expires": time.time() + ttl,
            }

    def get_sports(self, fixture_key: str) -> Optional[Dict[str, Any]]:
        data = self._get_json(self._key_sports(fixture_key))
        return data if isinstance(data, dict) else None

    def _get_json(self, key: str) -> Any:
        if self._redis_ok and self._redis:
            val = self._redis.get(key)
            return json.loads(val) if val else None
        entry = self._memory.get(key)
        if not entry or entry.get("expires", 0) < time.time():
            return None
        return entry.get("data")

    def list_fixture_keys(self, pattern: str = "fve:ticks:*") -> List[str]:
        if self._redis_ok and self._redis:
            keys = self._redis.keys(pattern.replace("*", "*"))
            return [k.replace("fve:ticks:", "") for k in keys]
        return [k.replace("fve:ticks:", "") for k in self._memory if k.startswith("fve:ticks:")]


_cache: Optional[LineCache] = None


def get_cache() -> LineCache:
    global _cache
    if _cache is None:
        _cache = LineCache()
    return _cache
