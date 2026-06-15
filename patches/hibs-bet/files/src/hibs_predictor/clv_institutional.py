"""Institutional CLV: fair closing de-vig, multiplicative edge, log-odds portfolio metric."""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

from hibs_predictor.odds_devig import odds_ratio_devig_probs


def _valid_decimal(odds: Any) -> Optional[float]:
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    return o if o > 1.0 else None


def _closing_margin_multiplier(closing: Mapping[str, Any]) -> Optional[float]:
    """Book overround M where sum(1/odds) = 1 + M."""
    inv = []
    for side in ("home", "draw", "away"):
        o = _valid_decimal(closing.get(side))
        if o is not None:
            inv.append(1.0 / o)
    if len(inv) < 2:
        return None
    return sum(inv) - 1.0


def fair_closing_1x2_odds(closing: Mapping[str, Any]) -> Dict[str, Optional[float]]:
    """
    Fair decimal closing odds per outcome.

    Primary: institutional margin lift ``odds_fair = odds_raw * (1 + M)`` on full 1X2.
    Fallback: odds-ratio de-vig when margin cannot be computed.
    """
    raw: Dict[str, float] = {}
    for side in ("home", "draw", "away"):
        o = _valid_decimal(closing.get(side))
        if o is not None:
            raw[side] = o
    if len(raw) < 2:
        return {s: _valid_decimal(closing.get(s)) for s in ("home", "draw", "away")}

    margin = _closing_margin_multiplier(closing)
    if margin is not None and margin >= 0:
        mult = 1.0 + margin
        return {k: round(v * mult, 4) for k, v in raw.items()}

    probs = odds_ratio_devig_probs(raw)
    if not probs:
        return {s: _valid_decimal(closing.get(s)) for s in ("home", "draw", "away")}
    fair: Dict[str, Optional[float]] = {s: None for s in ("home", "draw", "away")}
    for side, p in probs.items():
        key = str(side).lower()
        if key in fair and p > 0:
            fair[key] = round(1.0 / p, 4)
    return fair


def closing_overround_pct(closing: Mapping[str, Any]) -> Optional[float]:
    """Book margin on closing 1X2 as percentage (sum implied - 1) * 100."""
    m = _closing_margin_multiplier(closing)
    if m is None:
        return None
    return round(m * 100.0, 3)


def compute_edge_clv_pct(odds_taken: Optional[float], odds_close_fair: Optional[float]) -> Optional[float]:
    """Multiplicative CLV edge: (odds_taken / odds_close_fair) - 1, as percent."""
    taken = _valid_decimal(odds_taken)
    fair = _valid_decimal(odds_close_fair)
    if taken is None or fair is None:
        return None
    return round((taken / fair - 1.0) * 100.0, 3)


def compute_mu_clv_log(odds_taken: Optional[float], odds_close_fair: Optional[float]) -> Optional[float]:
    """Per-wager log-odds CLV: ln(odds_taken / odds_close_fair)."""
    taken = _valid_decimal(odds_taken)
    fair = _valid_decimal(odds_close_fair)
    if taken is None or fair is None:
        return None
    return round(math.log(taken / fair), 5)


def enrich_clv_institutional_fields(
    clv: Dict[str, Any],
    closing_raw: Mapping[str, Any],
    *,
    stake_outcome: Optional[str],
    odds_taken: Any,
) -> Dict[str, Any]:
    """Attach fair close, edge_clv_pct, mu_clv_log to an existing CLV blob."""
    fair = fair_closing_1x2_odds(closing_raw)
    clv["closing_odds_1x2_fair"] = fair
    clv["closing_overround_pct"] = closing_overround_pct(closing_raw)
    outcome = str(stake_outcome or "").lower()
    fair_side = fair.get(outcome) if outcome in ("home", "draw", "away") else None
    clv["odds_close_fair"] = fair_side
    clv["edge_clv_pct"] = compute_edge_clv_pct(odds_taken, fair_side)
    clv["mu_clv_log"] = compute_mu_clv_log(odds_taken, fair_side)
    return clv
