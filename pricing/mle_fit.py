"""MLE / Nelder–Mead fit of λ to multi-market book marginals."""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Tuple

from pricing.score_matrix import PricingConfig, build_score_matrix, derive_market_probs


def _neg_log_loss(targets: Dict[str, float], model: Dict[str, float], *, eps: float = 1e-9) -> float:
  loss = 0.0
  for k, t in targets.items():
    p = max(eps, min(1.0 - eps, float(model.get(k, eps))))
    t = max(eps, min(1.0 - eps, float(t)))
    loss -= t * math.log(p)
  return loss


def _objective(
    lam_h: float,
    lam_a: float,
    targets: Dict[str, float],
    cfg: PricingConfig,
) -> float:
    matrix = build_score_matrix(lam_h, lam_a, config=cfg)
    model = derive_market_probs(matrix)
    return _neg_log_loss(targets, model)


def nelder_mead(
    f: Callable[[float, float], float],
    x0: Tuple[float, float],
    *,
    max_iter: int = 120,
    tol: float = 1e-5,
) -> Tuple[float, float, float]:
    """2D Nelder–Mead (no scipy). Returns (lam_h, lam_a, final_loss)."""
    # Simplex: x0, x0+(step,0), x0+(0,step)
    step = 0.25
    v = [
        [float(x0[0]), float(x0[1])],
        [float(x0[0]) + step, float(x0[1])],
        [float(x0[0]), float(x0[1]) + step],
    ]
    vals = [f(v[0][0], v[0][1]), f(v[1][0], v[1][1]), f(v[2][0], v[2][1])]
    alpha, gamma, rho, sigma = 1.0, 2.0, 0.5, 0.5

    for _ in range(max_iter):
        order = sorted(range(3), key=lambda i: vals[i])
        v = [v[i] for i in order]
        vals = [vals[i] for i in order]
        if abs(vals[0] - vals[2]) < tol:
            break
        cx = (v[0][0] + v[1][0]) / 2.0
        cy = (v[0][1] + v[1][1]) / 2.0
        rx = cx + alpha * (cx - v[2][0])
        ry = cy + alpha * (cy - v[2][1])
        rx = max(0.35, min(5.5, rx))
        ry = max(0.35, min(5.5, ry))
        fr = f(rx, ry)
        if fr < vals[0]:
            ex = cx + gamma * (rx - cx)
            ey = cy + gamma * (ry - cy)
            ex = max(0.35, min(5.5, ex))
            ey = max(0.35, min(5.5, ey))
            fe = f(ex, ey)
            if fe < fr:
                v[2], vals[2] = [ex, ey], fe
            else:
                v[2], vals[2] = [rx, ry], fr
        elif fr < vals[1]:
            v[2], vals[2] = [rx, ry], fr
        else:
            cx2 = cx + rho * (v[2][0] - cx)
            cy2 = cy + rho * (v[2][1] - cy)
            cx2 = max(0.35, min(5.5, cx2))
            cy2 = max(0.35, min(5.5, cy2))
            fc = f(cx2, cy2)
            if fc < vals[2]:
                v[2], vals[2] = [cx2, cy2], fc
            else:
                for i in (1, 2):
                    v[i][0] = v[0][0] + sigma * (v[i][0] - v[0][0])
                    v[i][1] = v[0][1] + sigma * (v[i][1] - v[0][1])
                    vals[i] = f(v[i][0], v[i][1])

    best_i = min(range(3), key=lambda i: vals[i])
    return v[best_i][0], v[best_i][1], vals[best_i]


def fit_lambdas_mle(
    targets: Dict[str, float],
    *,
    config: PricingConfig,
    lam_h0: float = 1.35,
    lam_a0: float = 1.15,
) -> Dict[str, Any]:
    """MLE fit of (λ_h, λ_a) to de-vigged book marginals via Nelder–Mead."""
    if not targets:
        return {"ok": False, "error": "no targets", "method": "mle"}

    cfg = config
    def objective(lh: float, la: float) -> float:
        return _objective(lh, la, targets, cfg)

    lam_h, lam_a, loss = nelder_mead(objective, (lam_h0, lam_a0))
    matrix = build_score_matrix(lam_h, lam_a, config=cfg)
    model = derive_market_probs(matrix)
    deltas = {k: round(model.get(k, 0.0) - targets.get(k, 0.0), 4) for k in targets}
    return {
        "ok": True,
        "method": "mle_nelder_mead",
        "lam_h": round(lam_h, 4),
        "lam_a": round(lam_a, 4),
        "neg_log_loss": round(loss, 6),
        "model_marginals": model,
        "target_marginals": targets,
        "deltas": deltas,
        "rmse": round(math.sqrt(sum(d * d for d in deltas.values()) / max(len(deltas), 1)), 4),
    }
