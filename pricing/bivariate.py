"""Bivariate Poisson: X = Z1 + Z3, Y = Z2 + Z3 (shared low-score correlation)."""

from __future__ import annotations

import math
from typing import Tuple


def poisson_pmf(lam: float, k: int) -> float:
    if k < 0 or lam < 0:
        return 0.0
    try:
        return math.exp(-lam) * (lam**k) / math.factorial(k)
    except (OverflowError, ValueError):
        return 0.0


def lambdas_from_marginals(lam_h: float, lam_a: float, *, shared_frac: float) -> Tuple[float, float, float]:
    """Split marginal rates into independent + shared Poisson components (λ₁, λ₂, λ₃)."""
    lam_h = max(0.08, float(lam_h))
    lam_a = max(0.08, float(lam_a))
    frac = max(0.0, min(0.45, float(shared_frac)))
    lam3 = frac * min(lam_h, lam_a)
    lam1 = max(0.05, lam_h - lam3)
    lam2 = max(0.05, lam_a - lam3)
    return lam1, lam2, lam3


def score_probability(lam1: float, lam2: float, lam3: float, h: int, a: int) -> float:
    """P(home = h, away = a) under bivariate Poisson."""
    if h < 0 or a < 0:
        return 0.0
    z_max = min(h, a)
    total = 0.0
    for z in range(z_max + 1):
        total += poisson_pmf(lam1, h - z) * poisson_pmf(lam2, a - z) * poisson_pmf(lam3, z)
    return max(0.0, total)
