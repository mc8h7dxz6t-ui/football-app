"""Forward-only B2B evidence gates (post-deploy snapshots — not backtest headlines)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from hibs_predictor.evidence_presentation import buyer_readiness_bundle, gate_row

# Institutional scorecard thresholds (docs/INSTITUTIONAL_SCORECARD.md)
CAPTURE_PASS_PCT = 50.0
CAPTURE_SCORED_PASS_PCT = 80.0
CLV_MIN_ROWS = 25
CLV_BEAT_PASS_PCT = 50.0
CLV_VALUE_BEAT_PASS_PCT = 50.0
MU_CLV_PASS = 0.0
MIN_MATCHDAYS_BEFORE_CAPTURE_GATE = 3

_INFORMATIONAL_GATE_IDS = frozenset(
    {
        "F9b_clv_beat_close_fair_shin",
        "F9c_clv_value_beat_close",
        "F9d_mu_clv_value",
    }
)


def evidence_deploy_since_iso() -> Optional[str]:
    """ISO timestamp for forward evidence window (env overrides deploy revision file)."""
    load_dotenv()
    explicit = (os.getenv("HIBS_EVIDENCE_DEPLOY_DATE") or "").strip()
    if explicit:
        if "T" not in explicit:
            explicit = f"{explicit}T00:00:00+00:00"
        return explicit
    try:
        from hibs_predictor.deploy_info import gather_deploy_info

        deployed = (gather_deploy_info().get("deployed_at") or "").strip()
        return deployed or None
    except Exception:
        return None


def forward_evidence_gates() -> Dict[str, Any]:
    """
    Pass/fail gates for buyer data room — forward windows only.

    Uses rolling 7d for capture (scorecard F7) and 28d for CLV population (F8/F9).
  When ``since_deploy`` is set, CLV/capture also report post-deploy slices.
    """
    load_dotenv()
    from hibs_predictor.prediction_log import (
        _clv_enabled,
        _enabled as audit_enabled,
        audit_odds_capture_stats,
        clv_beat_close_summary,
        clv_value_beat_close_summary,
        pred_log_sync_cron_status,
    )

    since = evidence_deploy_since_iso()
    gates: List[Dict[str, Any]] = []

    audit_on = audit_enabled()
    clv_on = _clv_enabled()
    cron = pred_log_sync_cron_status()

    gates.append(
        gate_row(
            "F1_audit",
            label="Prediction audit enabled",
            passed=audit_on,
            actual=audit_on,
            threshold="enabled",
            message="Set HIBS_PREDICTION_LOG_ENABLED=1",
            critical=True,
        )
    )
    gates.append(
        gate_row(
            "F2_clv",
            label="CLV logging enabled",
            passed=clv_on,
            actual=clv_on,
            threshold="enabled",
            message="Set HIBS_CLV_LOG_ENABLED=1",
            critical=True,
        )
    )
    cron_ok = bool(cron.get("scheduled"))
    gates.append(
        gate_row(
            "F3_cron",
            label="Daily pred-log-sync cron",
            passed=cron_ok,
            actual=cron.get("scheduled"),
            threshold="scheduled=true",
            message=cron.get("message", "Install deploy/cron-hibs-calibration.sh --install"),
            critical=True,
        )
    )

    cap_7d = audit_odds_capture_stats(days=7)
    matchdays_7d = _count_matchdays(days=7, since_iso=since)
    capture_pct = cap_7d.get("capture_rate_pct")
    capture_gate_active = matchdays_7d >= MIN_MATCHDAYS_BEFORE_CAPTURE_GATE
    capture_pass = (
        capture_gate_active
        and capture_pct is not None
        and float(capture_pct) >= CAPTURE_PASS_PCT
    )
    if not capture_gate_active:
        cap_msg = (
            f"Need ≥{MIN_MATCHDAYS_BEFORE_CAPTURE_GATE} matchdays with snapshots in 7d "
            f"(have {matchdays_7d}). Load dashboard while logged in to seed snapshots."
        )
    elif capture_pct is None:
        cap_msg = cap_7d.get("message", "No snapshots in 7d window.")
    elif capture_pass:
        cap_msg = "Forward 7d 1X2 capture meets B2B gate."
    else:
        cap_msg = (
            "Run scripts/run_forward_backfill_plan.sh or load dashboard during fixture window; "
            "ensure ensure_snapshot_odds on capture."
        )
    gates.append(
        gate_row(
            "F7_forward_capture_7d",
            label="Forward 1X2 odds capture (7d)",
            passed=capture_pass,
            actual=capture_pct,
            threshold=f">={CAPTURE_PASS_PCT}% after {MIN_MATCHDAYS_BEFORE_CAPTURE_GATE} matchdays",
            message=cap_msg,
            critical=False,
            n=int(cap_7d.get("n_snapshots") or 0),
            window="7d",
            coverage_pct=float(capture_pct) if capture_pct is not None else None,
        )
    )

    cap_28d = audit_odds_capture_stats(days=28, since_iso=since)
    scored_pct = cap_28d.get("scored_capture_rate_pct")
    scored_pass = scored_pct is not None and float(scored_pct) >= CAPTURE_SCORED_PASS_PCT
    gates.append(
        gate_row(
            "F7b_scored_capture_since_deploy",
            label="Scored-row 1X2 capture (since deploy)",
            passed=scored_pass if since else False,
            actual=scored_pct,
            threshold=f">={CAPTURE_SCORED_PASS_PCT}% scored rows",
            message=(
                "Historic hole excluded when HIBS_EVIDENCE_DEPLOY_DATE or .deploy-revision set."
                if since
                else "Set HIBS_EVIDENCE_DEPLOY_DATE for since-deploy slice."
            ),
            n=int(cap_28d.get("n_scored") or 0),
            window="since_deploy",
            coverage_pct=float(scored_pct) if scored_pct is not None else None,
        )
    )

    clv_28d = clv_beat_close_summary(days=28, since_iso=since)
    clv_trial_28d = clv_beat_close_summary(days=28, since_iso=since, trial_leagues_only=True)
    n_clv = int(clv_28d.get("n_clv_rows") or 0)
    beat = clv_28d.get("beat_close_pct")
    gates.append(
        gate_row(
            "F8_clv_sample",
            label="CLV sample size (28d forward)",
            passed=n_clv >= CLV_MIN_ROWS,
            actual=n_clv,
            threshold=f">={CLV_MIN_ROWS} rows",
            message=clv_28d.get("message", "Daily pred-log-sync after matches."),
            n=n_clv,
            window="28d",
        )
    )
    beat_pass = beat is not None and float(beat) >= CLV_BEAT_PASS_PCT and n_clv >= CLV_MIN_ROWS
    f9_msg = "Descriptive until F8 passes; not a staking guarantee."
    ci = clv_28d.get("beat_close_wilson_ci_95") or {}
    if isinstance(ci, dict) and ci.get("low_pct") is not None:
        f9_msg += (
            f" Wilson 95% CI [{ci['low_pct']}-{ci['high_pct']}%]"
            f" (point {beat}% — pass rule unchanged)."
        )
    med = clv_28d.get("median_clv_pp")
    if med is not None:
        f9_msg += f" Median CLV {med}pp."
    n_trial = int(clv_trial_28d.get("n_clv_rows") or 0)
    beat_trial = clv_trial_28d.get("beat_close_pct")
    if n_trial > 0 and beat_trial is not None:
        ci_trial = clv_trial_28d.get("beat_close_wilson_ci_95") or {}
        trial_note = f" Trial-league slice (info only): {beat_trial}% beat-close n={n_trial}."
        if isinstance(ci_trial, dict) and ci_trial.get("low_pct") is not None:
            trial_note += f" Wilson [{ci_trial['low_pct']}-{ci_trial['high_pct']}%]."
        f9_msg += trial_note
    gates.append(
        gate_row(
            "F9_clv_beat_close",
            label="CLV beat-close %",
            passed=beat_pass,
            actual=beat,
            threshold=f">={CLV_BEAT_PASS_PCT}% on >={CLV_MIN_ROWS} rows",
            message=f9_msg,
            n=n_clv,
            window="28d",
            coverage_pct=float(beat) if beat is not None else None,
        )
    )

    from hibs_predictor.price_truth import clv_beat_close_fair_summary

    clv_fair_shin = clv_beat_close_fair_summary(days=28, since_iso=since, method="shin")
    n_fair = int(clv_fair_shin.get("n_clv_rows") or 0)
    beat_fair = clv_fair_shin.get("beat_close_pct")
    f9b_msg = (
        "Informational only — Shin fair-line CLV from stored 1X2 triplets (no new API). "
        "F9 pass rule unchanged (raw implied)."
    )
    if n_fair > 0 and beat_fair is not None:
        f9b_msg += f" Fair-Shin beat-close {beat_fair}% n={n_fair}."
        ci_f = clv_fair_shin.get("beat_close_wilson_ci_95") or {}
        if isinstance(ci_f, dict) and ci_f.get("low_pct") is not None:
            f9b_msg += f" Wilson [{ci_f['low_pct']}-{ci_f['high_pct']}%]."
    gates.append(
        gate_row(
            "F9b_clv_beat_close_fair_shin",
            label="CLV beat-close % (Shin fair — informational)",
            passed=False,
            actual=beat_fair,
            threshold="informational — not buyer pass/fail",
            message=f9b_msg,
            n=n_fair,
            window="28d",
            coverage_pct=float(beat_fair) if beat_fair is not None else None,
            critical=False,
        )
    )

    clv_value_28d = clv_value_beat_close_summary(days=28, since_iso=since)
    n_value = int(clv_value_28d.get("n_clv_rows") or 0)
    beat_value = clv_value_28d.get("beat_close_pct")
    mu_clv_value = clv_value_28d.get("mu_clv")
    beat_value_pass = (
        beat_value is not None
        and float(beat_value) >= CLV_VALUE_BEAT_PASS_PCT
        and n_value >= CLV_MIN_ROWS
    )
    f9c_msg = "Scale cohort — value-flagged picks only; excludes predicted_outcome CLV fallback."
    if n_value and beat_value is not None:
        f9c_msg += f" {beat_value}% beat-close on {n_value} row(s)."
        if mu_clv_value is not None:
            f9c_msg += f" μ_CLV={mu_clv_value}."
    gates.append(
        gate_row(
            "F9c_clv_value_beat_close",
            label="Value-pick CLV beat-close % (scale cohort)",
            passed=beat_value_pass,
            actual=beat_value,
            threshold=f">={CLV_VALUE_BEAT_PASS_PCT}% on >={CLV_MIN_ROWS} value rows",
            message=f9c_msg,
            n=n_value,
            window="28d",
            coverage_pct=float(beat_value) if beat_value is not None else None,
            critical=False,
        )
    )
    mu_pass = (
        mu_clv_value is not None
        and float(mu_clv_value) > MU_CLV_PASS
        and n_value >= CLV_MIN_ROWS
    )
    gates.append(
        gate_row(
            "F9d_mu_clv_value",
            label="Value-pick μ_CLV (log-odds vs fair close)",
            passed=mu_pass,
            actual=mu_clv_value,
            threshold=f">{MU_CLV_PASS} on >={CLV_MIN_ROWS} value rows",
            message="Institutional portfolio edge — margin-lift fair closing odds.",
            n=n_value,
            window="28d",
            critical=False,
        )
    )

    critical = [g for g in gates if g.get("critical")]
    evidence = [
        g
        for g in gates
        if not g.get("critical") and g.get("id") not in _INFORMATIONAL_GATE_IDS
    ]
    critical_pass = all(g["pass"] for g in critical)
    evidence_pass = all(g["pass"] for g in evidence)
    grade = _evidence_grade(critical_pass, evidence_pass, gates)
    readiness = buyer_readiness_bundle(
        gates=gates,
        critical_pass=critical_pass,
        evidence_pass=evidence_pass,
        vertical="football",
    )

    return {
        "since_deploy_iso": since,
        "matchdays_7d": matchdays_7d,
        "odds_capture_7d": cap_7d,
        "odds_capture_28d_since_deploy": cap_28d,
        "clv_28d_since_deploy": clv_28d,
        "clv_trial_28d_since_deploy": clv_trial_28d,
        "clv_fair_shin_28d_since_deploy": clv_fair_shin,
        "clv_value_28d_since_deploy": clv_value_28d,
        "mu_clv_value": mu_clv_value,
        "gates": gates,
        "critical_pass": critical_pass,
        "evidence_pass": evidence_pass,
        "evidence_grade": grade,
        "next_actions": _next_actions(gates, since),
        **readiness,
    }


def _evidence_grade(critical_pass: bool, evidence_pass: bool, gates: List[Dict[str, Any]]) -> str:
    if not critical_pass:
        return "D"
    if evidence_pass:
        return "A"
    passed = sum(1 for g in gates if g["pass"])
    ratio = passed / max(len(gates), 1)
    if ratio >= 0.85:
        return "B+"
    if ratio >= 0.7:
        return "B"
    if ratio >= 0.55:
        return "C+"
    return "C"


def _next_actions(gates: List[Dict[str, Any]], since: Optional[str]) -> List[str]:
    actions: List[str] = []
    by_id = {g["id"]: g for g in gates}
    if not by_id.get("F3_cron", {}).get("pass"):
        actions.append("sudo bash deploy/cron-hibs-calibration.sh --install")
    if not by_id.get("F7_forward_capture_7d", {}).get("pass"):
        actions.append("bash scripts/run_forward_backfill_plan.sh")
        actions.append("Load dashboard while logged in during fixture days (seeds snapshots).")
    if not by_id.get("F8_clv_sample", {}).get("pass"):
        actions.append("Wait for matchdays + daily pred-log-sync; do not scale stakes until n≥25.")
    if not since:
        actions.append("Set HIBS_EVIDENCE_DEPLOY_DATE in .env (or deploy via link_production.sh for .deploy-revision).")
    if not actions:
        actions.append("Export data room: bash scripts/export_b2b_data_room.sh")
    return actions


def _count_matchdays(*, days: int, since_iso: Optional[str]) -> int:
    """Distinct UTC kickoff dates with ≥1 snapshot in window."""
    from hibs_predictor.prediction_log import _db_path, _enabled, init_db

    if not _enabled() or not os.path.isfile(_db_path()):
        return 0
    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
    if since_iso and since_iso > cutoff:
        cutoff = since_iso
    import sqlite3

    conn = sqlite3.connect(_db_path(), timeout=20)
    try:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT substr(kickoff_iso, 1, 10)) AS n
            FROM prediction_snapshots
            WHERE captured_at >= ? AND kickoff_iso IS NOT NULL AND kickoff_iso != ''
            """,
            (cutoff,),
        ).fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        conn.close()
