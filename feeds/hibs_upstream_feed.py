"""Hibs-bet upstream feed — consume /api/fve/lines instead of direct book APIs."""

from __future__ import annotations

import os
from typing import Any, Dict, List

from bookmakers import classify_bookmaker
from feeds.base import FeedAdapter
from pipeline.tick import PriceTick
from services.hibs_lines_client import HibsLinesClient

_SIDE_MAP = {
    "home": ("Home", "Home"),
    "draw": ("Draw", "Draw"),
    "away": ("Away", "Away"),
}


class HibsUpstreamFeed(FeedAdapter):
    name = "hibs-upstream"
    enabled_by_default = True
    tier = "soft"

    def __init__(self, client: HibsLinesClient | None = None) -> None:
        self._client = client or HibsLinesClient()

    @classmethod
    def upstream_mode_enabled(cls) -> bool:
        mode = (os.environ.get("FVE_UPSTREAM_MODE") or "").strip().lower()
        if mode in ("hibs", "hibs-bet", "upstream"):
            return True
        return bool((os.environ.get("HIBS_UPSTREAM_BASE_URL") or "").strip())

    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        if not self._client.configured():
            return []
        payload = self._client.fetch_fixture_lines(fixture_key)
        best = payload.get("best_odds_1x2") or {}
        sources = payload.get("best_odds_source") or {}
        ticks: List[PriceTick] = []
        for side, (market, selection) in _SIDE_MAP.items():
            try:
                odds = float(best.get(side) or 0)
            except (TypeError, ValueError):
                continue
            if odds <= 1.0:
                continue
            bookmaker = str(sources.get(side) or "hibs-bet")
            ticks.append(
                PriceTick(
                    fixture_key=fixture_key,
                    market=market,
                    selection=selection,
                    odds=odds,
                    bookmaker=bookmaker,
                    source="hibs-upstream",
                    category=classify_bookmaker(bookmaker),
                    meta={
                        "upstream": "hibs-bet",
                        "fixture_id": payload.get("fixture_id"),
                    },
                )
            )
        return ticks

    def fetch_sports_context(self, fixture_key: str) -> Dict[str, Any] | None:
        if not self._client.configured():
            return None
        payload = self._client.fetch_fixture_lines(fixture_key)
        home = payload.get("home_stats")
        away = payload.get("away_stats")
        if not home or not away:
            return None
        return {
            "fixture_id": payload.get("fixture_id"),
            "home_team": payload.get("home_team"),
            "away_team": payload.get("away_team"),
            "home_stats": home,
            "away_stats": away,
            "kickoff_iso": payload.get("kickoff_iso"),
            "source": "hibs-upstream",
        }
