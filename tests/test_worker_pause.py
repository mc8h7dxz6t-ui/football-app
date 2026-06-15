import sys

import pytest

from feeds.registry import build_default_registry, is_matchbook_only
from pipeline.watchlist import discover_matchbook_only, parse_fixture_spec

_MATCHBOOK_ONLY_DISABLED = "api-football,betfair,pinnacle,the-odds-api"


def test_is_matchbook_only_when_disabled_soft_feeds(monkeypatch):
    monkeypatch.setenv("DISABLED_FEEDS", _MATCHBOOK_ONLY_DISABLED)
    monkeypatch.delenv("ENABLE_ODDS_API_FEED", raising=False)
    assert is_matchbook_only(build_default_registry())


def test_is_matchbook_only_false_with_api_football(monkeypatch):
    monkeypatch.delenv("DISABLED_FEEDS", raising=False)
    monkeypatch.delenv("ENABLE_ODDS_API_FEED", raising=False)
    names = {f.name for f in build_default_registry().enabled()}
    assert "api-football" in names
    assert not is_matchbook_only(build_default_registry())


def test_discover_matchbook_only_from_fixtures(monkeypatch):
    monkeypatch.setenv("WATCHLIST_FIXTURES", "A v B::999")
    keys, ctx = discover_matchbook_only()
    assert keys == ["A v B"]
    assert ctx["A v B"]["matchbook_event_id"] == 999


def test_parse_fixture_spec_matchbook_id_only(monkeypatch):
    monkeypatch.delenv("FVE_MATCHBOOK_MAP_FILE", raising=False)
    keys, ctx = parse_fixture_spec("Celtic v Rangers::12345")
    assert keys == ["Celtic v Rangers"]
    assert ctx["Celtic v Rangers"]["matchbook_event_id"] == 12345


def test_worker_exits_when_paused_without_arb_only(monkeypatch):
    monkeypatch.setenv("FVE_PAUSED", "1")
    monkeypatch.delenv("FVE_ARB_ONLY", raising=False)
    from worker import _enforce_pause_and_arb_gates

    with pytest.raises(SystemExit) as exc:
        _enforce_pause_and_arb_gates()
    assert exc.value.code == 0


def test_worker_refuses_arb_only_with_api_football(monkeypatch):
    monkeypatch.setenv("FVE_PAUSED", "1")
    monkeypatch.setenv("FVE_ARB_ONLY", "1")
    monkeypatch.delenv("DISABLED_FEEDS", raising=False)
    from worker import _enforce_pause_and_arb_gates

    with pytest.raises(SystemExit) as exc:
        _enforce_pause_and_arb_gates()
    assert exc.value.code == 1


def test_worker_allows_arb_only_matchbook_while_paused(monkeypatch):
    monkeypatch.setenv("FVE_PAUSED", "1")
    monkeypatch.setenv("FVE_ARB_ONLY", "1")
    monkeypatch.setenv("DISABLED_FEEDS", _MATCHBOOK_ONLY_DISABLED)
    monkeypatch.delenv("ENABLE_ODDS_API_FEED", raising=False)
    from worker import _enforce_pause_and_arb_gates

    _enforce_pause_and_arb_gates()


def test_worker_main_paused_exits_zero(monkeypatch):
    monkeypatch.setenv("FVE_PAUSED", "1")
    monkeypatch.delenv("FVE_ARB_ONLY", raising=False)
    monkeypatch.setattr(sys, "argv", ["worker.py"])
    from worker import main

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
