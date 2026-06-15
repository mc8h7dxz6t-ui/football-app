"""Feed adapter protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from pipeline.tick import PriceTick


class FeedAdapter(ABC):
    name: str
    enabled_by_default: bool = True
    # Tier: fast exchange/sharp feeds poll more often than soft aggregators.
    tier: str = "soft"  # exchange | sharp | soft

    @property
    def poll_interval_sec(self) -> float:
        """Per-feed poll cadence (override via env FEED_POLL_SEC_<NAME>)."""
        import os

        env_key = f"FEED_POLL_SEC_{self.name.upper().replace('-', '_')}"
        if os.environ.get(env_key):
            return float(os.environ[env_key])
        defaults = {"exchange": 1.0, "sharp": 2.0, "soft": 5.0}
        return defaults.get(self.tier, 5.0)

    @abstractmethod
    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        ...
