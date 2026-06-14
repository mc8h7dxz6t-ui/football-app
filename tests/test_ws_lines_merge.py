"""Tests for WS line_update delta merge."""

from __future__ import annotations

from pipeline.ws_lines_merge import apply_line_update, expand_line_update_for_client, merge_changed_markets
from services.ws_lines_client import LinesSessionState


def test_merge_changed_markets_updates_odds():
    prev = {"Home": {"soft": {"odds": 2.1, "bookmaker": "Bet365"}}}
    changed = {"Home": {"soft": {"odds": 2.15, "bookmaker": "Bet365"}}}
    merged = merge_changed_markets(prev, changed)
    assert merged["Home"]["soft"]["odds"] == 2.15


def test_apply_line_update_delta_mode():
    lines = {"fixture_key": "A v B", "shopped": {"Home": {"soft": {"odds": 2.0}}}}
    msg = {
        "type": "line_update",
        "mode": "delta",
        "fixture_key": "A v B",
        "changed_markets": {"Draw": {"exchange": {"odds": 3.5}}},
        "tick_count": 4,
    }
    out = apply_line_update(lines, msg)
    assert out["shopped"]["Home"]["soft"]["odds"] == 2.0
    assert out["shopped"]["Draw"]["exchange"]["odds"] == 3.5
    assert out["tick_count"] == 4


def test_expand_line_update_for_client():
    state: dict = {}
    msg = {
        "type": "line_update",
        "mode": "delta",
        "fixture_key": "X v Y",
        "ts": 1.0,
        "changed_markets": {"Home": {"soft": {"odds": 2.2}}},
    }
    expanded = expand_line_update_for_client(msg, state)
    assert expanded["mode"] == "full"
    assert expanded["lines"]["shopped"]["Home"]["soft"]["odds"] == 2.2
    assert state["shopped"]["Home"]["soft"]["odds"] == 2.2


def test_lines_session_state_snapshot_then_delta():
    session = LinesSessionState()
    snap = {
        "type": "snapshot",
        "fixture_key": "A v B",
        "lines": {"shopped": {"Home": {"soft": {"odds": 2.0}}}},
    }
    session.on_message(snap)
    delta = {
        "type": "line_update",
        "mode": "delta",
        "changed_markets": {"Home": {"soft": {"odds": 2.05}}},
    }
    out = session.on_message(delta)
    assert out["lines"]["shopped"]["Home"]["soft"]["odds"] == 2.05
