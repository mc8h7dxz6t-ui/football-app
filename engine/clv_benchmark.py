"""CLV closing-line benchmark tiers — honest ladder vs Pinnacle institutional standard.

Tier priority (highest first):
  pinnacle          — Pinnacle panel or commercial API close
  exchange          — Betfair / Matchbook exchange close
  sharp_synthetic   — de-vigged sharp composite (margin-lift fair)
  api_football      — API-Football best-price close (not equivalent to Pinnacle)
  unavailable       — no closing line
"""

from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional, Tuple

CLV_TIERS = ("pinnacle", "exchange", "sharp_synthetic", "api_football", "unavailable")

_EXCHANGE_BOOKS = frozenset({"betfair", "matchbook", "smarkets", "betdaq"})


def _valid_decimal(odds: Any) -> Optional[float]:
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    return o if o > 1.0 else None


def triplet_complete(triplet: Mapping[str, Any]) -> bool:
    return all(_valid_decimal(triplet.get(s)) is not None for s in ("home", "draw", "away"))


def _margin_multiplier(closing: Mapping[str, Any]) -> Optional[float]:
    inv = []
    for side in ("home", "draw", "away"):
        o = _valid_decimal(closing.get(side))
        if o is not None:
            inv.append(1.0 / o)
    if len(inv) < 2:
        return None
    return sum(inv) - 1.0


def sharp_synthetic_fair_odds(closing: Mapping[str, Any]) -> Dict[str, Optional[float]]:
    """Institutional margin-lift fair odds from a raw 1X2 triplet."""
    raw: Dict[str, float] = {}
    for side in ("home", "draw", "away"):
        o = _valid_decimal(closing.get(side))
        if o is not None:
            raw[side] = o
    if len(raw) < 2:
        return {s: _valid_decimal(closing.get(s)) for s in ("home", "draw", "away")}
    margin = _margin_multiplier(closing)
    if margin is not None and margin >= 0:
        mult = 1.0 + margin
        return {k: round(v * mult, 4) for k, v in raw.items()}
    return {s: _valid_decimal(closing.get(s)) for s in ("home", "draw", "away")}


def closing_for_market(triplet: Mapping[str, Any], market: str) -> Optional[float]:
    key = str(market or "").lower()
    if key not in ("home", "draw", "away"):
        return None
    return _valid_decimal(triplet.get(key))


def resolve_clv_closing(
    market: str,
    *,
    pinnacle_1x2: Optional[Mapping[str, Any]] = None,
    exchange_1x2: Optional[Mapping[str, Any]] = None,
    api_football_1x2: Optional[Mapping[str, Any]] = None,
    legacy_market_odds: Optional[float] = None,
) -> Tuple[Optional[float], str, str]:
    """
    Pick closing odds for CLV using institutional tier ladder.

    Returns (closing_odds, tier, source_tag).
    """
    mk = str(market or "").lower()
    if pinnacle_1x2 and triplet_complete(pinnacle_1x2):
        odds = closing_for_market(pinnacle_1x2, mk)
        if odds is not None:
            return odds, "pinnacle", "pinnacle_panel"

    if exchange_1x2 and triplet_complete(exchange_1x2):
        odds = closing_for_market(exchange_1x2, mk)
        if odds is not None:
            return odds, "exchange", "exchange_close"

    if api_football_1x2 and triplet_complete(api_football_1x2):
        fair = sharp_synthetic_fair_odds(api_football_1x2)
        odds = closing_for_market(fair, mk)
        if odds is not None:
            return odds, "sharp_synthetic", "api_football_fair"
        odds = closing_for_market(api_football_1x2, mk)
        if odds is not None:
            return odds, "api_football", "api_sports"

    if legacy_market_odds and _valid_decimal(legacy_market_odds):
        return float(legacy_market_odds), "api_football", "legacy_market_odds"

    return None, "unavailable", "unavailable"


