"""Intra-window tick tracking — capture line moves between polls."""

from __future__ import annotations

from typing import Dict, List, Tuple

from pipeline.tick import PriceTick

SelectionKey = Tuple[str, str, str, str]  # market, bookmaker, source, selection


def selection_key(t: PriceTick) -> SelectionKey:
    return (t.market, t.bookmaker, t.source, t.selection)


def merge_snapshot(existing: List[PriceTick], incoming: List[PriceTick]) -> tuple[List[PriceTick], List[PriceTick]]:
    """Merge incoming ticks into snapshot; return (new_snapshot, changed_ticks for history)."""
    by_sel: Dict[SelectionKey, PriceTick] = {selection_key(t): t for t in existing}
    changed: List[PriceTick] = []
    for t in incoming:
        sk = selection_key(t)
        prev = by_sel.get(sk)
        if prev is None or abs(prev.odds - t.odds) >= 0.001:
            changed.append(t)
        by_sel[sk] = t
    return list(by_sel.values()), changed


def peak_ticks_in_window(history: List[PriceTick], window_sec: float, *, now: float) -> List[PriceTick]:
    """Best (max) back odds per selection seen inside the lookback window."""
    cutoff = now - window_sec
    recent = [t for t in history if t.received_at >= cutoff]
    best: Dict[SelectionKey, PriceTick] = {}
    for t in recent:
        sk = selection_key(t)
        cur = best.get(sk)
        if cur is None or t.odds > cur.odds:
            best[sk] = t
    return list(best.values())
