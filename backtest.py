"""Pure calibration / backtest metrics for 1X2 predictions (no Streamlit / no I/O).

Football-facing facade over ``metrics/`` — institutional Brier, Murphy, data room.
A "record" is ``{"probs": {"Home": .., "Draw": .., "Away": ..}, "outcome": "Home"|"Draw"|"Away"}``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from metrics.brier import brier_macro, log_loss_macro, normalize_probs
from metrics.calibration import calibration_table_top_pick
from metrics.data_room import build_data_room_export, evaluate_multiclass

OUTCOMES = ("Home", "Draw", "Away")


def settle_1x2(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "Home"
    if home_goals < away_goals:
        return "Away"
    return "Draw"


def _normalised(probs: Dict[str, float]) -> Dict[str, float]:
    return normalize_probs(probs, OUTCOMES)


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
    return brier_macro(records, prob_field=field, outcome_field="outcome", keys=OUTCOMES)


def log_loss_1x2(records: Sequence[Dict[str, Any]], *, field: str = "probs") -> Optional[float]:
    return log_loss_macro(records, prob_field=field, outcome_field="outcome", keys=OUTCOMES)


def top_pick_accuracy(records: Sequence[Dict[str, Any]], *, field: str = "probs") -> Optional[float]:
    rows = [r for r in records if r.get(field)]
    if not rows:
        return None
    hits = 0
    for rec in rows:
        p = _normalised(rec[field])
        if max(OUTCOMES, key=lambda k: p[k]) == rec["outcome"]:
            hits += 1
    return 100.0 * hits / len(rows)


def calibration_table(
    records: Sequence[Dict[str, Any]], *, bins: int = 10, field: str = "probs"
) -> List[Dict[str, Any]]:
    return calibration_table_top_pick(records, bins=bins, prob_field=field, outcome_field="outcome", keys=OUTCOMES)


def evaluate(records: Sequence[Dict[str, Any]], *, bins: int = 10, field: str = "probs") -> Dict[str, Any]:
    """Headline calibration summary for a set of settled predictions."""
    full = evaluate_multiclass(
        records, prob_field=field, outcome_field="outcome", keys=OUTCOMES, bins=bins
    )
    return {
        "n": full["n"],
        "brier_score": full["brier_score"],
        "log_loss": full["log_loss"],
        "top_pick_accuracy_pct": round(top_pick_accuracy(records, field=field), 2) if full["n"] else None,
        "uniform_baseline_brier": full["uniform_baseline_brier"],
        "calibration": full["calibration_top_pick"],
        "murphy": full["murphy"],
        "calibration_all_legs": full["calibration_all_legs"],
    }


def evaluate_vs_market(records: Sequence[Dict[str, Any]], *, bins: int = 10) -> Dict[str, Any]:
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


def export_data_room(
    records: Sequence[Dict[str, Any]],
    *,
    min_events: int = 1000,
    oos_only: bool = True,
    oos_declared: bool = True,
    train_cutoff: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Football 1X2 institutional data room export."""
    return build_data_room_export(
        product="football",
        target_kind="1x2",
        records=records,
        prob_field="probs",
        market_field="market_probs",
        outcome_field="outcome",
        keys=OUTCOMES,
        min_events=min_events,
        oos_only=oos_only,
        oos_declared=oos_declared,
        train_cutoff=train_cutoff,
        extra=extra,
    )


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
