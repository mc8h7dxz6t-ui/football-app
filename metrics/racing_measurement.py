"""Institutional++ measurement contract — place vs place, OOS, paired benchmark."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from metrics.racing_emit import decimal_to_implied_prob, normalize_race_probs

# Sanity band for independent place probabilities summed across a field.
PLACE_PROB_SUM_MIN = 0.8
PLACE_PROB_SUM_MAX = 6.0

RACE_DATE_COLS = ("card_date", "race_date", "meeting_date")
ODDS_SOURCE_COLS = ("odds_source", "source")
CONFIG_HASH_COLS = ("config_hash",)


def clamp_probability(p: float) -> float:
    return min(max(float(p), 0.0), 1.0)


def model_probs_for_export(
    target: str,
    raw: Sequence[float],
    *,
    model_col: Optional[str] = None,
) -> List[float]:
    """Place: independent P(place) per runner (no field normalize). Win: field normalize."""
    vals: List[float] = []
    for v in raw:
        try:
            vals.append(float(v) if v is not None else 0.0)
        except (TypeError, ValueError):
            vals.append(0.0)
    if target == "place":
        return [clamp_probability(v) for v in vals]
    return normalize_race_probs(vals) if any(vals) else [0.0] * len(vals)


def market_prob_for_target(
    target: str,
    *,
    place_decimal: Any = None,
    win_decimal: Any = None,
) -> Tuple[Optional[float], Optional[str]]:
    """Return (probability, source_label). Place target uses place odds only."""
    if target == "place":
        p = decimal_to_implied_prob(place_decimal)
        return (p, "offered_place_decimal" if p is not None else None)
    p = decimal_to_implied_prob(win_decimal)
    if p is not None:
        return (p, "win_decimal")
    p = decimal_to_implied_prob(place_decimal)
    return (p, "place_decimal_fallback" if p is not None else None)


def runners_fully_paired(runners: Sequence[Dict[str, Any]]) -> bool:
    return bool(runners) and all(r.get("market_prob") is not None for r in runners)


def filter_races_after_cutoff(
    records: Sequence[Dict[str, Any]],
    train_cutoff: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Keep races with race_date strictly after train_cutoff (OOS window)."""
    cutoff = (train_cutoff or "").strip()[:10]
    if not cutoff:
        return list(records), {
            "oos_enforced": False,
            "train_cutoff": None,
            "excluded_on_or_before_cutoff": 0,
            "excluded_missing_race_date": 0,
        }
    kept: List[Dict[str, Any]] = []
    excluded_date = 0
    missing_date = 0
    for rec in records:
        d = rec.get("race_date") or rec.get("card_date")
        if not d:
            missing_date += 1
            continue
        day = str(d)[:10]
        if day <= cutoff:
            excluded_date += 1
        else:
            kept.append(rec)
    return kept, {
        "oos_enforced": True,
        "train_cutoff": cutoff,
        "excluded_on_or_before_cutoff": excluded_date,
        "excluded_missing_race_date": missing_date,
        "n_after_cutoff": len(kept),
    }


def measurement_contract_summary(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Forensic stats over JSONL / export records."""
    if not records:
        return {"n_races": 0}
    n = len(records)
    paired_races = 0
    place_sums: List[float] = []
    win_fallback_legs = 0
    missing_meta = 0
    missing_race_date = 0
    legs = 0
    legs_with_market = 0

    for rec in records:
        if not rec.get("meta"):
            missing_meta += 1
        if not (rec.get("race_date") or rec.get("card_date")):
            missing_race_date += 1
        runners = rec.get("runners") or []
        if runners_fully_paired(runners):
            paired_races += 1
        if str(rec.get("target", "place")).lower() == "place":
            place_sums.append(sum(float(r.get("model_prob", 0)) for r in runners))
        for r in runners:
            legs += 1
            if r.get("market_prob") is not None:
                legs_with_market += 1
            meta = rec.get("meta") or {}
            if meta.get("market_prob_column") == "win_decimal":
                win_fallback_legs += 1

    out: Dict[str, Any] = {
        "n_races": n,
        "paired_races": paired_races,
        "paired_race_pct": round(100.0 * paired_races / n, 2) if n else 0.0,
        "runner_legs": legs,
        "runner_legs_with_market": legs_with_market,
        "runner_legs_market_pct": round(100.0 * legs_with_market / legs, 2) if legs else 0.0,
        "missing_race_date": missing_race_date,
        "missing_meta": missing_meta,
        "win_decimal_market_legs": win_fallback_legs,
    }
    if place_sums:
        out["model_prob_sum_per_race"] = {
            "min": round(min(place_sums), 4),
            "max": round(max(place_sums), 4),
            "mean": round(sum(place_sums) / len(place_sums), 4),
        }
        out["place_prob_sum_in_band"] = (
            PLACE_PROB_SUM_MIN <= out["model_prob_sum_per_race"]["mean"] <= PLACE_PROB_SUM_MAX
        )
    return out


def resolve_export_git_sha() -> Optional[str]:
    env = (os.environ.get("RACING_EXPORT_GIT_SHA") or "").strip()
    if env:
        return env[:12]
    try:
        root = Path(__file__).resolve().parents[1]
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(root),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()[:12]
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def build_export_meta(
    *,
    source_table: str,
    target: str,
    model_col: Optional[str],
    market_col: Optional[str],
    scored_at: Optional[str] = None,
    odds_source: Optional[str] = None,
    config_hash: Optional[str] = None,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "source_table": source_table,
        "target": target,
        "model_prob_column": model_col,
        "market_prob_column": market_col,
        "paired_benchmark_only": target == "place",
        "field_normalize_model_probs": target == "win",
    }
    sha = resolve_export_git_sha()
    if sha:
        meta["export_git_sha"] = sha
    if scored_at:
        meta["scored_at"] = str(scored_at)
    if odds_source:
        meta["odds_source"] = str(odds_source)
    if config_hash:
        meta["config_hash"] = str(config_hash)
    return meta
