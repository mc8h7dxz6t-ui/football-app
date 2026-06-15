"""Dixon–Coles low-score adjustment τ(x,y)."""

from __future__ import annotations


def dixon_coles_tau(h: int, a: int, lam_h: float, lam_a: float, rho: float) -> float:
    """τ(x,y) for (0,0), (1,1), (0,1), (1,0); 1 elsewhere."""
    if abs(rho) < 1e-9:
        return 1.0
    if h == 0 and a == 0:
        return 1.0 - lam_h * lam_a * rho
    if h == 0 and a == 1:
        return 1.0 + lam_h * rho
    if h == 1 and a == 0:
        return 1.0 + lam_a * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0
