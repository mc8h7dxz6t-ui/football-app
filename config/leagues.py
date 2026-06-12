"""League ids for auto watchlist (aligned with Streamlit app)."""

from __future__ import annotations

import os
from typing import Dict, List

DEFAULT_LEAGUES: Dict[str, int] = {
    "Scotland Premiership": 179,
    "Scotland Championship": 180,
    "England Premier League": 39,
    "England Championship": 40,
    "Germany Bundesliga": 78,
    "Spain La Liga": 140,
    "Italy Serie A": 135,
    "France Ligue 1": 61,
    "Netherlands Eredivisie": 88,
    "Portugal Primeira Liga": 94,
    "Belgium First Division A": 144,
    "Denmark Superliga": 119,
    "Switzerland Super League": 207,
    "Sweden Allsvenskan": 113,
}


def leagues_for_watchlist() -> Dict[str, int]:
    """Override via FVE_LEAGUE_IDS=39,140 or FVE_LEAGUE_NAMES=England Premier League,..."""
    raw_ids = os.environ.get("FVE_LEAGUE_IDS", "").strip()
    if raw_ids:
        ids = [int(x.strip()) for x in raw_ids.split(",") if x.strip()]
        return {f"league_{lid}": lid for lid in ids}
    raw_names = os.environ.get("FVE_LEAGUE_NAMES", "").strip()
    if raw_names:
        names = [n.strip() for n in raw_names.split(",") if n.strip()]
        return {n: DEFAULT_LEAGUES[n] for n in names if n in DEFAULT_LEAGUES}
    return dict(DEFAULT_LEAGUES)


def league_id_list() -> List[int]:
    return list(leagues_for_watchlist().values())
