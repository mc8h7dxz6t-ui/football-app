"""Pinnacle sharp line feed (stub — requires commercial API access)."""

from __future__ import annotations

import os
from typing import Any, Dict, List

from feeds.base import FeedAdapter
from pipeline.tick import PriceTick


class PinnacleFeed(FeedAdapter):
    name = "pinnacle"
    enabled_by_default = bool(os.environ.get("PINNACLE_API_KEY"))
    tier = "sharp"

    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        # Phase 2: commercial Pinnacle API integration
        return []
