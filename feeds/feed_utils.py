"""Helpers for composite / backup feed chains."""

from __future__ import annotations

from typing import Dict, List, Set

from pipeline.tick import PriceTick

_1X2 = ("Home", "Draw", "Away")


def markets_covered(ticks: List[PriceTick]) -> Set[str]:
    return {t.market for t in ticks if t.market in _1X2 and t.odds > 1.0}


def has_complete_1x2(ticks: List[PriceTick]) -> bool:
    return markets_covered(ticks) >= set(_1X2)


def merge_ticks_union(existing: List[PriceTick], incoming: List[PriceTick]) -> List[PriceTick]:
    """Union by (market, bookmaker) keeping best odds per leg."""
    best: Dict[tuple[str, str], PriceTick] = {}
    for tick in list(existing) + list(incoming):
        if tick.odds <= 1.0:
            continue
        key = (tick.market, tick.bookmaker)
        prev = best.get(key)
        if prev is None or tick.odds > prev.odds:
            best[key] = tick
    return list(best.values())
