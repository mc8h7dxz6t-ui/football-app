"""Redis in-memory line cache with tick history ring buffer."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from pipeline.tick import PriceTick
from pipeline.tick_history import merge_snapshot, peak_ticks_in_window

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_TTL_SEC = int(os.environ.get("LINE_CACHE_TTL_SEC", "120"))
_HISTORY_MAX = int(os.environ.get("TICK_HISTORY_MAX", "2000"))
_PEAK_WINDOW_SEC = float(os.environ.get("PEAK_ODDS_WINDOW_SEC", "5"))


class LineCache:
    """Fixture-level tick store + append-only history for intra-window moves."""

    def __init__(self, redis_url: str = _REDIS_URL, ttl_sec: int = _TTL_SEC) -> None:
        self.ttl_sec = ttl_sec
        self.history_max = _HISTORY_MAX
        self.peak_window_sec = _PEAK_WINDOW_SEC
        self._memory: Dict[str, Dict[str, Any]] = {}
        self._history_mem: Dict[str, List[Dict[str, Any]]] = {}
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
        return "redis" if self._redis_ok else "memory"

    def _key_ticks(self, fixture_key: str) -> str:
        return f"fve:ticks:{fixture_key}"

    def _key_history(self, fixture_key: str) -> str:
        return f"fve:history:{fixture_key}"

    def _key_meta(self, fixture_key: str) -> str:
        return f"fve:meta:{fixture_key}"

    def merge_ticks(
        self,
        fixture_key: str,
        incoming: List[PriceTick],
        *,
        source: str = "",
        feed_name: str = "",
    ) -> Dict[str, Any]:
        """Merge incoming ticks, append changes to history, update snapshot."""
        if not incoming:
            return {"appended": 0, "snapshot_count": len(self.get_ticks(fixture_key))}

        existing = self.get_ticks(fixture_key)
        snapshot, changed = merge_snapshot(existing, incoming)
        appended = self._append_history(fixture_key, changed)

        meta = {
            "updated_at": time.time(),
            "source": source,
            "last_feed": feed_name,
            "snapshot_count": len(snapshot),
            "history_appended": appended,
            "peak_window_sec": self.peak_window_sec,
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
        """Backward-compatible replace-style write (prefer merge_ticks in ingest)."""
        stats = self.merge_ticks(fixture_key, ticks, source=source)
        return stats["snapshot_count"]

    def _append_history(self, fixture_key: str, ticks: List[PriceTick]) -> int:
        if not ticks:
            return 0
        rows = [t.to_dict() for t in ticks]
        if self._redis_ok and self._redis:
            key = self._key_history(fixture_key)
            pipe = self._redis.pipeline()
            for row in rows:
                pipe.rpush(key, json.dumps(row))
            pipe.ltrim(key, -self.history_max, -1)
            pipe.expire(key, self.ttl_sec)
            pipe.execute()
        else:
            hist = self._history_mem.setdefault(fixture_key, [])
            hist.extend(rows)
            self._history_mem[fixture_key] = hist[-self.history_max :]
        return len(ticks)

    def get_ticks(self, fixture_key: str) -> List[PriceTick]:
        raw = self._get_json(self._key_ticks(fixture_key))
        if not raw:
            return []
        return [PriceTick.from_dict(d) for d in raw if isinstance(d, dict)]

    def get_tick_history(self, fixture_key: str, *, since: Optional[float] = None) -> List[PriceTick]:
        if self._redis_ok and self._redis:
            key = self._key_history(fixture_key)
            raw_rows = self._redis.lrange(key, 0, -1) or []
            rows = [json.loads(r) for r in raw_rows if r]
        else:
            rows = self._history_mem.get(fixture_key, [])
        ticks = [PriceTick.from_dict(d) for d in rows if isinstance(d, dict)]
        if since is not None:
            ticks = [t for t in ticks if t.received_at >= since]
        return ticks

    def get_peak_ticks(self, fixture_key: str, window_sec: Optional[float] = None) -> List[PriceTick]:
        """Best odds per selection inside the lookback window (captures intra-poll spikes)."""
        w = window_sec if window_sec is not None else self.peak_window_sec
        history = self.get_tick_history(fixture_key)
        if not history:
            return self.get_ticks(fixture_key)
        peaks = peak_ticks_in_window(history, w, now=time.time())
        if peaks:
            return peaks
        return self.get_ticks(fixture_key)

    def get_meta(self, fixture_key: str) -> Dict[str, Any]:
        return self._get_json(self._key_meta(fixture_key)) or {}

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
