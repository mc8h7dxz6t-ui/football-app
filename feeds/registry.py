"""Compose enabled feed adapters."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from feeds.api_football_feed import ApiFootballFeed
from feeds.betfair_feed import BetfairFeed
from feeds.base import FeedAdapter
from feeds.matchbook_feed import MatchbookFeed
from feeds.odds_api_feed import OddsApiFeed
from feeds.pinnacle_feed import PinnacleFeed


class FeedRegistry:
    def __init__(self, feeds: List[FeedAdapter]) -> None:
        self._feeds = {f.name: f for f in feeds}

    def enabled(self) -> List[FeedAdapter]:
        disabled = {s.strip() for s in os.environ.get("DISABLED_FEEDS", "").split(",") if s.strip()}
        out: List[FeedAdapter] = []
        for f in self._feeds.values():
            if f.name in disabled:
                continue
            if not f.enabled_by_default and f.name not in os.environ.get("ENABLED_FEEDS", ""):
                continue
            out.append(f)
        return out

    def get(self, name: str) -> Optional[FeedAdapter]:
        return self._feeds.get(name)


def build_default_registry() -> FeedRegistry:
    feeds: List[FeedAdapter] = [
        MatchbookFeed(),
        BetfairFeed(),
        PinnacleFeed(),
        ApiFootballFeed(),
    ]
    if os.environ.get("ENABLE_ODDS_API_FEED", "").strip().lower() in ("1", "true", "yes", "on"):
        feeds.append(OddsApiFeed())
    return FeedRegistry(feeds)
