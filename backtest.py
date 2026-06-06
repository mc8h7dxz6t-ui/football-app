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


def implied_probs_1x2(odds_home: float, odds_draw: float, odds_away: float) -> Optional[Dict[str, float]]:
    """De-vigged market probabilities from 1X2 decimal odds (None if any missing)."""
    legs = (odds_home, odds_draw, odds_away)
    if any((o is None) or float(o) <= 1.0 for o in legs):
        return None
    inv = {"Home": 1.0 / odds_home, "Draw": 1.0 / odds_draw, "Away": 1.0 / odds_away}
    overround = sum(inv.values())
    if overround <= 0:
        return None
    return {k: v / overround for k, v in inv.items()}


def brier_score_1x2(records: Sequence[Dict[str, Any]], *, field: str = "probs") -> Optional[float]:
    """Mean multiclass Brier score (0 best, 2 worst). None if no usable records."""
    rows = [r for r in records if r.get(field)]
    if not rows:
        return None
    total = 0.0
    for rec in rows:
        p = _normalised(rec[field])
        y = rec["outcome"]
        total += sum((p[k] - (1.0 if k == y else 0.0)) ** 2 for k in OUTCOMES)
    return total / len(rows)


def log_loss_1x2(records: Sequence[Dict[str, Any]], *, field: str = "probs") -> Optional[float]:
    """Mean negative log-likelihood of the realised outcome. None if no usable records."""
    rows = [r for r in records if r.get(field)]
    if not rows:
        return None
    total = 0.0
    for rec in rows:
        p = _normalised(rec[field])
        total += -math.log(min(max(p[rec["outcome"]], _LOG_CLIP), 1.0))
    return total / len(rows)


def top_pick_accuracy(records: Sequence[Dict[str, Any]], *, field: str = "probs") -> Optional[float]:
    """Fraction where the highest-probability outcome was the realised one (%)."""
    rows = [r for r in records if r.get(field)]
    if not rows:
        return None
    hits = 0
    for rec in rows:
        p = _normalised(rec[field])
        if max(OUTCOMES, key=lambda k: p[k]) == rec["outcome"]:
            hits += 1
    return 100.0 * hits / len(rows)


def calibration_table(records: Sequence[Dict[str, Any]], *, bins: int = 10, field: str = "probs") -> List[Dict[str, Any]]:
    """Reliability table on the top-pick probability: predicted vs realised per bin."""
    buckets: List[Dict[str, Any]] = [
        {"bin_lo": i / bins, "bin_hi": (i + 1) / bins, "n": 0, "pred_sum": 0.0, "hits": 0}
        for i in range(bins)
    ]
    for rec in records:
        if not rec.get(field):
            continue
        p = _normalised(rec[field])
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


def evaluate(records: Sequence[Dict[str, Any]], *, bins: int = 10, field: str = "probs") -> Dict[str, Any]:
    """Headline calibration summary for a set of settled predictions."""
    n = len([r for r in records if r.get(field)])
    brier = brier_score_1x2(records, field=field)
    return {
        "n": n,
        "brier_score": round(brier, 4) if brier is not None else None,
        "log_loss": round(log_loss_1x2(records, field=field), 4) if n else None,
        "top_pick_accuracy_pct": round(top_pick_accuracy(records, field=field), 2) if n else None,
        # Uniform 1/3-1/3-1/3 baseline Brier is 0.667; beating it shows real signal.
        "uniform_baseline_brier": 0.6667,
        "calibration": calibration_table(records, bins=bins, field=field),
    }


def evaluate_vs_market(records: Sequence[Dict[str, Any]], *, bins: int = 10) -> Dict[str, Any]:
    """Model vs de-vigged market on the SAME fixtures (records carry market_probs).

    The market is the real benchmark; beating its Brier/log loss is the bar that
    matters. Only fixtures with market odds are scored on both sides.
    """
    paired = [r for r in records if r.get("probs") and r.get("market_probs")]
    model = evaluate(paired, bins=bins, field="probs")
    market = evaluate(paired, bins=bins, field="market_probs")
    verdict = None
    if model["brier_score"] is not None and market["brier_score"] is not None:
        verdict = "model beats market" if model["brier_score"] < market["brier_score"] else "market beats model"
    return {
        "n_paired": len(paired),
        "model": model,
        "market": market,
        "brier_delta_vs_market": (
            round(model["brier_score"] - market["brier_score"], 4)
            if model["brier_score"] is not None and market["brier_score"] is not None
            else None
        ),
        "verdict": verdict,
    }


def roi_backtest(bets: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Flat/seeded staking P&L. Each bet: {"won": bool, "odds": float, "stake": float}."""
    n = len(bets)
    staked = sum(float(b.get("stake", 1.0)) for b in bets)
    returned = sum(float(b["stake"]) * float(b["odds"]) for b in bets if b.get("won"))
    wins = sum(1 for b in bets if b.get("won"))
    pnl = returned - staked
    return {
        "bets": n,
        "wins": wins,
        "hit_rate_pct": round(100.0 * wins / n, 2) if n else None,
        "staked": round(staked, 2),
        "pnl_units": round(pnl, 3),
        "roi_pct": round(100.0 * pnl / staked, 2) if staked > 0 else None,
    }
