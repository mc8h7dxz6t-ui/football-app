"""Market contract validation — league_profiles.yaml as enforceable Inst++ policy."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# Tier semantics (config guarantees process, not profit).
TIER_A_ELITE = frozenset({"EPL", "UCL", "LA_LIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1"})
TIER_B_DOMESTIC = frozenset({"CHAMPIONSHIP", "SCOTLAND", "EUROPA_LEAGUE", "UECL", "EREDIVISIE", "PRIMEIRA"})
TIER_C_AUDIT_ONLY = frozenset({"INTL_FRIENDLIES", "FA_CUP", "LEAGUE_CUP", "SCOTTISH_CUP"})

_REQUIRED_PROFILE_FIELDS = (
    "draw_target",
    "upset_risk",
    "market_anchor",
    "value_margin_extra",
)

_FIELD_RANGES: Dict[str, Tuple[float, float]] = {
    "draw_target": (0.18, 0.35),
    "upset_risk": (0.0, 0.30),
    "market_anchor": (0.0, 0.25),
    "value_margin_extra": (0.0, 0.05),
}


def league_contract_tier(league_code: str, *, pipeline_excluded: bool) -> str:
    code = str(league_code or "").strip().upper()
    if pipeline_excluded or code in TIER_C_AUDIT_ONLY:
        return "C"
    if code in TIER_A_ELITE:
        return "A"
    if code in TIER_B_DOMESTIC:
        return "B"
    return "B"


def min_dq_for_tier(tier: str) -> float:
    return {"A": 85.0, "B": 82.0, "C": 88.0}.get(tier, 82.0)


def in_scale_cohort(tier: str) -> bool:
    return tier in ("A", "B")


def validate_market_contracts() -> Tuple[List[str], List[str], Dict[str, Any]]:
    """
    Validate config/league_profiles.yaml + pipeline_excluded consistency.

    Returns (blocking_issues, warnings, summary_dict).
    """
    from hibs_predictor.league_profiles import (
        get_league_profile,
        pipeline_excluded_league_codes,
    )

    excluded = pipeline_excluded_league_codes(reload=True)
    issues: List[str] = []
    warnings: List[str] = []
    leagues_checked: List[Dict[str, Any]] = []

    try:
        from hibs_predictor.league_profiles import _load_yaml_profiles  # type: ignore[attr-defined]

        doc = _load_yaml_profiles() or {}
    except Exception:
        doc = {}

    league_map = doc.get("leagues") if isinstance(doc, dict) else {}
    if not isinstance(league_map, dict) or not league_map:
        warnings.append("league_profiles.yaml missing or empty — using Python fallbacks only.")

    codes = sorted(set(league_map.keys()) if league_map else ())
    if not codes:
        from hibs_predictor.league_profiles import _PROFILES  # type: ignore[attr-defined]

        codes = sorted(_PROFILES.keys())

    for code in codes:
        prof = get_league_profile(code)
        row_issues: List[str] = []
        for field in _REQUIRED_PROFILE_FIELDS:
            if field not in prof:
                row_issues.append(f"{code}: missing {field}")
                continue
            try:
                val = float(prof[field])
            except (TypeError, ValueError):
                row_issues.append(f"{code}: {field} not numeric")
                continue
            lo, hi = _FIELD_RANGES[field]
            if not (lo <= val <= hi):
                row_issues.append(f"{code}: {field}={val} outside [{lo}, {hi}]")

        is_excluded = code.upper() in excluded
        tier = league_contract_tier(code, pipeline_excluded=is_excluded)
        if is_excluded and tier != "C":
            warnings.append(f"{code} is pipeline_excluded but tier={tier} (expected C).")
        if tier == "C" and not is_excluded and code.upper() in TIER_C_AUDIT_ONLY:
            warnings.append(f"{code} tier C audit-only but not in pipeline_excluded — add to YAML.")

        if row_issues:
            issues.extend(row_issues)
        leagues_checked.append(
            {
                "league": code,
                "tier": tier,
                "pipeline_excluded": is_excluded,
                "in_scale_cohort": in_scale_cohort(tier) and not is_excluded,
                "min_dq_pct": min_dq_for_tier(tier),
                "value_margin_extra": prof.get("value_margin_extra"),
            }
        )

    if excluded:
        for code in sorted(excluded):
            if code.upper() in TIER_A_ELITE:
                warnings.append(f"Elite league {code} is pipeline_excluded — scale cohort will skip it.")

    summary = {
        "leagues_checked": len(leagues_checked),
        "pipeline_excluded_n": len(excluded),
        "tier_counts": {
            "A": sum(1 for r in leagues_checked if r["tier"] == "A"),
            "B": sum(1 for r in leagues_checked if r["tier"] == "B"),
            "C": sum(1 for r in leagues_checked if r["tier"] == "C"),
        },
        "leagues": leagues_checked[:40],
    }
    return issues, warnings, summary
