"""Redis in-memory line cache with dict fallback (institutional ingest layer)."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from pipeline.tick import PriceTick

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_TTL_SEC = int(os.environ.get("LINE_CACHE_TTL_SEC", "120"))


class LineCache:
    """Fixture-level tick store. Redis when available; else process memory."""

    def __init__(self, redis_url: str = _REDIS_URL, ttl_sec: int = _TTL_SEC) -> None:
        self.ttl_sec = ttl_sec
        self._memory: Dict[str, Dict[str, Any]] = {}
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

    def _key_meta(self, fixture_key: str) -> str:
        return f"fve:meta:{fixture_key}"

    def put_ticks(self, fixture_key: str, ticks: List[PriceTick], *, source: str = "") -> int:
        if not ticks:
            return 0
        seen: set[str] = set()
        unique: List[PriceTick] = []
        for t in ticks:
            dk = t.dedupe_key()
            if dk in seen:
                continue
            seen.add(dk)
            unique.append(t)

        payload = [t.to_dict() for t in unique]
        meta = {"updated_at": time.time(), "source": source, "count": len(payload)}
        if self._redis_ok and self._redis:
            pipe = self._redis.pipeline()
            pipe.setex(self._key_ticks(fixture_key), self.ttl_sec, json.dumps(payload))
            pipe.setex(self._key_meta(fixture_key), self.ttl_sec, json.dumps(meta))
            pipe.execute()
        else:
            self._memory[self._key_ticks(fixture_key)] = {"data": payload, "expires": time.time() + self.ttl_sec}
            self._memory[self._key_meta(fixture_key)] = {"data": meta, "expires": time.time() + self.ttl_sec}
        return len(unique)

    def get_ticks(self, fixture_key: str) -> List[PriceTick]:
        raw = self._get_json(self._key_ticks(fixture_key))
        if not raw:
            return []
        return [PriceTick.from_dict(d) for d in raw if isinstance(d, dict)]

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
