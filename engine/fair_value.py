"""Benchmark soft lines against sharp synthetic fair value — detect model hallucination."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from engine.devig import blend_sharp_lines, devig_multiway, fair_odds_from_probs


@dataclass
class SharpBenchmark:
    """Result of comparing a selection to the sharp synthetic line."""

    selection: str
    soft_odds: float
    sharp_fair_odds: float
    sharp_fair_prob: float
    soft_implied_prob: float
    edge_vs_sharp_pct: float
    model_prob: Optional[float] = None
    model_edge_vs_sharp_pct: Optional[float] = None
    likely_hallucination: bool = False


def synthetic_sharp_line(
    sharp_books: List[Dict[str, float]],
    *,
    method: str = "shin",
) -> Dict[str, float]:
    """Institutional-style synthetic zero-vig line from sharp/exchange books only."""
    return blend_sharp_lines(sharp_books, method=method)  # type: ignore[arg-type]


def benchmark_vs_sharp(
    *,
    selection: str,
    soft_odds: float,
    sharp_line_probs: Dict[str, float],
    model_prob: Optional[float] = None,
    min_model_sharp_gap_pct: float = 3.0,
) -> Optional[SharpBenchmark]:
    """True edge = soft price vs sharp fair, not vs naive max odds alone.

    If model shows edge but sharp fair says otherwise, flag likely_hallucination.
    """
    if soft_odds <= 1.0 or selection not in sharp_line_probs:
        return None
    sharp_p = float(sharp_line_probs[selection])
    if sharp_p <= 0:
        return None
    sharp_fair_odds = 1.0 / sharp_p
    soft_implied = 1.0 / soft_odds
    edge_vs_sharp = (soft_odds * sharp_p - 1.0) * 100.0

    model_edge = None
    hallucination = False
    if model_prob is not None:
        model_edge = (float(model_prob) - sharp_p) * 100.0
        naive_edge = (float(model_prob) * soft_odds - 1.0) * 100.0
        if naive_edge > 0 and edge_vs_sharp < 0:
            hallucination = True
        if model_edge > min_model_sharp_gap_pct and edge_vs_sharp < min_model_sharp_gap_pct:
            hallucination = True

    return SharpBenchmark(
        selection=selection,
        soft_odds=soft_odds,
        sharp_fair_odds=sharp_fair_odds,
        sharp_fair_prob=sharp_p,
        soft_implied_prob=soft_implied,
        edge_vs_sharp_pct=edge_vs_sharp,
        model_prob=model_prob,
        model_edge_vs_sharp_pct=model_edge,
        likely_hallucination=hallucination,
    )


def sharp_line_from_quotes(
    quotes_by_channel: Dict[str, Dict[str, Dict[str, float]]],
    market: str,
) -> Dict[str, float]:
    """Build sharp synthetic line from shopped exchange + sharp channel quotes."""
    sharp_odds: List[Dict[str, float]] = []
    for ch in ("exchange", "sharp"):
        q = quotes_by_channel.get(market, {}).get(ch, {})
        o = float(q.get("odds") or 0)
        if o > 1.0:
            sel = market
            sharp_odds.append({sel: o})
    if not sharp_odds:
        return {}
    if len(sharp_odds) == 1:
        return devig_multiway(sharp_odds[0])
    blended = blend_sharp_lines(sharp_odds)
    return blended
