"""Normalised price ticks from any feed."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class PriceTick:
    fixture_key: str
    market: str
    selection: str
    odds: float
    bookmaker: str
    source: str
    category: str = "unknown"
    received_at: float = field(default_factory=time.time)
    sequence: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    def dedupe_key(self) -> str:
        payload = {
            "f": self.fixture_key,
            "m": self.market,
            "s": self.selection,
            "o": round(self.odds, 4),
            "b": self.bookmaker,
            "src": self.source,
        }
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PriceTick":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})
