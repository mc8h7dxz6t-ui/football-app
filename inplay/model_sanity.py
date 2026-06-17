"""MC vs closed-form sanity checks for I2."""

from __future__ import annotations

from typing import Dict, Optional

from inplay.evidence_store import record_model_sanity
from inplay.kernel_bridge import mc_intensity
from inplay.monte_carlo_gpu import simulate_1x2


def closed_form_1x2(home_lambda: float, away_lambda: float) -> Dict[str, float]:
    """Independent Poisson closed-form 1X2 (sanity reference)."""
    # Small grid approximation
    max_g = 8
    ph = pow(2.718281828, -home_lambda)
    pa = pow(2.718281828, -away_lambda)
    p_home = p_draw = p_away = 0.0
    for i in range(max_g + 1):
        pi = ph * pow(home_lambda, i) / max(1, __import__("math").factorial(i))
        for j in range(max_g + 1):
            pj = pa * pow(away_lambda, j) / max(1, __import__("math").factorial(j))
            p = pi * pj
            if i > j:
                p_home += p
            elif i < j:
                p_away += p
            else:
                p_draw += p
    s = p_home + p_draw + p_away or 1.0
    return {
        "home": round(p_home / s, 5),
        "draw": round(p_draw / s, 5),
        "away": round(p_away / s, 5),
    }


def run_sanity_check(
    *,
    home_lambda: float,
    away_lambda: float,
    fixture_id: Optional[int] = None,
    paths: int = 100_000,
) -> float:
    """Compare MC home prob to closed-form; record for I2."""
    mc = mc_intensity(home_lambda, away_lambda, paths=paths)
    cf = closed_form_1x2(home_lambda, away_lambda)
    diff = record_model_sanity(
        fixture_id=fixture_id,
        mc_home=float(mc["home"]),
        cf_home=float(cf["home"]),
        held_out=True,
    )
    return diff
