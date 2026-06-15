import os

from pipeline.rate_limits import ApiBudget, BudgetConfig, get_budget


def test_budget_blocks_after_cap(monkeypatch):
    monkeypatch.setenv("FVE_ODDS_API_MAX_CALLS_PER_HOUR", "2")
    b = ApiBudget(BudgetConfig(odds_api_per_hour=2))
    assert b.allow("the-odds-api")
    b.record("odds_api")
    b.record("odds_api")
    assert not b.allow("odds_api")


def test_budget_aliases_matchbook_feed_name():
    b = ApiBudget(BudgetConfig(matchbook_per_hour=1))
    assert b.allow("matchbook")
    b.record("matchbook")
    assert not b.allow("matchbook")


def test_budget_status_shape():
    st = get_budget().status()
    assert "sources" in st
    assert "matchbook" in st["sources"]
    assert "remaining" in st["sources"]["matchbook"]
