"""Bookmaker metadata: channel (exchange / sharp / soft), homepage / sport links.

API-Football and The Odds API return names and ids; we classify and build
place-bet URLs where the upstream API does not provide deep links.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple
from urllib.parse import quote

BookCategory = str  # exchange | sharp | soft | unknown

# Normalised name fragment -> (category, base URL for football/sports)
_REGISTRY: Dict[str, Tuple[BookCategory, str]] = {
    # Exchanges
    "betfair": ("exchange", "https://www.betfair.com/exchange/plus/football"),
    "betfair exchange": ("exchange", "https://www.betfair.com/exchange/plus/football"),
    "matchbook": ("exchange", "https://www.matchbook.com/events/sports/football"),
    "smarkets": ("exchange", "https://smarkets.com/event/sport/football"),
    "betdaq": ("exchange", "https://www.betdaq.com/exchange/football"),
    # Sharp / low-margin
    "pinnacle": ("sharp", "https://www.pinnacle.com/en/soccer/matchups/highlights/"),
    "betcris": ("sharp", "https://www.betcris.com/en/sportsbook/soccer/"),
    "bookmaker": ("sharp", "https://www.bookmaker.eu/sports/soccer"),
    # Soft / recreational (UK & EU retail)
    "bet365": ("soft", "https://www.bet365.com/#/AC/B1/C1/D13/E1/F2/"),
    "william hill": ("soft", "https://sports.williamhill.com/betting/en-gb/football"),
    "ladbrokes": ("soft", "https://sports.ladbrokes.com/football"),
    "coral": ("soft", "https://sports.coral.co.uk/football"),
    "sky bet": ("soft", "https://m.skybet.com/football"),
    "paddy power": ("soft", "https://www.paddypower.com/football"),
    "betfred": ("soft", "https://www.betfred.com/sports/football"),
    "boylesports": ("soft", "https://www.boylesports.com/sports/football"),
    "888sport": ("soft", "https://www.888sport.com/football/"),
    "unibet": ("soft", "https://www.unibet.co.uk/betting/sports/filter/football"),
    "betway": ("soft", "https://betway.com/en/sports/grp/soccer"),
    "bwin": ("soft", "https://sports.bwin.com/en/sports/football-4"),
    "marathon": ("soft", "https://www.marathonbet.co.uk/en/betting/Football"),
    "1xbet": ("soft", "https://1xbet.com/en/line/Football/"),
    "10bet": ("soft", "https://www.10bet.com/sports/football/"),
    "betvictor": ("soft", "https://www.betvictor.com/en/sports/football"),
    "sportingbet": ("soft", "https://sports.sportingbet.com/en/sports/football-4"),
    # Racing-focused (soft) — used when sport context is racing
    "bet365 racing": ("soft", "https://www.bet365.com/#/AC/B2/D13/E1/F163/"),
    "william hill racing": ("soft", "https://sports.williamhill.com/betting/en-gb/horse-racing"),
    "ladbrokes racing": ("soft", "https://sports.ladbrokes.com/horse-racing"),
    "paddy power racing": ("soft", "https://www.paddypower.com/horse-racing"),
}

# API-Football numeric ids (subset) -> registry key
_API_FOOTBALL_IDS: Dict[int, str] = {
    8: "bet365",
    3: "betfair",
    11: "william hill",
    16: "unibet",
    4: "pinnacle",
    6: "bwin",
    13: "betway",
    21: "888sport",
    32: "betvictor",
    34: "10bet",
    35: "marathon",
}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def classify_bookmaker(name: str, bookmaker_id: Optional[int] = None) -> BookCategory:
    """Classify a book as exchange, sharp, soft, or unknown."""
    n = _norm(name)
    if bookmaker_id is not None and bookmaker_id in _API_FOOTBALL_IDS:
        key = _API_FOOTBALL_IDS[bookmaker_id]
        if key in _REGISTRY:
            return _REGISTRY[key][0]
    for key, (cat, _) in _REGISTRY.items():
        if key in n or n in key:
            return cat
    if "exchange" in n or "matchbook" in n or "smarket" in n:
        return "exchange"
    return "unknown"


def bookmaker_url(
    name: str,
    *,
    sport: str = "football",
    event_label: str = "",
    bookmaker_id: Optional[int] = None,
    direct_link: str = "",
) -> str:
    """Best-effort place-bet URL. Prefer upstream deep link when provided."""
    if direct_link and direct_link.startswith("http"):
        return direct_link

    n = _norm(name)
    if bookmaker_id is not None and bookmaker_id in _API_FOOTBALL_IDS:
        n = _API_FOOTBALL_IDS[bookmaker_id]

    sport_key = "racing" if sport == "racing" else "football"
    for key, (_, url) in _REGISTRY.items():
        if key in n or n in key:
            if sport_key == "racing" and "racing" in key:
                return url
            if sport_key == "football" and "racing" not in key:
                return url

    # Generic search fallback for unknown books
    q = quote(event_label or sport)
    return f"https://www.google.com/search?q={quote(name)}+{q}+betting"
