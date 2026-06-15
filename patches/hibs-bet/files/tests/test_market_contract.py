"""Market contract validator — league_profiles.yaml Inst++ policy."""

from __future__ import annotations

from hibs_predictor.market_contract import (
    in_scale_cohort,
    league_contract_tier,
    min_dq_for_tier,
    validate_market_contracts,
)


def test_league_tiers():
    assert league_contract_tier("EPL", pipeline_excluded=False) == "A"
    assert league_contract_tier("CHAMPIONSHIP", pipeline_excluded=False) == "B"
    assert league_contract_tier("INTL_FRIENDLIES", pipeline_excluded=True) == "C"
    assert in_scale_cohort("A") is True
    assert in_scale_cohort("C") is False
    assert min_dq_for_tier("A") == 85.0


def test_validate_market_contracts_runs():
    issues, warnings, summary = validate_market_contracts()
    assert isinstance(issues, list)
    assert isinstance(warnings, list)
    assert summary.get("leagues_checked", 0) >= 1