def parse_pinnacle_1x2_from_panel(panel: Any) -> Dict[str, Optional[float]]:
    """Extract Pinnacle 1X2 from all_bookmaker_odds-style panel rows."""
    out: Dict[str, Optional[float]] = {"home": None, "draw": None, "away": None}
    if not isinstance(panel, list):
        return out
    for row in panel:
        if not isinstance(row, dict):
            continue
        bm = str(row.get("bookmaker") or row.get("name") or "").lower()
        if "pinnacle" not in bm:
            continue
        for side in out:
            o = _valid_decimal(row.get(side) or row.get(f"odds_{side}"))
            if o is not None:
                cur = out[side]
                out[side] = o if cur is None else max(cur, o)
    return out


def parse_exchange_1x2_from_panel(panel: Any) -> Dict[str, Optional[float]]:
    """Best exchange 1X2 across Betfair / Matchbook rows in a panel."""
    out: Dict[str, Optional[float]] = {"home": None, "draw": None, "away": None}
    if not isinstance(panel, list):
        return out
    for row in panel:
        if not isinstance(row, dict):
            continue
        bm = str(row.get("bookmaker") or row.get("name") or "").lower()
        if not any(x in bm for x in _EXCHANGE_BOOKS):
            continue
        for side in out:
            o = _valid_decimal(row.get(side) or row.get(f"odds_{side}"))
            if o is not None:
                cur = out[side]
                out[side] = o if cur is None else max(cur, o)
    return out


def parse_pinnacle_1x2_from_odds_response(odds_raw: Any) -> Dict[str, Optional[float]]:
    """Pinnacle-only 1X2 from API-Football odds response bookmaker names."""
    out: Dict[str, Optional[float]] = {"home": None, "draw": None, "away": None}
    if not isinstance(odds_raw, list):
        return out
    for entry in odds_raw:
        if not isinstance(entry, dict):
            continue
        for bm in entry.get("bookmakers", []) or []:
            bm_name = str(bm.get("name") or "").lower()
            if "pinnacle" not in bm_name:
                continue
            for bet in bm.get("bets", []) or []:
                if bet.get("name") != "Match Winner":
                    continue
                for v in bet.get("values", []) or []:
                    val = str(v.get("value") or "").lower()
                    if val not in out:
                        continue
                    o = _valid_decimal(v.get("odd"))
                    if o is not None:
                        cur = out[val]
                        out[val] = o if cur is None else max(cur, o)
    return out


def parse_exchange_1x2_from_odds_response(odds_raw: Any) -> Dict[str, Optional[float]]:
    """Exchange 1X2 from API-Football odds response."""
    out: Dict[str, Optional[float]] = {"home": None, "draw": None, "away": None}
    if not isinstance(odds_raw, list):
        return out
    for entry in odds_raw:
        if not isinstance(entry, dict):
            continue
        for bm in entry.get("bookmakers", []) or []:
            bm_name = str(bm.get("name") or "").lower()
            if not any(x in bm_name for x in _EXCHANGE_BOOKS):
                continue
            for bet in bm.get("bets", []) or []:
                if bet.get("name") != "Match Winner":
                    continue
                for v in bet.get("values", []) or []:
                    val = str(v.get("value") or "").lower()
                    if val not in out:
                        continue
                    o = _valid_decimal(v.get("odd"))
                    if o is not None:
                        cur = out[val]
                        out[val] = o if cur is None else max(cur, o)
    return out


def clv_benchmark_disclosure() -> Dict[str, Any]:
    """Summary for /health — institutional honesty on CLV measurement."""
    return {
        "institutional_standard": "pinnacle_close",
        "tier_ladder": list(CLV_TIERS),
        "current_default": "api_football_or_panel_fallback",
        "pinnacle_api_configured": bool((os.getenv("PINNACLE_API_KEY") or "").strip()),
        "note": (
            "CLV gates use best available close on the tier ladder. "
            "API-Football best-price is not equivalent to Pinnacle closing line."
        ),
    }