"""The Odds API feed — OFF by default; slow poll to protect shared free-tier quota."""

from __future__ import annotations

import os
from typing import Any, Dict, List

from feeds.base import FeedAdapter
from odds_sources import ODDS_API_FOOTBALL_SPORTS, fetch_odds_api_football, get_odds_api_key
from pipeline.tick import PriceTick


def _enabled() -> bool:
    return os.environ.get("ENABLE_ODDS_API_FEED", "").strip().lower() in ("1", "true", "yes", "on")


class OddsApiFeed(FeedAdapter):
    name = "the-odds-api"
    enabled_by_default = False
    tier = "soft"

    @property
    def poll_interval_sec(self) -> float:
        if os.environ.get("FEED_POLL_SEC_THE_ODDS_API"):
            return super().poll_interval_sec
        return float(os.environ.get("ODDS_API_FEED_INTERVAL_SEC", "300"))

    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        if not _enabled() and not self.enabled_by_default:
            return []
        key = get_odds_api_key()
        if not key:
            return []
        league = str(context.get("league_name", ""))
        if not league:
            for name in ODDS_API_FOOTBALL_SPORTS:
                if name.split()[-1].lower() in fixture_key.lower():
                    league = name
                    break
        if not league:
            return []

        from odds_shopping import parse_odds_api_h2h

        events = fetch_odds_api_football(league, key)
        home = str(context.get("home_team", "")).lower()
        away = str(context.get("away_team", "")).lower()
        offers = parse_odds_api_h2h(events)
        ticks: List[PriceTick] = []
        for o in offers:
            if home and away:
                el = o.event_label.lower()
                if home not in el or away not in el:
                    continue
            ticks.append(
                PriceTick(
                    fixture_key=fixture_key,
                    market=o.market,
                    selection=o.selection_label,
                    odds=o.odds,
                    bookmaker=o.bookmaker,
                    source="the-odds-api",
                    category=o.category,
                    meta={"bet_url": o.bet_url},
                )
            )
        return ticks
