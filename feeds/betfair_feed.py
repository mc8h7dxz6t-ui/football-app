"""Betfair Exchange feed adapter (streaming-ready stub).

Production: Betfair Stream API (SSL socket) or Exchange REST polling.
Set BETFAIR_APP_KEY + session token for live integration.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from feeds.base import FeedAdapter
from pipeline.tick import PriceTick


class BetfairFeed(FeedAdapter):
    name = "betfair"
    enabled_by_default = bool(os.environ.get("BETFAIR_APP_KEY"))

    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        # Phase 2: Betfair Stream API (SSL) or listMarketBook REST poll
        if not context.get("betfair_market_id") or not os.environ.get("BETFAIR_APP_KEY"):
            return []
        return []
