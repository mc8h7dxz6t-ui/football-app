"""Tests for institutional metrics layer."""

import backtest as bt
from metrics.brier import brier_multiclass_event, brier_race, uniform_baseline_brier
from metrics.murphy import murphy_decomposition
from metrics.data_room import build_data_room_export, institutional_gates


def _rec(h, d, a, outcome, market=None):
    r = {"probs": {"Home": h, "Draw": d, "Away": a}, "outcome": outcome}
    if market:
        r["market_probs"] = market
    return r


def test_uniform_baseline_three_class():
    assert abs(uniform_baseline_brier(3) - 0.6667) < 1e-3


def test_murphy_identity():
    forecasts = [0.1, 0.2, 0.3, 0.8, 0.9, 0.15, 0.85]
    outcomes = [0, 0, 0, 1, 1, 0, 1]
    m = murphy_decomposition(forecasts, outcomes, bins=5)
    assert m["n"] == 7
    assert abs(m["brier_score"] - m["murphy_check"]) < 2e-3


def test_brier_race_variable_runners():
    # (0.5-1)^2 + 0.2^2 + 0.2^2 + 0.1^2 = 0.34 → /4 = 0.085
    assert abs(brier_race([0.5, 0.2, 0.2, 0.1], [1, 0, 0, 0]) - 0.085) < 1e-9


def test_evaluate_includes_murphy():
    recs = [_rec(0.7, 0.15, 0.15, "Home"), _rec(0.1, 0.1, 0.8, "Away")]
    ev = bt.evaluate(recs)
    assert ev["murphy"]["n"] == 6  # 3 legs x 2 fixtures
    assert ev["calibration_all_legs"]


def test_data_room_gates_fail_in_sample():
    recs = [_rec(0.6, 0.2, 0.2, "Home", market={"Home": 0.5, "Draw": 0.25, "Away": 0.25})] * 50
    export = bt.export_data_room(recs, min_events=1000, oos_declared=False)
    assert export["gates"]["institutional_grade"] is False
    assert "n_events=50" in export["gates"]["reasons"][0] or any(
        "n_events" in r for r in export["gates"]["reasons"]
    )


def test_data_room_gates_pass_synthetic():
    # Model sharper than market on 1000 identical fixtures
    recs = [
        _rec(0.9, 0.05, 0.05, "Home", market={"Home": 0.5, "Draw": 0.25, "Away": 0.25})
        for _ in range(1000)
    ]
    export = bt.export_data_room(recs, min_events=1000, oos_declared=True)
    assert export["gates"]["institutional_grade"] is True
    assert export["gates"]["valuation_tier"] == "institutional_grade"
    assert export["delta_vs_market"]["verdict"] == "model_beats_market"


def test_institutional_gates_venue_racing():
    g = institutional_gates(
        n_events=1500,
        model_brier=0.15,
        market_brier=0.16,
        oos_declared=True,
        venue_mapped_pct=0.87,
        target_kind="place",
    )
    assert g["institutional_grade"] is False
    assert any("venue_mapped" in r for r in g["reasons"])
