"""
L1 price-truth mining from audit DB — no new APIs.

Uses stored opening/closing 1X2 triplets + model probabilities to compute:
- Shin / OR fair prices and fair-line CLV
- Log-odds α residuals (model vs fair)
- Cross-book disagreement and sharp-anchor coverage from persisted panels

F9 remains raw-implied beat-close; fair-line metrics are informational (F9b).
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple

from dotenv import load_dotenv

from hibs_predictor.odds_devig import fair_probs_from_odds, log_odds_alpha

FairMethod = Literal["shin", "or"]
_SIDES = ("home", "draw", "away")


def _decimal_odds_side(raw: Any) -> Optional[float]:
    try:
        v = float(raw)
        return v if v > 1.0 else None
    except (TypeError, ValueError):
        return None


def triplet_from_mapping(raw: Mapping[str, Any]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {s: None for s in _SIDES}
    if not isinstance(raw, dict):
        return out
    for side in _SIDES:
        out[side] = _decimal_odds_side(raw.get(side))
    return out


def triplet_complete(triplet: Mapping[str, Optional[float]]) -> bool:
    return all(_decimal_odds_side(triplet.get(s)) is not None for s in _SIDES)


def fair_1x2_from_triplet(
    triplet: Mapping[str, Optional[float]],
    *,
    method: FairMethod = "shin",
) -> Dict[str, float]:
    odds = {s: float(triplet[s]) for s in _SIDES if _decimal_odds_side(triplet.get(s)) is not None}
    if len(odds) < 3:
        return {}
    return fair_probs_from_odds(odds, method=method)


def fair_implied_for_outcome(
    triplet: Mapping[str, Optional[float]],
    outcome: str,
    *,
    method: FairMethod = "shin",
) -> Optional[float]:
    fair = fair_1x2_from_triplet(triplet, method=method)
    if not fair or outcome not in fair:
        return None
    return float(fair[outcome])


def compute_clv_fair_pp(
    open_triplet: Mapping[str, Optional[float]],
    close_triplet: Mapping[str, Optional[float]],
    outcome: str,
    *,
    method: FairMethod = "shin",
) -> Optional[float]:
    """CLV on de-vigged fair line (pp): fair_close − fair_open on picked outcome."""
    if not triplet_complete(open_triplet) or not triplet_complete(close_triplet):
        return None
    fo = fair_implied_for_outcome(open_triplet, outcome, method=method)
    fc = fair_implied_for_outcome(close_triplet, outcome, method=method)
    if fo is None or fc is None:
        return None
    return round((fc - fo) * 100.0, 2)


def model_probs_from_prediction(pred: Mapping[str, Any]) -> Dict[str, float]:
    raw = pred.get("probabilities") or pred.get("probabilities_pct") or {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, float] = {}
    for side in _SIDES:
        try:
            v = float(raw.get(side, 0))
        except (TypeError, ValueError):
            continue
        out[side] = v / 100.0 if v > 1.0 else v
    total = sum(out.values())
    if total <= 0 or len(out) < 3:
        return {}
    return {k: out[k] / total for k in out}


def log_odds_alpha_block(
    model_probs: Mapping[str, float],
    fair_probs: Mapping[str, float],
) -> Dict[str, Optional[float]]:
    return {
        side: log_odds_alpha(float(model_probs.get(side, 0)), float(fair_probs.get(side, 0)))
        if side in model_probs and side in fair_probs
        else None
        for side in _SIDES
    }


def _round_fair(fair: Dict[str, float]) -> Dict[str, float]:
    return {k: round(v, 5) for k, v in fair.items()}


def enrich_clv_price_truth(
    clv: Dict[str, Any],
    *,
    model_probs: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """
    Augment a CLV block with fair-line metrics (idempotent).

    Reads opening_odds_1x2 / closing_odds_1x2 already stored in audit rows.
    """
    if not isinstance(clv, dict):
        return clv
    opening = triplet_from_mapping(clv.get("opening_odds_1x2") or {})
    closing = triplet_from_mapping(clv.get("closing_odds_1x2") or {})
    outcome = clv.get("best_bet_outcome")

    pt: Dict[str, Any] = dict(clv.get("price_truth") or {})
    if triplet_complete(opening):
        pt["open_fair_or"] = _round_fair(fair_1x2_from_triplet(opening, method="or"))
        pt["open_fair_shin"] = _round_fair(fair_1x2_from_triplet(opening, method="shin"))
    if triplet_complete(closing):
        pt["close_fair_or"] = _round_fair(fair_1x2_from_triplet(closing, method="or"))
        pt["close_fair_shin"] = _round_fair(fair_1x2_from_triplet(closing, method="shin"))

    if outcome and triplet_complete(opening) and triplet_complete(closing):
        clv["clv_pp_fair_or"] = compute_clv_fair_pp(opening, closing, str(outcome), method="or")
        clv["clv_pp_fair_shin"] = compute_clv_fair_pp(opening, closing, str(outcome), method="shin")

    if model_probs and triplet_complete(opening):
        for method, key in (("shin", "open_fair_shin"), ("or", "open_fair_or")):
            fair = pt.get(key) or {}
            if fair:
                alphas = log_odds_alpha_block(model_probs, fair)
                pt[f"log_odds_alpha_{method}"] = alphas
                if outcome and alphas.get(str(outcome)) is not None:
                    pt[f"log_odds_alpha_{method}_best_bet"] = alphas[str(outcome)]

    if pt:
        clv["price_truth"] = pt

    try:
        from hibs_predictor.clv_institutional import enrich_clv_institutional_fields

        stake_outcome = clv.get("clv_stake_outcome") or clv.get("best_bet_outcome")
        stake_odds = clv.get("clv_stake_odds") or clv.get("best_bet_odds")
        if stake_odds is None and stake_outcome:
            stake_odds = opening.get(str(stake_outcome))
        if triplet_complete(closing) and stake_outcome and stake_odds:
            closing_raw = {s: closing.get(s) for s in _SIDES}
            enrich_clv_institutional_fields(
                clv,
                closing_raw,
                stake_outcome=str(stake_outcome),
                odds_taken=stake_odds,
            )
    except Exception:
        pass

    return clv


def attach_price_panel_to_prediction(fixture: Dict[str, Any], prediction: Dict[str, Any]) -> None:
    """Persist book panel + fair open lines on prediction_json (forward capture)."""
    for key in (
        "sharp_anchor_implied",
        "sharp_anchor_implied_shin",
        "best_odds_1x2",
        "best_odds_source",
        "all_bookmaker_odds",
        "odds_cross_book_max_implied_diff_pct",
        "odds_primary_source",
    ):
        val = fixture.get(key)
        if val is not None and prediction.get(key) is None:
            prediction[key] = val

    opening = triplet_from_mapping(prediction.get("bookmaker_odds") or {})
    if triplet_complete(opening):
        prediction.setdefault(
            "fair_1x2_open",
            {
                "or": _round_fair(fair_1x2_from_triplet(opening, method="or")),
                "shin": _round_fair(fair_1x2_from_triplet(opening, method="shin")),
            },
        )
        model = model_probs_from_prediction(prediction)
        if model:
            prediction.setdefault(
                "log_odds_alpha_open",
                {
                    "shin": log_odds_alpha_block(model, prediction["fair_1x2_open"]["shin"]),
                    "or": log_odds_alpha_block(model, prediction["fair_1x2_open"]["or"]),
                },
            )

    panel = prediction.get("all_bookmaker_odds")
    if isinstance(panel, list) and panel:
        prediction["bookmaker_panel_n"] = len(panel)
        pin = sum(
            1
            for row in panel
            if isinstance(row, dict) and "pinnacle" in str(row.get("bookmaker") or row.get("name") or "").lower()
        )
        prediction["bookmaker_panel_pinnacle_n"] = pin


def _db_path() -> str:
    load_dotenv()
    from hibs_predictor.project_paths import resolve_data_path

    return str(resolve_data_path("HIBS_PREDICTION_LOG_DB", "data/prediction_audit.sqlite"))


def _audit_rows(
    *,
    days: int = 365,
    since_iso: Optional[str] = None,
    scored_only: bool = True,
) -> List[Dict[str, Any]]:
    path = _db_path()
    if not os.path.isfile(path):
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
    if since_iso and since_iso > cutoff:
        cutoff = since_iso
    conn = sqlite3.connect(path, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        where = "kickoff_iso >= ?"
        params: List[Any] = [cutoff]
        if scored_only:
            where += " AND result_outcome IS NOT NULL AND result_outcome != ''"
        cur = conn.execute(
            f"""
            SELECT id, fixture_id, league_code, kickoff_iso,
                   prediction_json, enrich_summary_json, result_outcome
            FROM prediction_snapshots
            WHERE {where}
            """,
            tuple(params),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def price_truth_from_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        enrich = json.loads(row.get("enrich_summary_json") or "{}")
        pred = json.loads(row.get("prediction_json") or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(enrich, dict):
        return None
    clv = enrich.get("clv")
    if not isinstance(clv, dict):
        return None
    clv = enrich_clv_price_truth(dict(clv), model_probs=model_probs_from_prediction(pred))
    out: Dict[str, Any] = {
        "fixture_id": row.get("fixture_id"),
        "league_code": row.get("league_code"),
        "kickoff_iso": row.get("kickoff_iso"),
        "best_bet_outcome": clv.get("best_bet_outcome"),
        "clv_pp_raw": clv.get("clv_pp"),
        "clv_pp_fair_or": clv.get("clv_pp_fair_or"),
        "clv_pp_fair_shin": clv.get("clv_pp_fair_shin"),
        "price_truth": clv.get("price_truth"),
        "has_book_panel": bool(pred.get("all_bookmaker_odds")),
        "bookmaker_panel_n": pred.get("bookmaker_panel_n"),
    }
    pt = clv.get("price_truth") or {}
    for method in ("shin", "or"):
        ab = pt.get(f"log_odds_alpha_{method}_best_bet")
        if ab is not None:
            out[f"log_odds_alpha_{method}_best_bet"] = ab
    return out


def _wilson_interval(successes: int, n: int) -> Optional[Dict[str, float]]:
    if n <= 0:
        return None
    from hibs_predictor.prediction_log import wilson_score_interval

    lo, hi = wilson_score_interval(successes, n)
    if lo is None or hi is None:
        return None
    return {"low_pct": lo, "high_pct": hi}


def clv_beat_close_fair_summary(
    *,
    days: int = 28,
    since_iso: Optional[str] = None,
    method: FairMethod = "shin",
    trial_leagues_only: bool = False,
) -> Dict[str, Any]:
    """Rolling beat-close on fair-line CLV (informational — not F9 pass rule)."""
    field = f"clv_pp_fair_{method}"
    out: Dict[str, Any] = {
        "window_days": int(days),
        "since_iso": since_iso,
        "fair_method": method,
        "n_clv_rows": 0,
        "beat_close_pct": None,
        "avg_clv_pp": None,
        "median_clv_pp": None,
        "beat_close_wilson_ci_95": None,
        "trial_leagues_only": bool(trial_leagues_only),
        "note": "Informational F9b — F9 pass rule uses raw implied clv_pp only.",
    }
    rows = _audit_rows(days=days, since_iso=since_iso, scored_only=True)
    if trial_leagues_only:
        from hibs_predictor.institutional_readiness import _TRIAL_VALUE_LEAGUES

        trial = sorted(_TRIAL_VALUE_LEAGUES - {"WORLD_CUP", "INTL_FRIENDLIES"})
        trial_set = set(trial)
        rows = [r for r in rows if str(r.get("league_code") or "") in trial_set]

    n = beat = 0
    pp_sum = 0.0
    pp_vals: List[float] = []
    for row in rows:
        mined = price_truth_from_row(row)
        if not mined:
            continue
        pp = mined.get(field)
        if pp is None:
            continue
        try:
            pp_f = float(pp)
        except (TypeError, ValueError):
            continue
        n += 1
        pp_sum += pp_f
        pp_vals.append(pp_f)
        if pp_f > 0:
            beat += 1
    out["n_clv_rows"] = n
    if n:
        out["beat_close_pct"] = round(100.0 * beat / n, 2)
        out["avg_clv_pp"] = round(pp_sum / n, 2)
        ordered = sorted(pp_vals)
        mid = len(ordered) // 2
        out["median_clv_pp"] = (
            round(ordered[mid], 2)
            if len(ordered) % 2
            else round((ordered[mid - 1] + ordered[mid]) / 2.0, 2)
        )
        out["beat_close_wilson_ci_95"] = _wilson_interval(beat, n)
    return out


def log_odds_alpha_summary(
    *,
    days: int = 120,
    since_iso: Optional[str] = None,
    method: FairMethod = "shin",
) -> Dict[str, Any]:
    """Aggregate log-odds α on best-bet legs (opening fair vs model)."""
    key = f"log_odds_alpha_{method}_best_bet"
    alphas: List[float] = []
    for row in _audit_rows(days=days, since_iso=since_iso, scored_only=False):
        mined = price_truth_from_row(row)
        if not mined:
            continue
        a = mined.get(key)
        if a is None:
            continue
        try:
            alphas.append(float(a))
        except (TypeError, ValueError):
            continue
    n = len(alphas)
    out: Dict[str, Any] = {"n": n, "method": method, "mean_alpha": None, "t_stat": None}
    if not n:
        return out
    mean = sum(alphas) / n
    out["mean_alpha"] = round(mean, 5)
    if n >= 2:
        var = sum((x - mean) ** 2 for x in alphas) / (n - 1)
        if var > 0:
            se = math.sqrt(var / n)
            out["t_stat"] = round(mean / se, 3) if se > 0 else None
    return out


def panel_coverage_summary(*, days: int = 28, since_iso: Optional[str] = None) -> Dict[str, Any]:
    """Share of snapshots with persisted multi-book panel (forward L1 readiness)."""
    rows = _audit_rows(days=days, since_iso=since_iso, scored_only=False)
    n = len(rows)
    with_panel = with_pin = with_sharp = 0
    for row in rows:
        try:
            pred = json.loads(row.get("prediction_json") or "{}")
        except json.JSONDecodeError:
            continue
        panel = pred.get("all_bookmaker_odds")
        if isinstance(panel, list) and len(panel) >= 2:
            with_panel += 1
        if int(pred.get("bookmaker_panel_pinnacle_n") or 0) > 0:
            with_pin += 1
        if pred.get("sharp_anchor_implied") or pred.get("fair_1x2_open"):
            with_sharp += 1
    return {
        "window_days": days,
        "since_iso": since_iso,
        "n_snapshots": n,
        "panel_rate_pct": round(100.0 * with_panel / n, 2) if n else None,
        "pinnacle_panel_rate_pct": round(100.0 * with_pin / n, 2) if n else None,
        "sharp_or_fair_rate_pct": round(100.0 * with_sharp / n, 2) if n else None,
    }


def backfill_audit_price_truth(*, dry_run: bool = False, limit: Optional[int] = None) -> Dict[str, Any]:
    """
    Offline backfill: recompute price_truth on enrich_summary_json.clv from stored odds.

    No API calls — mines opening/closing triplets + prediction_json model probs.
    """
    path = _db_path()
    if not os.path.isfile(path):
        return {"updated": 0, "skipped": 0, "message": "no audit DB"}
    from hibs_predictor.prediction_log import init_db

    init_db()
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    updated = skipped = 0
    try:
        sql = """
            SELECT id, prediction_json, enrich_summary_json
            FROM prediction_snapshots
            WHERE enrich_summary_json IS NOT NULL
            ORDER BY id DESC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
        for row in rows:
            try:
                enrich = json.loads(row["enrich_summary_json"] or "{}")
                pred = json.loads(row["prediction_json"] or "{}")
            except json.JSONDecodeError:
                skipped += 1
                continue
            clv = enrich.get("clv")
            if not isinstance(clv, dict):
                skipped += 1
                continue
            new_clv = enrich_clv_price_truth(dict(clv), model_probs=model_probs_from_prediction(pred))
            attach_price_panel_to_prediction({}, pred)
            new_enrich = dict(enrich)
            new_enrich["clv"] = new_clv
            new_pred_json = json.dumps(pred, default=str)
            new_enrich_json = json.dumps(new_enrich, default=str)
            if new_enrich_json == row["enrich_summary_json"] and new_pred_json == row["prediction_json"]:
                skipped += 1
                continue
            if not dry_run:
                conn.execute(
                    """
                    UPDATE prediction_snapshots
                    SET enrich_summary_json = ?, prediction_json = ?
                    WHERE id = ?
                    """,
                    (new_enrich_json, new_pred_json, row["id"]),
                )
            updated += 1
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return {"updated": updated, "skipped": skipped, "dry_run": dry_run}


