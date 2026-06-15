"""Joint scoreline matrix and derivative market extraction."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pricing.bivariate import lambdas_from_marginals, poisson_pmf, score_probability
from pricing.dixon_coles import dixon_coles_tau


def institutional_mode_enabled() -> bool:
    mode = (os.environ.get("FVE_PRICING_MODE") or "institutional").strip().lower()
    return mode not in ("independent", "legacy", "simple")


@dataclass(frozen=True)
class PricingConfig:
    max_goals: int = 10
    shared_frac: float = 0.22
    dixon_coles_rho: float = -0.13
    use_bivariate: bool = True
    use_dixon_coles: bool = True

    @classmethod
    def from_env(cls) -> "PricingConfig":
        def _f(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, str(default)))
            except ValueError:
                return default

        def _i(name: str, default: int) -> int:
            try:
                return int(os.environ.get(name, str(default)))
            except ValueError:
                return default

        biv = os.environ.get("FVE_BIVARIATE_POISSON", "1").strip().lower() not in ("0", "false", "no", "off")
        dc = os.environ.get("FVE_DIXON_COLES", "1").strip().lower() not in ("0", "false", "no", "off")
        return cls(
            max_goals=max(6, min(12, _i("FVE_SCORE_MATRIX_MAX_GOALS", 10))),
            shared_frac=max(0.0, min(0.45, _f("FVE_BIV_POISSON_SHARED_FRAC", 0.22))),
            dixon_coles_rho=_f("FVE_DIXON_COLES_RHO", -0.13),
            use_bivariate=biv,
            use_dixon_coles=dc,
        )


def _independent_cell_prob(lam_h: float, lam_a: float, h: int, a: int) -> float:
    return poisson_pmf(lam_h, h) * poisson_pmf(lam_a, a)


def build_score_matrix(
    lam_h: float,
    lam_a: float,
    *,
    config: Optional[PricingConfig] = None,
) -> Dict[Tuple[int, int], float]:
    """P(X=h, Y=a) for h,a in 0..max_goals — bivariate + optional Dixon–Coles."""
    cfg = config or PricingConfig.from_env()
    lam_h = max(0.05, min(6.0, float(lam_h)))
    lam_a = max(0.05, min(6.0, float(lam_a)))
    rho = cfg.dixon_coles_rho if cfg.use_dixon_coles else 0.0

    if cfg.use_bivariate:
        lam1, lam2, lam3 = lambdas_from_marginals(lam_h, lam_a, shared_frac=cfg.shared_frac)
        raw: Dict[Tuple[int, int], float] = {}
        for h in range(cfg.max_goals + 1):
            for a in range(cfg.max_goals + 1):
                p = score_probability(lam1, lam2, lam3, h, a)
                if cfg.use_dixon_coles:
                    p *= dixon_coles_tau(h, a, lam_h, lam_a, rho)
                if p > 0:
                    raw[(h, a)] = p
    else:
        raw = {}
        for h in range(cfg.max_goals + 1):
            for a in range(cfg.max_goals + 1):
                p = _independent_cell_prob(lam_h, lam_a, h, a)
                if cfg.use_dixon_coles:
                    p *= dixon_coles_tau(h, a, lam_h, lam_a, rho)
                if p > 0:
                    raw[(h, a)] = p

    total = sum(raw.values()) or 1.0
    return {k: v / total for k, v in raw.items()}


def derive_market_probs(matrix: Dict[Tuple[int, int], float]) -> Dict[str, float]:
    """Sum score cells → 1X2, Over 2.5, BTTS (coherent derivatives)."""
    home_p = draw_p = away_p = 0.0
    over25 = 0.0
    btts = 0.0
    for (h, a), p in matrix.items():
        if h > a:
            home_p += p
        elif h == a:
            draw_p += p
        else:
            away_p += p
        if h + a >= 3:
            over25 += p
        if h >= 1 and a >= 1:
            btts += p
    total = home_p + draw_p + away_p or 1.0
    return {
        "Home": home_p / total,
        "Draw": draw_p / total,
        "Away": away_p / total,
        "Over2.5": min(max(over25, 0.01), 0.99),
        "BTTS": min(max(btts, 0.01), 0.99),
    }


def matrix_marginals(matrix: Dict[Tuple[int, int], float]) -> Dict[str, float]:
    return derive_market_probs(matrix)


def _marginal_error(
    matrix: Dict[Tuple[int, int], float],
    targets: Dict[str, float],
) -> float:
    model = derive_market_probs(matrix)
    err = 0.0
    for k, target in targets.items():
        if k not in model:
            continue
        err += (model[k] - target) ** 2
    return err


def fit_lambdas_to_book_marginals(
    targets: Dict[str, float],
    *,
    config: Optional[PricingConfig] = None,
    lam_h0: float = 1.35,
    lam_a0: float = 1.15,
) -> Dict[str, Any]:
    """Grid-search λ_h, λ_a so model marginals best match de-vigged book marginals."""
    cfg = config or PricingConfig.from_env()
    if not targets:
        return {"ok": False, "error": "no targets"}

    best_err = float("inf")
    best = (lam_h0, lam_a0)
    for lam_h in [x * 0.1 for x in range(5, 61)]:  # 0.5 .. 6.0
        for lam_a in [x * 0.1 for x in range(5, 61)]:
            matrix = build_score_matrix(lam_h, lam_a, config=cfg)
            err = _marginal_error(matrix, targets)
            if err < best_err:
                best_err = err
                best = (lam_h, lam_a)

    lam_h, lam_a = best
    matrix = build_score_matrix(lam_h, lam_a, config=cfg)
    model = derive_market_probs(matrix)
    deltas = {k: round(model.get(k, 0.0) - targets.get(k, 0.0), 4) for k in targets}
    return {
        "ok": True,
        "lam_h": round(lam_h, 3),
        "lam_a": round(lam_a, 3),
        "model_marginals": model,
        "target_marginals": targets,
        "deltas": deltas,
        "rmse": round(best_err ** 0.5, 4),
    }
