"""Pure calibration / backtest metrics for 1X2 predictions (no Streamlit / no I/O).

Brings the institutional discipline of *proving* a model to the football engine:
- Brier score and log loss vs realised outcomes,
- top-pick accuracy,
- a reliability (calibration) table.

A "record" is ``{"probs": {"Home": .., "Draw": .., "Away": ..}, "outcome": "Home"|"Draw"|"Away"}``.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

OUTCOMES = ("Home", "Draw", "Away")
_LOG_CLIP = 1e-12


def settle_1x2(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "Home"
    if home_goals < away_goals:
        return "Away"
    return "Draw"


def _normalised(probs: Dict[str, float]) -> Dict[str, float]:
    vals = {k: max(float(probs.get(k, 0.0)), 0.0) for k in OUTCOMES}
    total = sum(vals.values()) or 1.0
    return {k: v / total for k, v in vals.items()}


def brier_score_1x2(records: Sequence[Dict[str, Any]]) -> Optional[float]:
    """Mean multiclass Brier score (0 best, 2 worst). None if no records."""
    if not records:
        return None
    total = 0.0
    for rec in records:
        p = _normalised(rec["probs"])
        y = rec["outcome"]
        total += sum((p[k] - (1.0 if k == y else 0.0)) ** 2 for k in OUTCOMES)
    return total / len(records)


def log_loss_1x2(records: Sequence[Dict[str, Any]]) -> Optional[float]:
    """Mean negative log-likelihood of the realised outcome. None if no records."""
    if not records:
        return None
    total = 0.0
    for rec in records:
        p = _normalised(rec["probs"])
        total += -math.log(min(max(p[rec["outcome"]], _LOG_CLIP), 1.0))
    return total / len(records)


def top_pick_accuracy(records: Sequence[Dict[str, Any]]) -> Optional[float]:
    """Fraction where the highest-probability outcome was the realised one (%)."""
    if not records:
        return None
    hits = 0
    for rec in records:
        p = _normalised(rec["probs"])
        pick = max(OUTCOMES, key=lambda k: p[k])
        if pick == rec["outcome"]:
            hits += 1
    return 100.0 * hits / len(records)


def calibration_table(records: Sequence[Dict[str, Any]], *, bins: int = 10) -> List[Dict[str, Any]]:
    """Reliability table on the top-pick probability: predicted vs realised per bin."""
    buckets: List[Dict[str, Any]] = [
        {"bin_lo": i / bins, "bin_hi": (i + 1) / bins, "n": 0, "pred_sum": 0.0, "hits": 0}
        for i in range(bins)
    ]
    for rec in records:
        p = _normalised(rec["probs"])
        pick = max(OUTCOMES, key=lambda k: p[k])
        conf = p[pick]
        idx = min(int(conf * bins), bins - 1)
        b = buckets[idx]
        b["n"] += 1
        b["pred_sum"] += conf
        if pick == rec["outcome"]:
            b["hits"] += 1
    out = []
    for b in buckets:
        if b["n"] == 0:
            continue
        out.append(
            {
                "bin": f"{b['bin_lo']:.0%}-{b['bin_hi']:.0%}",
                "n": b["n"],
                "avg_predicted_pct": round(100.0 * b["pred_sum"] / b["n"], 2),
                "actual_pct": round(100.0 * b["hits"] / b["n"], 2),
            }
        )
    return out


def evaluate(records: Sequence[Dict[str, Any]], *, bins: int = 10) -> Dict[str, Any]:
    """Headline calibration summary for a set of settled predictions."""
    n = len(records)
    brier = brier_score_1x2(records)
    return {
        "n": n,
        "brier_score": round(brier, 4) if brier is not None else None,
        "log_loss": round(log_loss_1x2(records), 4) if n else None,
        "top_pick_accuracy_pct": round(top_pick_accuracy(records), 2) if n else None,
        # Uniform 1/3-1/3-1/3 baseline Brier is 0.667; beating it shows real signal.
        "uniform_baseline_brier": 0.6667,
        "calibration": calibration_table(records, bins=bins),
    }
