"""Calibration tables — top-pick (legacy) and all-legs (institutional)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from metrics.brier import normalize_probs


def calibration_table_top_pick(
    records: Sequence[Dict[str, Any]],
    *,
    bins: int = 10,
    prob_field: str = "probs",
    outcome_field: str = "outcome",
    keys: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Reliability on the highest-probability outcome per event."""
    buckets: List[Dict[str, Any]] = [
        {"bin_lo": i / bins, "bin_hi": (i + 1) / bins, "n": 0, "pred_sum": 0.0, "hits": 0}
        for i in range(bins)
    ]
    for rec in records:
        if not rec.get(prob_field):
            continue
        p = rec[prob_field]
        k = keys if keys is not None else tuple(p.keys())
        pn = normalize_probs(p, k)
        pick = max(k, key=lambda x: pn[x])
        conf = pn[pick]
        idx = min(int(conf * bins), bins - 1)
        b = buckets[idx]
        b["n"] += 1
        b["pred_sum"] += conf
        if pick == rec.get(outcome_field):
            b["hits"] += 1
    out: List[Dict[str, Any]] = []
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


def calibration_table_all_legs(
    records: Sequence[Dict[str, Any]],
    *,
    bins: int = 10,
    prob_field: str = "probs",
    outcome_field: str = "outcome",
    keys: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Institutional reliability: every predicted leg pooled into probability bins."""
    buckets: List[Dict[str, Any]] = [
        {"bin_lo": i / bins, "bin_hi": (i + 1) / bins, "n": 0, "pred_sum": 0.0, "hits": 0}
        for i in range(bins)
    ]
    for rec in records:
        if not rec.get(prob_field):
            continue
        p = rec[prob_field]
        outcome = rec.get(outcome_field)
        k = keys if keys is not None else tuple(p.keys())
        pn = normalize_probs(p, k)
        for leg in k:
            conf = pn[leg]
            idx = min(int(conf * bins), bins - 1)
            b = buckets[idx]
            b["n"] += 1
            b["pred_sum"] += conf
            if leg == outcome:
                b["hits"] += 1
    out: List[Dict[str, Any]] = []
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


def pooled_forecast_outcomes(
    records: Sequence[Dict[str, Any]],
    *,
    prob_field: str = "probs",
    outcome_field: str = "outcome",
    keys: Optional[Sequence[str]] = None,
) -> tuple[List[float], List[int]]:
    """Flatten events to (forecast, hit) pairs for Murphy decomposition."""
    forecasts: List[float] = []
    outcomes: List[int] = []
    for rec in records:
        if not rec.get(prob_field):
            continue
        p = rec[prob_field]
        y = rec.get(outcome_field)
        k = keys if keys is not None else tuple(p.keys())
        pn = normalize_probs(p, k)
        for leg in k:
            forecasts.append(pn[leg])
            outcomes.append(1 if leg == y else 0)
    return forecasts, outcomes
