"""Feed circuit breakers — graceful degradation when sources fail."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class FeedState(str, Enum):
    CLOSED = "closed"      # healthy
    OPEN = "open"          # failing — use cache only
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    recovery_timeout_sec: float = 60.0
    half_open_max_calls: int = 2
    _failures: int = 0
    _state: FeedState = FeedState.CLOSED
    _opened_at: float = 0.0
    _half_open_calls: int = 0
    last_error: str = ""
    last_success_at: float = field(default_factory=time.time)

    @property
    def state(self) -> FeedState:
        if self._state == FeedState.OPEN:
            if time.time() - self._opened_at >= self.recovery_timeout_sec:
                self._state = FeedState.HALF_OPEN
                self._half_open_calls = 0
        return self._state

    def allow_call(self) -> bool:
        st = self.state
        if st == FeedState.CLOSED:
            return True
        if st == FeedState.HALF_OPEN:
            return self._half_open_calls < self.half_open_max_calls
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._state = FeedState.CLOSED
        self.last_success_at = time.time()
        self.last_error = ""

    def record_failure(self, error: str) -> None:
        self._failures += 1
        self.last_error = error[:500]
        if self._state == FeedState.HALF_OPEN:
            self._state = FeedState.OPEN
            self._opened_at = time.time()
            return
        if self._failures >= self.failure_threshold:
            self._state = FeedState.OPEN
            self._opened_at = time.time()

    def call_started(self) -> None:
        if self.state == FeedState.HALF_OPEN:
            self._half_open_calls += 1

    def status(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "state": self.state.value,
            "failures": self._failures,
            "last_error": self.last_error,
            "last_success_at": self.last_success_at,
        }


class BreakerRegistry:
    def __init__(self) -> None:
        self._breakers: Dict[str, CircuitBreaker] = {}

    def get(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name=name)
        return self._breakers[name]

    def all_status(self) -> Dict[str, Dict[str, object]]:
        return {k: v.status() for k, v in self._breakers.items()}


breakers = BreakerRegistry()
