"""Feed adapter protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from pipeline.tick import PriceTick


class FeedAdapter(ABC):
    name: str
    enabled_by_default: bool = True

    @abstractmethod
    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        ...
