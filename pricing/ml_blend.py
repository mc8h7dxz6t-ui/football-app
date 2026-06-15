"""Optional ML 1X2 head blend + isotonic calibration (hibs-compatible JSON)."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from pricing.league_calibration import load_calibration


def _isotonic_apply(p: float, knots: Dict[str, Any]) -> float:
    xs = knots.get("x") or knots.get("X")
    ys = knots.get("y") or knots.get("Y")
    if not isinstance(xs, list) or not isinstance(ys, list) or len(xs) < 2 or len(xs) != len(ys):
        return p
    try:
        px = max(0.0, min(1.0, float(p)))
        x_vals = [float(x) for x in xs]
        y_vals = [float(y) for y in ys]
        if px <= x_vals[0]:
            return max(1e-6, min(1.0 - 1e-6, y_vals[0]))
        if px >= x_vals[-1]:
            return max(1e-6, min(1.0 - 1e-6, y_vals[-1]))
        for i in range(len(x_vals) - 1):
            if x_vals[i] <= px <= x_vals[i + 1]:
                span = x_vals[i + 1] - x_vals[i]
                if span <= 0:
                    return y_vals[i + 1]
                t = (px - x_vals[i]) / span
                return max(1e-6, min(1.0 - 1e-6, y_vals[i] * (1.0 - t) + y_vals[i + 1] * t))
    except (TypeError, ValueError):
        return p
    return p


def calibrate_ml_1x2(ml_probs: Dict[str, float]) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """Apply global isotonic knots from FVE calibration file when present."""
    raw = load_calibration()
    ml_cal = raw.get("ml_calibration") if isinstance(raw, dict) else None
    if not isinstance(ml_cal, dict):
        return ml_probs, {"applied": False}
    global_knots = ml_cal.get("global")
    if not isinstance(global_knots, dict):
        return ml_probs, {"applied": False}

    key_map = {"Home": "home", "Draw": "draw", "Away": "away", "home": "home", "draw": "draw", "away": "away"}
    out = dict(ml_probs)
    applied = False
    for src, dst in key_map.items():
        if src not in out:
            continue
        knots = global_knots.get(dst)
        if not isinstance(knots, dict):
            continue
        out[src] = _isotonic_apply(float(out[src]), knots)
        applied = True
    if not applied:
        return ml_probs, {"applied": False}
    total = sum(float(out.get(k, 0.0)) for k in ("Home", "Draw", "Away", "home", "draw", "away") if k in out)
    # normalize only standard keys present
    norm_keys = [k for k in ("Home", "Draw", "Away") if k in out]
    if not norm_keys:
        norm_keys = [k for k in ("home", "draw", "away") if k in out]
    t = sum(float(out[k]) for k in norm_keys)
    if t <= 0:
        return ml_probs, {"applied": False}
    for k in norm_keys:
        out[k] = max(1e-6, float(out[k]) / t)
    return out, {"applied": True, "source": "ml_calibration_global"}


def blend_1x2_heads(
    matrix_probs: Dict[str, float],
    ml_probs: Dict[str, float] | None,
    *,
    ml_weight: float = 0.33,
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """
    Blend Poisson-matrix 1X2 with optional ML head; O2.5/BTTS stay matrix-derived elsewhere.

    Returns full market dict with coherent goal markets from caller.
    """
    w = max(0.0, min(1.0, float(ml_weight)))
    if not ml_probs or w <= 0:
        return dict(matrix_probs), {"blended": False}

    ml_cal, dbg = calibrate_ml_1x2(ml_probs)
    m = {k: matrix_probs.get(k, 0.0) for k in ("Home", "Draw", "Away")}
    ml = {}
    for k in ("Home", "Draw", "Away"):
        lk = k if k in ml_cal else k.lower()
        if lk in ml_cal:
            ml[k] = float(ml_cal[lk])
    if len(ml) < 3:
        return dict(matrix_probs), {"blended": False, "reason": "incomplete_ml"}

    blended = {k: (1.0 - w) * m[k] + w * ml[k] for k in ("Home", "Draw", "Away")}
    t = sum(blended.values()) or 1.0
    out = dict(matrix_probs)
    for k in ("Home", "Draw", "Away"):
        out[k] = blended[k] / t
    dbg = {**dbg, "blended": True, "ml_weight": w}
    return out, dbg
