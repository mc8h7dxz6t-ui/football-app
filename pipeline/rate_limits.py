"""Shared API call budgets — protect Matchbook / Odds API keys used across repos."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_PREFIX = os.environ.get("FVE_BUDGET_PREFIX", "fve:budget")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if raw == "" or raw == "0":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _hour_bucket() -> str:
    return time.strftime("%Y%m%d%H", time.gmtime())


@dataclass
class BudgetConfig:
    """Per-source hourly caps (0 = unlimited). Conservative defaults for shared keys."""

    matchbook_per_hour: int = _env_int("FVE_MATCHBOOK_MAX_CALLS_PER_HOUR", 1200)
    odds_api_per_hour: int = _env_int("FVE_ODDS_API_MAX_CALLS_PER_HOUR", 15)
    api_football_per_hour: int = _env_int("FVE_API_FOOTBALL_MAX_CALLS_PER_HOUR", 100)


class ApiBudget:
    """Redis-backed counters so multiple repos/processes share one budget."""

    SOURCE_ALIASES = {
        "matchbook": "matchbook",
        "the-odds-api": "odds_api",
        "odds-api": "odds_api",
        "odds_api": "odds_api",
        "api-football": "api_football",
        "api_football": "api_football",
    }

    def __init__(self, config: Optional[BudgetConfig] = None) -> None:
        self.config = config or BudgetConfig()
        self._mem: Dict[str, int] = {}
        self._redis = None
        self._redis_ok = False
        try:
            import redis

            self._redis = redis.from_url(_REDIS_URL, decode_responses=True, socket_connect_timeout=1)
            self._redis.ping()
            self._redis_ok = True
        except Exception:
            self._redis = None

    def _normalize(self, source: str) -> str:
        return self.SOURCE_ALIASES.get(source.lower(), source.lower())

    def _cap(self, source: str) -> int:
        src = self._normalize(source)
        if src == "matchbook":
            return self.config.matchbook_per_hour
        if src == "odds_api":
            return self.config.odds_api_per_hour
        if src == "api_football":
            return self.config.api_football_per_hour
        return 0

    def _key(self, source: str) -> str:
        return f"{_PREFIX}:{self._normalize(source)}:{_hour_bucket()}"

    def allow(self, source: str) -> bool:
        cap = self._cap(source)
        if cap <= 0:
            return True
        k = self._key(source)
        if self._redis_ok and self._redis:
            count = int(self._redis.get(k) or 0)
        else:
            count = self._mem.get(k, 0)
        return count < cap

    def record(self, source: str, n: int = 1) -> int:
        cap = self._cap(source)
        k = self._key(source)
        if self._redis_ok and self._redis:
            new_val = int(self._redis.incrby(k, n))
            if new_val == n:
                self._redis.expire(k, 7200)
            return new_val
        self._mem[k] = self._mem.get(k, 0) + n
        return self._mem[k]

    def status(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"backend": "redis" if self._redis_ok else "memory", "hour": _hour_bucket(), "sources": {}}
        for src in ("matchbook", "odds_api", "api_football"):
            cap = self._cap(src)
            k = self._key(src)
            used = int(self._redis.get(k) or 0) if self._redis_ok and self._redis else self._mem.get(k, 0)
            out["sources"][src] = {
                "used_this_hour": used,
                "cap_per_hour": cap if cap > 0 else None,
                "remaining": None if cap <= 0 else max(cap - used, 0),
            }
        return out


_budget: Optional[ApiBudget] = None


def get_budget() -> ApiBudget:
    global _budget
    if _budget is None:
        _budget = ApiBudget()
    return _budget
