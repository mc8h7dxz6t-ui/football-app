"""Institutional data room export — shared schema for football and racing stacks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from metrics.brier import brier_macro, log_loss_macro, uniform_baseline_brier
from metrics.calibration import (
    calibration_table_all_legs,
    calibration_table_top_pick,
    pooled_forecast_outcomes,
)
from metrics.murphy import murphy_decomposition

SCHEMA_VERSION = "1.0"
DEFAULT_MIN_EVENTS = 1000


def evaluate_multiclass(
    records: Sequence[Dict[str, Any]],
    *,
    prob_field: str = "probs",
    outcome_field: str = "outcome",
    keys: Optional[Sequence[str]] = None,
    bins: int = 10,
) -> Dict[str, Any]:
    """Full institutional summary for fixed or variable multiclass events."""
    rows = [r for r in records if r.get(prob_field) and r.get(outcome_field) is not None]
    n = len(rows)
    n_classes = len(keys) if keys else (len(rows[0][prob_field]) if rows else 3)
    brier = brier_macro(rows, prob_field=prob_field, outcome_field=outcome_field, keys=keys)
    forecasts, outcomes = pooled_forecast_outcomes(
        rows, prob_field=prob_field, outcome_field=outcome_field, keys=keys
    )
    murphy = murphy_decomposition(forecasts, outcomes, bins=bins)
    return {
        "n": n,
        "brier_score": round(brier, 4) if brier is not None else None,
        "log_loss": round(log_loss_macro(rows, prob_field=prob_field, outcome_field=outcome_field, keys=keys), 4)
        if n
        else None,
        "uniform_baseline_brier": round(uniform_baseline_brier(n_classes), 4),
        "murphy": murphy,
        "calibration_top_pick": calibration_table_top_pick(
            rows, bins=bins, prob_field=prob_field, outcome_field=outcome_field, keys=keys
        ),
        "calibration_all_legs": calibration_table_all_legs(
            rows, bins=bins, prob_field=prob_field, outcome_field=outcome_field, keys=keys
        ),
    }


def institutional_gates(
    *,
    n_events: int,
    model_brier: Optional[float],
    market_brier: Optional[float],
    min_events: int = DEFAULT_MIN_EVENTS,
    max_brier_delta_vs_market: float = 0.0,
    oos_only: bool = True,
    oos_declared: bool = True,
    venue_mapped_pct: Optional[float] = None,
    min_venue_mapped_pct: float = 0.95,
    target_kind: str = "1x2",
) -> Dict[str, Any]:
    """Pass/fail gates for internal engineering vs institutional grade."""
    reasons: List[str] = []
    if n_events < min_events:
        reasons.append(f"n_events={n_events} < min_events={min_events}")
    if not oos_declared:
        reasons.append("oos_not_declared")
    if not oos_only:
        reasons.append("window_not_marked_oos")
    if model_brier is None:
        reasons.append("model_brier_missing")
    if market_brier is None:
        reasons.append("market_brier_missing")
    elif model_brier is not None and model_brier - market_brier > max_brier_delta_vs_market:
        reasons.append(
            f"model_brier_delta_vs_market={model_brier - market_brier:.4f} > {max_brier_delta_vs_market}"
        )
    if venue_mapped_pct is not None and venue_mapped_pct < min_venue_mapped_pct:
        reasons.append(
            f"venue_mapped_pct={venue_mapped_pct:.2%} < {min_venue_mapped_pct:.0%}"
        )
    if target_kind not in ("1x2", "win", "place"):
        reasons.append(f"unknown_target_kind={target_kind}")

    passed = len(reasons) == 0
    return {
        "institutional_grade": passed,
        "valuation_tier": "institutional_grade" if passed else "internal_engineering",
        "min_events": min_events,
        "reasons": reasons,
    }


def build_data_room_export(
    *,
    product: str,
    target_kind: str,
    records: Sequence[Dict[str, Any]],
    prob_field: str = "probs",
    market_field: str = "market_probs",
    outcome_field: str = "outcome",
    keys: Optional[Sequence[str]] = None,
    bins: int = 10,
    min_events: int = DEFAULT_MIN_EVENTS,
    oos_only: bool = True,
    oos_declared: bool = True,
    train_cutoff: Optional[str] = None,
    window_label: str = "rolling",
    extra: Optional[Dict[str, Any]] = None,
    venue_mapping: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build JSON-serialisable data room artifact (football 1X2 or racing win/place)."""
    model = evaluate_multiclass(
        records, prob_field=prob_field, outcome_field=outcome_field, keys=keys, bins=bins
    )
    paired = [r for r in records if r.get(prob_field) and r.get(market_field) and r.get(outcome_field)]
    market = evaluate_multiclass(
        paired, prob_field=market_field, outcome_field=outcome_field, keys=keys, bins=bins
    )
    delta = None
    if model["brier_score"] is not None and market["brier_score"] is not None:
        delta = round(model["brier_score"] - market["brier_score"], 4)

    venue_pct = None
    if venue_mapping and venue_mapping.get("n_races", 0) > 0:
        venue_pct = venue_mapping["n_mapped"] / venue_mapping["n_races"]

    gates = institutional_gates(
        n_events=model["n"],
        model_brier=model["brier_score"],
        market_brier=market["brier_score"] if paired else None,
        min_events=min_events,
        oos_only=oos_only,
        oos_declared=oos_declared,
        venue_mapped_pct=venue_pct,
        target_kind=target_kind,
    )

    export: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "product": product,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": {
            "kind": target_kind,
            "description": _target_description(target_kind),
        },
        "window": {
            "type": window_label,
            "min_events": min_events,
            "n_events": model["n"],
            "n_paired_market": len(paired),
            "oos_only": oos_only,
            "oos_declared": oos_declared,
            "train_cutoff": train_cutoff,
        },
        "model": model,
        "market": market if paired else {"n": 0, "brier_score": None},
        "delta_vs_market": {
            "brier_score": delta,
            "verdict": _verdict(delta),
        },
        "gates": gates,
    }
    if venue_mapping is not None:
        export["venue_mapping"] = venue_mapping
    if extra:
        export["meta"] = extra
    return export


def _target_description(kind: str) -> str:
    if kind == "1x2":
        return "Three-way football result (Home / Draw / Away)"
    if kind == "win":
        return "Single winner per race — multi-class Brier over runners"
    if kind == "place":
        return "Place finisher (top-k) — binary Brier per runner on P(place)"
    return kind


def _verdict(delta: Optional[float]) -> Optional[str]:
    if delta is None:
        return None
    if delta < 0:
        return "model_beats_market"
    if delta > 0:
        return "market_beats_model"
    return "tie"
