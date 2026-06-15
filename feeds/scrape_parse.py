"""Shared scrape payload → ticks parsing."""

from __future__ import annotations

from typing import Any, Dict, List

from bookmakers import classify_bookmaker
from pipeline.tick import PriceTick

_SIDE_MAP = {
    "home": ("Home", "Home"),
    "draw": ("Draw", "Draw"),
    "away": ("Away", "Away"),
}


def payload_to_ticks(fixture_key: str, payload: Dict[str, Any], *, source: str = "scrape") -> List[PriceTick]:
    ticks: List[PriceTick] = []
    best = payload.get("best_odds_1x2") or {}
    sources = payload.get("best_odds_source") or {}
    upstream = payload.get("scrape_source") or payload.get("source") or source
    for side, (market, selection) in _SIDE_MAP.items():
        try:
            odds = float(best.get(side) or 0)
        except (TypeError, ValueError):
            continue
        if odds <= 1.0:
            continue
        bookmaker = str(sources.get(side) or upstream)
        ticks.append(
            PriceTick(
                fixture_key=fixture_key,
                market=market,
                selection=selection,
                odds=odds,
                bookmaker=bookmaker,
                source=source,
                category=classify_bookmaker(bookmaker),
                meta={"upstream": upstream},
            )
        )
    for row in payload.get("all_bookmaker_odds") or payload.get("bookmakers") or []:
        if not isinstance(row, dict):
            continue
        bm = str(row.get("bookmaker") or row.get("name") or "unknown")
        for side, (market, selection) in _SIDE_MAP.items():
            try:
                odds = float(row.get(side) or row.get(f"odds_{side}") or 0)
            except (TypeError, ValueError):
                continue
            if odds <= 1.0:
                continue
            ticks.append(
                PriceTick(
                    fixture_key=fixture_key,
                    market=market,
                    selection=selection,
                    odds=odds,
                    bookmaker=bm,
                    source=source,
                    category=classify_bookmaker(bm),
                    meta={"upstream": upstream},
                )
            )
    return ticks
