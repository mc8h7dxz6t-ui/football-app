"""De-vigging and synthetic fair-probability lines (prop-shop grade).

Methods:
- proportional: standard inverse-odds normalisation (multiplicative)
- power: iterative power de-vig (better for longshots on some books)
- shin: Shin (1992) / ZY-style correction for insider-trader vig
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, Literal, Optional, Tuple

DeVigMethod = Literal["proportional", "power", "shin"]

OUTCOMES_1X2 = ("Home", "Draw", "Away")


def overround(odds: Dict[str, float]) -> float:
    """Book margin: sum(1/odds) - 1. Zero for fair book."""
    inv = [1.0 / float(o) for o in odds.values() if float(o) > 1.0]
    return sum(inv) - 1.0 if inv else 0.0


def _valid_odds(odds: Dict[str, float]) -> bool:
    return bool(odds) and all(float(o) > 1.0 for o in odds.values())


def devig_proportional(odds: Dict[str, float]) -> Dict[str, float]:
    if not _valid_odds(odds):
        return {}
    inv = {k: 1.0 / float(v) for k, v in odds.items()}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()} if s > 0 else {}


def devig_power(odds: Dict[str, float], *, iterations: int = 50) -> Dict[str, float]:
    """Power / iterative method — reduces favourite-longshot bias vs proportional."""
    if not _valid_odds(odds):
        return {}
    implied = {k: 1.0 / float(v) for k, v in odds.items()}
    target = sum(implied.values())
    lo, hi = 0.5, 2.0
    for _ in range(iterations):
        mid = (lo + hi) / 2.0
        powered = sum(p**mid for p in implied.values())
        if powered > target:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    adj = {key: p**k for key, p in implied.items()}
    s = sum(adj.values())
    return {key: v / s for key, v in adj.items()} if s > 0 else {}


def devig_shin(odds: Dict[str, float], *, iterations: int = 100) -> Dict[str, float]:
    """Shin de-vig — common syndicate approach for 1X2 / multi-runner."""
    if not _valid_odds(odds):
        return {}
    implied = {k: 1.0 / float(v) for k, v in odds.items()}
    keys = list(implied.keys())
    z_lo, z_hi = 0.0, 0.5
    for _ in range(iterations):
        z = (z_lo + z_hi) / 2.0
        denom = sum(math.sqrt(p**2 + 4 * (1 - z) * p**2 / len(keys)) for p in implied.values())
        if denom > 1.0:
            z_lo = z
        else:
            z_hi = z
    z = (z_lo + z_hi) / 2.0
    fair = {}
    for k, p in implied.items():
        fair[k] = (math.sqrt(p**2 + 4 * (1 - z) * p**2 / len(keys)) - p) / (2 * (1 - z))
    s = sum(fair.values())
    return {k: v / s for k, v in fair.items()} if s > 0 else {}


def devig_multiway(
    odds: Dict[str, float],
    *,
    method: DeVigMethod = "proportional",
) -> Dict[str, float]:
    if method == "proportional":
        return devig_proportional(odds)
    if method == "power":
        return devig_power(odds)
    if method == "shin":
        return devig_shin(odds)
    return devig_proportional(odds)


def devig_1x2(
    odds_home: float,
    odds_draw: float,
    odds_away: float,
    *,
    method: DeVigMethod = "proportional",
) -> Optional[Dict[str, float]]:
    odds = {"Home": odds_home, "Draw": odds_draw, "Away": odds_away}
    if not _valid_odds(odds):
        return None
    return devig_multiway(odds, method=method)


def fair_odds_from_probs(probs: Dict[str, float]) -> Dict[str, float]:
    """Convert fair probabilities to decimal odds (no vig)."""
    out: Dict[str, float] = {}
    for k, p in probs.items():
        pf = max(float(p), 1e-9)
        out[k] = 1.0 / pf
    return out


def blend_sharp_lines(
    lines: Iterable[Dict[str, float]],
    *,
    weights: Optional[Iterable[float]] = None,
    method: DeVigMethod = "proportional",
) -> Dict[str, float]:
    """Synthetic zero-vig line from multiple sharp books (average fair probs)."""
    lines_list = [devig_multiway(l, method=method) for l in lines if _valid_odds(l)]
    lines_list = [l for l in lines_list if l]
    if not lines_list:
        return {}
    keys = lines_list[0].keys()
    w = list(weights) if weights else [1.0] * len(lines_list)
    if len(w) != len(lines_list):
        w = [1.0] * len(lines_list)
    total_w = sum(w) or 1.0
    blended = {k: 0.0 for k in keys}
    for line, weight in zip(lines_list, w):
        for k in keys:
            blended[k] = blended.get(k, 0.0) + line.get(k, 0.0) * weight
    return {k: v / total_w for k, v in blended.items()}
