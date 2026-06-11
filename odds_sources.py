"""Multi-source odds fetch: API-Football + optional The Odds API."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

from odds_shopping import (
    OddsOffer,
    parse_api_football_odds,
    parse_odds_api_h2h,
    parse_odds_api_racing_win,
)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
REQUEST_TIMEOUT = 15

# The Odds API sport keys (football) aligned to our league names where possible
ODDS_API_FOOTBALL_SPORTS: Dict[str, str] = {
    "England Premier League": "soccer_epl",
    "England Championship": "soccer_efl_champ",
    "Germany Bundesliga": "soccer_germany_bundesliga",
    "Spain La Liga": "soccer_spain_la_liga",
    "Italy Serie A": "soccer_italy_serie_a",
    "France Ligue 1": "soccer_france_ligue_one",
    "Netherlands Eredivisie": "soccer_netherlands_eredivisie",
    "Portugal Primeira Liga": "soccer_portugal_primeira_liga",
    "Scotland Premiership": "soccer_spl",
    "Belgium First Division A": "soccer_belgium_first_div",
    "Denmark Superliga": "soccer_denmark_superliga",
    "Switzerland Super League": "soccer_switzerland_superleague",
    "Sweden Allsvenskan": "soccer_sweden_allsvenskan",
}

ODDS_API_RACING_SPORTS = [
    ("UK & Ireland", "horse_racing_uk"),
    ("USA", "horse_racing_usa"),
    ("Australia", "horse_racing_australia"),
]


def get_odds_api_key() -> str:
    key = (
        os.environ.get("ODDS_API_KEY")
        or os.environ.get("THE_ODDS_API_KEY")
        or ""
    ).strip()
    if key:
        return key
    try:
        import streamlit as st

        key = str(st.secrets.get("ODDS_API_KEY", "") or "").strip()  # type: ignore[union-attr]
    except Exception:
        key = ""
    return key


def _odds_api_get(path: str, params: Dict[str, Any], key: str) -> Any:
    from pipeline.rate_limits import get_budget

    budget = get_budget()
    if not budget.allow("odds_api"):
        return []
    try:
        resp = requests.get(
            f"{ODDS_API_BASE}/{path}",
            params={**params, "apiKey": key},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        budget.record("odds_api")
        return resp.json()
    except (requests.RequestException, ValueError):
        return []


def fetch_odds_api_football(league_name: str, key: str) -> List[Dict[str, Any]]:
    sport = ODDS_API_FOOTBALL_SPORTS.get(league_name)
    if not sport:
        return []
    data = _odds_api_get(
        f"sports/{sport}/odds",
        {"regions": "uk,eu", "markets": "h2h", "oddsFormat": "decimal"},
        key,
    )
    return data if isinstance(data, list) else []


def fetch_odds_api_racing(region_key: str, key: str) -> List[Dict[str, Any]]:
    data = _odds_api_get(
        f"sports/{region_key}/odds",
        {"regions": "uk,us,au", "markets": "win", "oddsFormat": "decimal"},
        key,
    )
    return data if isinstance(data, list) else []


def merge_offers(*groups: List[OddsOffer]) -> List[OddsOffer]:
    """Combine offers from multiple sources (line shop across all)."""
    out: List[OddsOffer] = []
    for g in groups:
        out.extend(g)
    return out


def football_offers_for_fixture(
    api_football_odds: Dict[str, Any],
    *,
    event_label: str,
    league_name: str = "",
    odds_api_key: str = "",
    odds_api_events: Optional[List[Dict[str, Any]]] = None,
) -> List[OddsOffer]:
    """Build merged offer list for one fixture from all configured sources."""
    af = parse_api_football_odds(api_football_odds, event_label=event_label)
    extra: List[OddsOffer] = []
    key = odds_api_key or get_odds_api_key()
    if key:
        events = odds_api_events
        if events is None and league_name:
            events = fetch_odds_api_football(league_name, key)
        if events:
            oa = parse_odds_api_h2h(events)
            # Keep only offers for this fixture (fuzzy match on team names in label)
            if event_label and " v " in event_label:
                home, away = [p.strip().lower() for p in event_label.split(" v ", 1)]
                extra = [
                    o
                    for o in oa
                    if home in o.event_label.lower() and away in o.event_label.lower()
                ]
            else:
                extra = oa
    return merge_offers(af, extra)


def racing_offers(region_key: str, odds_api_key: str = "") -> List[OddsOffer]:
    key = odds_api_key or get_odds_api_key()
    if not key:
        return []
    events = fetch_odds_api_racing(region_key, key)
    return parse_odds_api_racing_win(events)