def run_price_truth_research(
    *,
    days: int = 28,
    since_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Full L1 price-truth report for data room / research suite."""
    load_dotenv()
    explicit = (os.getenv("HIBS_EVIDENCE_DEPLOY_DATE") or "").strip()
    if since_iso is None and explicit:
        since_iso = explicit if "T" in explicit else f"{explicit}T00:00:00+00:00"

    raw = clv_beat_close_fair_summary(days=days, since_iso=since_iso, method="shin")
    raw_or = clv_beat_close_fair_summary(days=days, since_iso=since_iso, method="or")
    from hibs_predictor.prediction_log import clv_beat_close_summary

    f9_raw = clv_beat_close_summary(days=days, since_iso=since_iso)

    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "since_iso": since_iso,
        "window_days": days,
        "f9_raw_implied": {
            "n_clv_rows": f9_raw.get("n_clv_rows"),
            "beat_close_pct": f9_raw.get("beat_close_pct"),
            "avg_clv_pp": f9_raw.get("avg_clv_pp"),
            "note": "Official F9 pass rule — unchanged.",
        },
        "f9b_fair_shin": raw,
        "f9b_fair_or": raw_or,
        "log_odds_alpha_shin": log_odds_alpha_summary(days=max(days, 120), since_iso=since_iso, method="shin"),
        "log_odds_alpha_or": log_odds_alpha_summary(days=max(days, 120), since_iso=since_iso, method="or"),
        "panel_coverage": panel_coverage_summary(days=days, since_iso=since_iso),
        "starlizard_gap_notes": [
            "Shin/OR fair CLV computed offline from stored 1X2 triplets — no new API.",
            "F9 pass rule remains raw implied; fair-line metrics are informational (F9b).",
            "Persist all_bookmaker_odds + sharp anchors on forward capture for panel steam.",
            "Next: Pinnacle-only close tag when closing odds response records book names.",
        ],
    }


def write_price_truth_report(report: Dict[str, Any], path: Optional[Path] = None) -> Path:
    out = path or (
        Path(__file__).resolve().parents[2] / "data" / "backtest_profiles" / "price_truth_research.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return out
