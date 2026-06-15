"""Production readiness checks for football stack (audit, config, safety)."""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

_TRIAL_VALUE_LEAGUES = frozenset(
    {
        "EPL",
        "SCOTLAND",
        "UCL",
        "EUROPA_LEAGUE",
        "UECL",
        "LA_LIGA",
        "SERIE_A",
        "BUNDESLIGA",
        "LIGUE_1",
        "EREDIVISIE",
        "PRIMEIRA",
    }
)


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _parse_league_set(raw: str) -> frozenset[str]:
    return frozenset(x.strip().upper() for x in (raw or "").split(",") if x.strip())


def collect_config_issues(*, production: bool | None = None) -> Tuple[List[str], List[str]]:
    """
    Return (blocking_issues, warnings) for institutional deployment.

    Blocking issues should fail CI / production boot when strict validation is on.
    """
    load_dotenv()
    is_prod = _env_truthy("HIBS_PRODUCTION") if production is None else bool(production)
    issues: List[str] = []
    warnings: List[str] = []

    if is_prod and _env_truthy("HIBS_DEV_FULL_DQ"):
        issues.append("HIBS_DEV_FULL_DQ=1 is for local dev only — disable on production VPS.")

    if is_prod and _env_truthy("HIBS_FETCH_ALL_DOMESTIC"):
        warnings.append(
            "HIBS_FETCH_ALL_DOMESTIC=1 disables summer/WC fetch trim — use only on local dev."
        )

    if _env_truthy("HIBS_AUTH_ENABLED"):
        if not (os.getenv("HIBS_SECRET_KEY") or "").strip():
            issues.append("HIBS_SECRET_KEY is required when HIBS_AUTH_ENABLED=1.")
        from hibs_predictor.auth import password_configured

        if not password_configured():
            issues.append("HIBS_AUTH_PASSWORD is required when HIBS_AUTH_ENABLED=1.")
    elif is_prod:
        warnings.append("HIBS_AUTH_ENABLED=0 on production — dashboard is public.")

    try:
        from hibs_predictor.prediction_log import _enabled as audit_enabled

        if not audit_enabled():
            if is_prod:
                issues.append("HIBS_PREDICTION_LOG_ENABLED=0 — enable audit logging on production.")
            else:
                warnings.append("Prediction audit log is disabled.")
        elif is_prod and not _env_truthy("HIBS_CLV_LOG_ENABLED"):
            warnings.append("HIBS_CLV_LOG_ENABLED=0 — CLV beat-close reporting will be thin.")
    except Exception as exc:
        warnings.append(f"Could not read prediction log config: {exc!s}"[:120])

    raw_leagues = (os.getenv("HIBS_VALUE_LEAGUES") or "").strip()
    if is_prod and not raw_leagues:
        warnings.append("HIBS_VALUE_LEAGUES unset — value gates apply to all leagues in window.")
    elif raw_leagues:
        chosen = _parse_league_set(raw_leagues)
        extra = sorted(chosen - _TRIAL_VALUE_LEAGUES)
        missing = sorted(_TRIAL_VALUE_LEAGUES - chosen)
        if extra and is_prod:
            warnings.append(f"Value leagues include non-trial codes: {', '.join(extra[:8])}.")
        if missing and is_prod:
            warnings.append(f"Trial cohort missing leagues: {', '.join(missing[:8])}.")

    if is_prod:
        try:
            from hibs_predictor.historic_calibration import calibration_cache_path

            path = calibration_cache_path()
            if not os.path.isfile(path):
                warnings.append(
                    f"Calibration cache missing ({path}) — run: python -m hibs_predictor.main calibration-fit"
                )
        except Exception:
            pass

    if is_prod and not _env_truthy("HIBS_SHARPEN_GATES"):
        warnings.append("HIBS_SHARPEN_GATES=0 — trial sharpen profile not active.")

    if is_prod and _env_truthy("HIBS_FVE_INTEGRATION") and _env_truthy("FVE_PAUSED"):
        issues.append("FVE_PAUSED=1 with HIBS_FVE_INTEGRATION=1 — line shop offline on production.")

    try:
        from hibs_predictor.market_contract import validate_market_contracts

        mc_issues, mc_warnings, _mc_summary = validate_market_contracts()
        issues.extend(mc_issues)
        warnings.extend(mc_warnings)
    except Exception as exc:
        warnings.append(f"Market contract validation skipped: {exc!s}"[:120])

    return issues, warnings


def _evidence_gates() -> Dict[str, Any]:
    """Honest promotion gates (football); does not inflate grades."""
    gates: Dict[str, Any] = {}
    try:
        from hibs_predictor.prediction_log import backtest_report_dict, scale_readiness_dict

        bt = backtest_report_dict(days=120)
        m = bt.get("metrics") or {}
        gates["brier_120d"] = m.get("brier_score_1x2")
        gates["n_scored_120d"] = m.get("n_scored")
        gates["beats_baseline"] = m.get("beats_baseline")
        gates["scale_readiness"] = scale_readiness_dict()
        gates["backtest_limitations"] = bt.get("limitations", [])
    except Exception as exc:
        gates["error"] = str(exc)[:160]
    return gates


