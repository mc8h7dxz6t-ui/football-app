"""FVE league name → FotMob competition id (public JSON, no API key)."""

from __future__ import annotations

from typing import Dict, Optional

# Aligns with config/leagues.py display names.
FOTMOB_LEAGUE_ID: Dict[str, int] = {
    "Scotland Premiership": 64,
    "Scotland Championship": 123,
    "England Premier League": 47,
    "England Championship": 48,
    "Germany Bundesliga": 54,
    "Spain La Liga": 87,
    "Italy Serie A": 55,
    "France Ligue 1": 53,
    "Netherlands Eredivisie": 57,
    "Portugal Primeira Liga": 61,
    "Belgium First Division A": 40,
    "Denmark Superliga": 46,
    "Switzerland Super League": 69,
    "Sweden Allsvenskan": 67,
    "Czech First League": 122,
}


def fotmob_id_for_league(league_name: str) -> Optional[int]:
    return FOTMOB_LEAGUE_ID.get(league_name.strip())
