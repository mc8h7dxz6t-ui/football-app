"""Portfolio Kelly — sqrt(k) within fixture for correlated derivative legs."""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List


def portfolio_stake_cap_pct() -> float:
    try:
        return max(1.0, float(os.environ.get("FVE_PORTFOLIO_STAKE_CAP_PCT", "20")))
    except ValueError:
        return 20.0


def apply_portfolio_kelly_to_picks(
    picks: List[Dict[str, Any]],
    *,
    bankroll: float,
    cap_pct: float | None = None,
) -> List[Dict[str, Any]]:
    """
    Joint sqrt(k) stake scaling for correlated legs on the same fixture.

    Mirrors hibs-bet ``apply_portfolio_kelly`` fixture-level leg rule.
    """
    if not picks:
        return picks
    cap = cap_pct if cap_pct is not None else portfolio_stake_cap_pct()
    k = len(picks)
    denom = math.sqrt(float(k)) if k > 1 else 1.0

    for p in picks:
        raw_stake = float(p.get("stake") or 0.0)
        raw_pct = (raw_stake / bankroll * 100.0) if bankroll > 0 else 0.0
        p["portfolio_kelly_original_stake"] = raw_stake
        p["portfolio_kelly_original_pct"] = round(raw_pct, 2)
        p["portfolio_match_legs"] = k

    scaled = [float(p["stake"]) / denom for p in picks]
    total_pct = sum((s / bankroll * 100.0) if bankroll > 0 else 0.0 for s in scaled)
    cap_scaled = False
    if total_pct > cap and total_pct > 0:
        factor = cap / total_pct
        scaled = [s * factor for s in scaled]
        cap_scaled = True

    out: List[Dict[str, Any]] = []
    for p, stake in zip(picks, scaled):
        row = dict(p)
        row["stake"] = round(stake, 2)
        row["stake_pct"] = round(stake / bankroll * 100.0, 2) if bankroll > 0 else 0.0
        row["portfolio_cap_scaled"] = cap_scaled
        out.append(row)
    return out