def readiness_dict() -> Dict[str, Any]:
    """Aggregate config + evidence for /api/health and CLI institutional-check."""
    load_dotenv()
    is_prod = _env_truthy("HIBS_PRODUCTION")
    issues, warnings = collect_config_issues(production=is_prod)

    try:
        from hibs_predictor.tournament_focus import tournament_focus_context

        tfc = tournament_focus_context()
    except Exception as exc:
        tfc = {"error": str(exc)[:120]}

    try:
        from hibs_predictor.league_profiles import pipeline_excluded_league_codes

        excluded = sorted(pipeline_excluded_league_codes())
    except Exception:
        excluded = []

    engineering_grade = "A"
    if issues:
        engineering_grade = "C" if len(issues) <= 2 else "D"
    elif len(warnings) > 4:
        engineering_grade = "B"
    elif warnings:
        engineering_grade = "B+"

    evidence = _evidence_gates()
    forward: Dict[str, Any] = {}
    market_contract: Dict[str, Any] = {}
    try:
        from hibs_predictor.market_contract import validate_market_contracts

        _mc_issues, _mc_warn, market_contract = validate_market_contracts()
        market_contract["valid"] = not _mc_issues
    except Exception as exc:
        market_contract = {"error": str(exc)[:160]}

    production_secure: Dict[str, Any] = {}
    try:
        from hibs_predictor.production_secure import production_secure_dict

        production_secure = production_secure_dict(probe_fve=_env_truthy("HIBS_FVE_INTEGRATION"))
    except Exception as exc:
        production_secure = {"error": str(exc)[:160]}

    try:
        from hibs_predictor.forward_evidence import forward_evidence_gates

        forward = forward_evidence_gates()
    except Exception as exc:
        forward = {"error": str(exc)[:160]}

    evidence_grade = str(forward.get("evidence_grade") or "C+")
    if forward.get("error"):
        evidence_grade = "D"

    football_score = forward.get("buyer_readiness_score")
    commercial_tier = forward.get("commercial_tier", "pilot_deployable")

    nine_ten: Dict[str, Any] = {}
    if _env_truthy("HIBS_NINE_TEN_INLINE"):
        try:
            from hibs_predictor.nine_ten_score import score_all

            nt = score_all()
            nine_ten = {
                "average": nt.get("average"),
                "pillars_at_9": nt.get("pillars_at_9"),
                "pillars_total": nt.get("pillars_total"),
                "institutional_ready": nt.get("institutional_ready"),
                "pillars": [
                    {
                        "id": p.get("id"),
                        "label": p.get("label"),
                        "score": p.get("score"),
                        "at_target": p.get("at_target"),
                    }
                    for p in (nt.get("pillars") or [])
                ],
            }
        except Exception as exc:
            nine_ten = {"error": str(exc)[:120]}
    else:
        nine_ten = {
            "deferred": True,
            "message": "Run ./scripts/score_hibs_nine_ten.sh (omit from hot /api/health path).",
        }

    return {
        "production_mode": is_prod,
        "as_of_utc": date.today().isoformat(),
        "engineering_grade": engineering_grade,
        "evidence_grade": evidence_grade,
        "buyer_ready": bool(forward.get("buyer_ready")),
        "buyer_readiness_score": football_score,
        "football_score": football_score,
        "commercial_tier": commercial_tier,
        "presentation_model": "telemetry → evidence → statistical interpretation → commercial tier",
        "nine_ten": nine_ten,
        "forward_evidence": forward,
        "overall_note": (
            "Forward B2B gates (7d capture, CLV n≥25) are separate from 120d backtest headlines. "
            "Commercial tier is pilot-deployable until evidence gates green — not sellable-as-proven-edge. "
            "See forward_evidence.gates and docs/B2B_OPERATOR_RUNBOOK.md."
        ),
        "blocking_issues": issues,
        "warnings": warnings,
        "tournament_focus": tfc,
        "pipeline_excluded_leagues": excluded,
        "market_contract": market_contract,
        "production_secure": production_secure,
        "evidence_gates": evidence,
        "trial_value_leagues": sorted(_TRIAL_VALUE_LEAGUES),
    }


def validate_production_config(*, strict: bool = True) -> List[str]:
    """Raise RuntimeError when strict and blocking issues exist."""
    issues, _warnings = collect_config_issues(production=True)
    if strict and issues:
        raise RuntimeError("Production config invalid: " + "; ".join(issues))
    return issues


def log_startup_readiness() -> None:
    """Log readiness summary at web boot (non-fatal)."""
    from hibs_predictor.app_logging import get_logger

    logger = get_logger("institutional")
    rep = readiness_dict()
    if rep.get("blocking_issues"):
        logger.warning(
            "Institutional readiness: %d blocking issue(s): %s",
            len(rep["blocking_issues"]),
            "; ".join(rep["blocking_issues"][:5]),
        )
    elif rep.get("warnings"):
        logger.info(
            "Institutional readiness: %d warning(s) — engineering_grade=%s",
            len(rep["warnings"]),
            rep.get("engineering_grade"),
        )
    else:
        logger.info(
            "Institutional readiness OK (engineering_grade=%s)",
            rep.get("engineering_grade"),
        )
