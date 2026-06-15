"""Exponential time-decay weighting for recent match lists."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def decay_weight(days_ago: float, *, half_life_days: float) -> float:
    """Weight ∝ exp(-α t) with α = ln(2) / half_life."""
    if days_ago < 0:
        days_ago = 0.0
    hl = max(1.0, float(half_life_days))
    alpha = math.log(2.0) / hl
    return math.exp(-alpha * days_ago)


def rate_from_recent_matches(
    matches: List[Dict[str, Any]],
    *,
    kind: str,
    half_life_days: float = 45.0,
    min_weight: float = 0.05,
) -> Optional[float]:
    """Per-game goals `kind` ('for'|'against') from weighted recent matches."""
    if not matches:
        return None
    num = den = 0.0
    for row in matches:
        if not isinstance(row, dict):
            continue
        try:
            days = float(row.get("days_ago") if row.get("days_ago") is not None else row.get("days") or 0)
            gf = float(row.get("goals_for") or row.get("gf") or 0)
            ga = float(row.get("goals_against") or row.get("ga") or 0)
        except (TypeError, ValueError):
            continue
        w = decay_weight(days, half_life_days=half_life_days)
        if w < min_weight:
            continue
        goals = gf if kind == "for" else ga
        num += w * goals
        den += w
    if den <= 0:
        return None
    return num / den


def blend_decay_with_aggregate(
    team: Dict[str, Any],
    venue: str,
    kind: str,
    aggregate_rate: float,
    *,
    half_life_days: float,
    blend_games: float = 6.0,
) -> float:
    """Blend decay-weighted recent rate with aggregate venue/overall rate."""
    recent = team.get("recent_matches")
    if not isinstance(recent, list):
        recent_key = f"{venue}_recent_matches"
        recent = team.get(recent_key) if isinstance(team.get(recent_key), list) else None
    decay_rate = rate_from_recent_matches(recent or [], kind=kind, half_life_days=half_life_days)
    if decay_rate is None:
        return aggregate_rate
    n = len([r for r in (recent or []) if isinstance(r, dict)])
    weight = n / (n + blend_games)
    return weight * decay_rate + (1.0 - weight) * aggregate_rate
