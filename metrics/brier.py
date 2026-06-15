"""Multi-class Brier and log loss — macro per event, variable runner fields."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

_LOG_CLIP = 1e-12


def normalize_probs(probs: Dict[str, float], keys: Optional[Sequence[str]] = None) -> Dict[str, float]:
    """Non-negative normalize; missing keys treated as 0."""
    if keys is None:
        keys = tuple(probs.keys())
    vals = {k: max(float(probs.get(k, 0.0)), 0.0) for k in keys}
    total = sum(vals.values()) or 1.0
    return {k: v / total for k, v in vals.items()}


def brier_multiclass_event(
    probs: Dict[str, float],
    outcome: str,
    *,
    keys: Optional[Sequence[str]] = None,
) -> float:
    """Brier score for one event (lower is better)."""
    if keys is None:
        keys = tuple(probs.keys())
    p = normalize_probs(probs, keys)
    return sum((p[k] - (1.0 if k == outcome else 0.0)) ** 2 for k in keys)


def brier_macro(
    records: Sequence[Dict[str, Any]],
    *,
    prob_field: str = "probs",
    outcome_field: str = "outcome",
    keys: Optional[Sequence[str]] = None,
) -> Optional[float]:
    """Mean Brier over events (macro). None if no usable records."""
    rows = [r for r in records if r.get(prob_field) and r.get(outcome_field) is not None]
    if not rows:
        return None
    total = 0.0
    for rec in rows:
        p = rec[prob_field]
        outcome = rec[outcome_field]
        k = keys if keys is not None else tuple(p.keys())
        total += brier_multiclass_event(p, outcome, keys=k)
    return total / len(rows)


def log_loss_event(
    probs: Dict[str, float],
    outcome: str,
    *,
    keys: Optional[Sequence[str]] = None,
) -> float:
    if keys is None:
        keys = tuple(probs.keys())
    p = normalize_probs(probs, keys)
    return -math.log(min(max(p[outcome], _LOG_CLIP), 1.0))


def log_loss_macro(
    records: Sequence[Dict[str, Any]],
    *,
    prob_field: str = "probs",
    outcome_field: str = "outcome",
    keys: Optional[Sequence[str]] = None,
) -> Optional[float]:
    rows = [r for r in records if r.get(prob_field) and r.get(outcome_field) is not None]
    if not rows:
        return None
    total = 0.0
    for rec in rows:
        p = rec[prob_field]
        total += log_loss_event(p, rec[outcome_field], keys=keys)
    return total / len(rows)


def brier_race(
    runner_probs: Sequence[float],
    outcomes: Sequence[int],
) -> Optional[float]:
    """Per-race Brier: (1/R) * sum (f_i - o_i)^2 for binary outcome vectors.

    ``outcomes`` are 0/1 (win or placed). Lengths must match and be > 0.
    """
    if len(runner_probs) != len(outcomes) or not runner_probs:
        return None
    r = len(runner_probs)
    return sum((float(f) - float(o)) ** 2 for f, o in zip(runner_probs, outcomes)) / r


def uniform_baseline_brier(n_classes: int) -> float:
    """Brier for uniform 1/n predictions (multiclass)."""
    if n_classes < 2:
        return 0.0
    p = 1.0 / n_classes
    return (n_classes - 1) * p * p + (1 - p) ** 2
