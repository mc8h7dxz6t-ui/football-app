"""Production Secure — enforceable checks backing investor/deal claims.

Separates:
- **Engineering secure** (boot/deploy allowed)
- **Traffic safe** (FVE/worker health, budgets, backpressure telemetry)
- **Evidence honest** (buyer_ready, F9 — reported, not hidden)
- **Commercial claims allowed** (what you may say in a term sheet today)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

# Replacement-cost bands (GBP) — defensible rebuild estimate, NOT sale price.
REPLACEMENT_COST_GBP = {"low": 150_000, "mid": 250_000, "high": 400_000}
SALEABLE_TODAY_GBP = {
    "football_pilot": {"low": 5_000, "high": 15_000},
    "racing_pilot": {"low": 3_000, "high": 10_000},
    "bundle_pilot": {"low": 8_000, "high": 20_000},
    "code_sale_non_exclusive": {"low": 15_000, "high": 25_000},
}


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _http_json(url: str, *, timeout: float = 8.0) -> Tuple[bool, Any, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hibs-production-secure/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return True, json.loads(raw) if raw.strip() else {}, ""
    except urllib.error.HTTPError as exc:
        return False, None, f"HTTP {exc.code}"
    except Exception as exc:
        return False, None, str(exc)[:120]


def _check_row(
    check_id: str,
    *,
    label: str,
    passed: bool,
    detail: str = "",
    blocking: bool = True,
) -> Dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "pass": bool(passed),
        "detail": detail,
        "blocking": blocking,
    }


def production_secure_dict(*, probe_fve: bool = True) -> Dict[str, Any]:
    """Run all Production Secure checks; return JSON for /api/health and CLI."""
    load_dotenv()
    checks: List[Dict[str, Any]] = []
    blocking: List[str] = []
    warnings: List[str] = []

    # --- Engineering / config ---
    try:
        from hibs_predictor.institutional_readiness import collect_config_issues, readiness_dict

        issues, warns = collect_config_issues(production=_env_truthy("HIBS_PRODUCTION"))
        blocking.extend(issues)
        warnings.extend(warns)
        checks.append(
            _check_row(
                "eng_institutional_config",
                label="Institutional config (no blocking issues)",
                passed=not issues,
                detail="; ".join(issues[:3]) if issues else "ok",
            )
        )
    except Exception as exc:
        checks.append(
            _check_row(
                "eng_institutional_config",
                label="Institutional config",
                passed=False,
                detail=str(exc)[:120],
            )
        )
        blocking.append(f"institutional config: {exc!s}"[:120])

    if _env_truthy("HIBS_PRODUCTION") and _env_truthy("HIBS_REQUIRE_CALIBRATION_CACHE"):
        try:
            from hibs_predictor.historic_calibration import calibration_cache_path

            path = calibration_cache_path()
            ok = os.path.isfile(path)
            checks.append(
                _check_row(
                    "eng_calibration_cache",
                    label="Calibration cache on disk",
                    passed=ok,
                    detail=path if ok else f"missing {path}",
                )
            )
            if not ok:
                blocking.append(f"Calibration cache missing: {path}")
        except Exception as exc:
            warnings.append(f"calibration cache check: {exc!s}"[:80])

    if _env_truthy("HIBS_PRODUCTION") and not _env_truthy("HIBS_SHARPEN_GATES"):
        warnings.append("HIBS_SHARPEN_GATES=0 — value picks not on trial sharpen profile.")

    # --- Market contract ---
    try:
        from hibs_predictor.market_contract import validate_market_contracts

        mc_issues, mc_warn, mc_summary = validate_market_contracts()
        checks.append(
            _check_row(
                "eng_market_contract",
                label="Market contract YAML valid",
                passed=not mc_issues,
                detail=f"leagues={mc_summary.get('leagues_checked', 0)}",
            )
        )
        blocking.extend(mc_issues)
        warnings.extend(mc_warn)
    except Exception as exc:
        warnings.append(f"market contract: {exc!s}"[:80])

    # --- Forward evidence (honesty, not boot block) ---
    forward: Dict[str, Any] = {}
    buyer_ready = False
    f9_pass = False
    try:
        from hibs_predictor.forward_evidence import forward_evidence_gates

        forward = forward_evidence_gates()
        buyer_ready = bool(forward.get("buyer_ready"))
        gates = {g["id"]: g for g in forward.get("gates") or []}
        f9_pass = bool((gates.get("F9_clv_beat_close") or {}).get("pass"))
        checks.append(
            _check_row(
                "evidence_buyer_ready",
                label="Forward evidence buyer_ready",
                passed=buyer_ready,
                detail=f"grade={forward.get('evidence_grade')}",
                blocking=False,
            )
        )
        checks.append(
            _check_row(
                "evidence_f9_beat_close",
                label="F9 CLV beat-close gate",
                passed=f9_pass,
                detail=str((gates.get("F9_clv_beat_close") or {}).get("actual")),
                blocking=False,
            )
        )
    except Exception as exc:
        warnings.append(f"forward evidence: {exc!s}"[:80])

    # --- FVE traffic safety (optional probe) ---
    fve_ok = True
    fve_detail = "probe skipped"
    if probe_fve and _env_truthy("HIBS_FVE_INTEGRATION"):
        fve_url = (os.getenv("FVE_API_URL") or "http://127.0.0.1:8000").rstrip("/")
        if _env_truthy("FVE_PAUSED"):
            fve_ok = False
            fve_detail = "FVE_PAUSED=1 — line shop offline"
            if _env_truthy("HIBS_PRODUCTION"):
                blocking.append("FVE_PAUSED=1 with HIBS_FVE_INTEGRATION=1")
        else:
            ok, health, err = _http_json(f"{fve_url}/health")
            if not ok:
                fve_ok = False
                fve_detail = err or "health unreachable"
            else:
                ws = (health or {}).get("ws") or {}
                worker = (health or {}).get("worker") or {}
                budgets = ((health or {}).get("api_budgets") or {}).get("sources") or {}
                exhausted = [
                    k for k, b in budgets.items() if (b or {}).get("remaining") is not None and (b or {}).get("remaining") <= 0
                ]
                fve_detail = (
                    f"ws_clients={ws.get('active_clients')} "
                    f"backpressure_drops={ws.get('backpressure_drops')} "
                    f"worker_ok={bool(worker.get('ok', worker.get('alive')))}"
                )
                if exhausted:
                    fve_ok = False
                    fve_detail += f" budgets_exhausted={exhausted}"
        checks.append(
            _check_row(
                "traffic_fve_health",
                label="FVE line shop healthy (unpaused + /health)",
                passed=fve_ok,
                detail=fve_detail,
            )
        )

    # --- Commercial claim caps (prevent oversell in API) ---
    eng_secure = not blocking
    traffic_safe = fve_ok if (_env_truthy("HIBS_FVE_INTEGRATION") and probe_fve) else eng_secure

    claims = {
        "production_traffic_safe": {
            "allowed": traffic_safe,
            "wording": "REST/WS stack probed; budgets and backpressure visible in /health",
            "not_claimed": "HFT-grade or unlimited concurrent load without soak test",
        },
        "quant_lab_discipline": {
            "allowed": eng_secure and bool(forward.get("critical_pass")),
            "wording": "Audit DB, forward gates F1–F9, institutional-check, market contract",
            "not_claimed": "Proven beat-close edge or buyer_ready until gates green",
        },
        "replacement_cost_gbp": REPLACEMENT_COST_GBP,
        "saleable_today_gbp": SALEABLE_TODAY_GBP,
        "implied_valuation_gbp": {
            "allowed": False,
            "reason": "No term-sheet valuation without buyer_ready + signed ARR or exclusivity terms",
            "example_blocked": 128_571,
        },
        "list_license_gbp": {
            "allowed": buyer_ready,
            "football_y1_range": [24_000, 55_000] if buyer_ready else [5_000, 15_000],
            "reason": "buyer_ready gates commercial list pricing",
        },
        "downside_shielded_by_gates": {
            "allowed": True,
            "wording": "Automated gates disclose F7–F9; they do not guarantee ROI",
            "not_claimed": "Economic downside eliminated",
        },
    }

    return {
        "secure": eng_secure and traffic_safe,
        "engineering_secure": eng_secure,
        "traffic_safe": traffic_safe,
        "buyer_ready": buyer_ready,
        "evidence_f9_pass": f9_pass,
        "checks": checks,
        "blocking_issues": blocking,
        "warnings": warnings,
        "commercial_claims": claims,
        "forward_evidence": {
            "evidence_grade": forward.get("evidence_grade"),
            "commercial_tier": forward.get("commercial_tier"),
            "buyer_readiness_score": forward.get("buyer_readiness_score"),
        },
    }


def validate_production_secure(*, strict: bool = True, probe_fve: bool = True) -> List[str]:
    """Raise RuntimeError when strict and engineering blocking issues exist."""
    rep = production_secure_dict(probe_fve=probe_fve)
    issues = list(rep.get("blocking_issues") or [])
    if strict and issues:
        raise RuntimeError("Production Secure failed: " + "; ".join(issues[:5]))
    return issues
