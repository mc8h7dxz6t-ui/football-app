"""The Odds API with fallback sport keys — fills thin 1X2 without hibs-bet."""

from __future__ import annotations

import os
from typing import Any, Dict, List

from feeds.base import FeedAdapter
from feeds.feed_utils import has_complete_1x2, merge_ticks_union
from odds_sources import ODDS_API_FOOTBALL_SPORTS, get_odds_api_key
from pipeline.tick import PriceTick


# Extra aliases when primary sport key returns thin coverage (mirrors hibs odds_thin_rescue).
ODDS_API_SPORT_FALLBACKS: Dict[str, List[str]] = {
    "soccer_epl": ["soccer_england_premier_league"],
    "soccer_efl_champ": ["soccer_england_championship"],
    "soccer_germany_bundesliga": ["soccer_germany_bundesliga1"],
    "soccer_spain_la_liga": ["soccer_spain_la_liga"],
    "soccer_italy_serie_a": ["soccer_italy_serie_a"],
    "soccer_france_ligue_one": ["soccer_france_ligue_one"],
}


def _sport_keys_for_context(context: Dict[str, Any]) -> List[str]:
    league = str(context.get("league_name") or "")
    primary = ODDS_API_FOOTBALL_SPORTS.get(league)
    keys: List[str] = []
    if primary:
        keys.append(primary)
        keys.extend(k for k in ODDS_API_SPORT_FALLBACKS.get(primary, []) if k not in keys)
    extra = os.environ.get("FVE_ODDS_BACKUP_SPORT_KEYS", "").strip()
    if extra:
        keys.extend(k.strip() for k in extra.split(",") if k.strip() and k.strip() not in keys)
    if keys:
        return keys
    # No league in context — scan all mapped sport keys (filtered by fixture teams in fetch).
    return list(dict.fromkeys(ODDS_API_FOOTBALL_SPORTS.values()))


class OddsBackupFeed(FeedAdapter):
    """Odds API feed with backup sport keys — opt-in via FVE_FEED_MODE=separate or ENABLED_FEEDS."""

    name = "odds-backup"
    enabled_by_default = False
    tier = "soft"

    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        key = get_odds_api_key()
        if not key:
            return []
        home = str(context.get("home_team") or "")
        away = str(context.get("away_team") or "")
        if not home or not away:
            parts = fixture_key.split(" v ", 1)
            if len(parts) == 2:
                home, away = parts[0].strip(), parts[1].strip()
        sport_keys = _sport_keys_for_context(context)
        if not sport_keys:
            return []

        from odds_shopping import parse_odds_api_h2h
        from odds_sources import _odds_api_get

        accumulated: List[PriceTick] = []
        for sport in sport_keys:
            events = _odds_api_get(
                f"sports/{sport}/odds",
                {"regions": "uk,eu", "markets": "h2h", "oddsFormat": "decimal"},
                key,
            )
            if not isinstance(events, list):
                continue
            offers = parse_odds_api_h2h(events)
            batch: List[PriceTick] = []
            for o in offers:
                el = o.event_label.lower()
                if home.lower() not in el or away.lower() not in el:
                    continue
                batch.append(
                    PriceTick(
                        fixture_key=fixture_key,
                        market=o.market,
                        selection=o.selection_label,
                        odds=o.odds,
                        bookmaker=o.bookmaker,
                        source="odds-backup",
                        category=o.category,
                        meta={"bet_url": o.bet_url, "sport_key": sport},
                    )
                )
            accumulated = merge_ticks_union(accumulated, batch)
            if has_complete_1x2(accumulated):
                break
        return accumulated
