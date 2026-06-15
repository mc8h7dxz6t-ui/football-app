"""Tests for line delta payloads."""

from __future__ import annotations

from pipeline.line_delta import build_line_update_message, diff_shopped, reset_line_delta_state


def _shopped(home: float, draw: float, away: float) -> dict:
    q = lambda o: {"odds": o, "bookmaker": "Bet365", "category": "soft"}
    return {
        "Home": {"soft": q(home)},
        "Draw": {"soft": q(draw)},
        "Away": {"soft": q(away)},
    }


def test_diff_shopped_detects_odds_change():
    prev = _shopped(2.0, 3.4, 3.8)
    cur = _shopped(2.1, 3.4, 3.8)
    changed = diff_shopped(prev, cur)
    assert "Home" in changed
    assert changed["Home"]["soft"]["odds"] == 2.1
    assert "Draw" not in changed


def test_build_line_update_message_delta_mode(monkeypatch):
    monkeypatch.setenv("FVE_WS_DELTA_UPDATES", "1")
    reset_line_delta_state("Test v Other")
    lines = {
        "tick_count": 3,
        "shopped": _shopped(2.0, 3.4, 3.8),
        "sharp_fair_probs": {"Home": 0.45, "Draw": 0.28, "Away": 0.27},
    }
    first = build_line_update_message("Test v Other", lines)
    assert first is not None
    assert first["mode"] == "delta"
    assert "changed_markets" in first
    assert first["type"] == "line_update"

    lines["shopped"] = _shopped(2.0, 3.4, 3.8)
    second = build_line_update_message("Test v Other", lines)
    assert second is None

    lines["shopped"] = _shopped(2.15, 3.4, 3.8)
    third = build_line_update_message("Test v Other", lines)
    assert third is not None
    assert third["mode"] == "delta"
    assert third["changed_markets"]["Home"]["soft"]["odds"] == 2.15


def test_build_line_update_message_full_mode(monkeypatch):
    monkeypatch.setenv("FVE_WS_DELTA_UPDATES", "0")
    reset_line_delta_state("Full v Mode")
    lines = {"tick_count": 1, "shopped": _shopped(2.0, 3.4, 3.8)}
    msg = build_line_update_message("Full v Mode", lines)
    assert msg is not None
    assert msg["mode"] == "full"
    assert "lines" in msg
