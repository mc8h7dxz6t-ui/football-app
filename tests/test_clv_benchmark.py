"""Tests for CLV benchmark tier ladder."""

from __future__ import annotations

from engine.clv_benchmark import (
    parse_pinnacle_1x2_from_panel,
    resolve_clv_closing,
    sharp_synthetic_fair_odds,
)


def test_resolve_prefers_pinnacle():
    closing, tier, source = resolve_clv_closing(
        "home",
        pinnacle_1x2={"home": 2.0, "draw": 3.5, "away": 4.0},
        exchange_1x2={"home": 1.9, "draw": 3.4, "away": 3.9},
        api_football_1x2={"home": 1.85, "draw": 3.3, "away": 3.8},
    )
    assert closing == 2.0
    assert tier == "pinnacle"
    assert source == "pinnacle_panel"


def test_resolve_exchange_when_no_pinnacle():
    closing, tier, _ = resolve_clv_closing(
        "away",
        exchange_1x2={"home": 2.1, "draw": 3.4, "away": 3.5},
        api_football_1x2={"home": 2.0, "draw": 3.3, "away": 3.4},
    )
    assert closing == 3.5
    assert tier == "exchange"


def test_parse_pinnacle_panel():
    panel = [
        {"bookmaker": "Bet365", "home": 2.0, "draw": 3.4, "away": 3.8},
        {"bookmaker": "Pinnacle", "home": 2.05, "draw": 3.5, "away": 3.9},
    ]
    pin = parse_pinnacle_1x2_from_panel(panel)
    assert pin["home"] == 2.05
    assert pin["draw"] == 3.5


def test_sharp_synthetic_raises_fair():
    raw = {"home": 2.0, "draw": 3.4, "away": 3.8}
    fair = sharp_synthetic_fair_odds(raw)
    assert fair["home"] > raw["home"]
