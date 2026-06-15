"""API-Football odds feed (legacy poll — lower resolution)."""

from __future__ import annotations

import os
from typing import Any, Dict, List

import requests

from bookmakers import classify_bookmaker
from feeds.base import FeedAdapter
from odds_shopping import parse_api_football_odds
from pipeline.tick import PriceTick

BASE = "https://v3.football.api-sports.io"
TIMEOUT = 15


class ApiFootballFeed(FeedAdapter):
    name = "api-football"
    enabled_by_default = True
    tier = "soft"

    def _key(self) -> str:
        return (os.environ.get("API_SPORTS_KEY") or os.environ.get("API_FOOTBALL_KEY") or "").strip()

    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        fixture_id = context.get("fixture_id")
        if not fixture_id:
            return []
        key = self._key()
        if not key:
            raise RuntimeError("API_SPORTS_KEY not set")

        resp = requests.get(
            f"{BASE}/odds",
            headers={"x-apisports-key": key},
            params={"fixture": fixture_id},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        label = context.get("event_label", fixture_key)
        offers = parse_api_football_odds(data, event_label=label)
        ticks: List[PriceTick] = []
        for o in offers:
            ticks.append(
                PriceTick(
                    fixture_key=fixture_key,
                    market=o.market,
                    selection=o.selection_label or o.market,
                    odds=o.odds,
                    bookmaker=o.bookmaker,
                    source="api-football",
                    category=o.category,
                    meta={"bet_url": o.bet_url, "bookmaker_id": o.bookmaker_id},
                )
            )
        return ticks
