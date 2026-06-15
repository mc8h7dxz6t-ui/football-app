"""Tests for institutional++ racing measurement contract."""

from __future__ import annotations

from metrics.racing_measurement import (
    filter_races_after_cutoff,
    measurement_contract_summary,
    model_probs_for_export,
    market_prob_for_target,
    runners_fully_paired,
)


def test_place_model_probs_not_field_normalized():
    raw = [0.35, 0.30, 0.25, 0.20]
    out = model_probs_for_export("place", raw)
    assert out == raw
    assert abs(sum(out) - 1.0) > 0.01


def test_win_model_probs_normalized():
    out = model_probs_for_export("win", [0.35, 0.30, 0.25, 0.20])
    assert abs(sum(out) - 1.0) < 1e-9


def test_place_market_ignores_win_decimal():
    p, src = market_prob_for_target("place", place_decimal=4.0, win_decimal=2.0)
    assert src == "offered_place_decimal"
    assert abs(p - 0.25) < 1e-9


def test_oos_filter_after_cutoff():
    rows = [
        {"race_id": "a", "race_date": "2026-01-01", "runners": []},
        {"race_id": "b", "race_date": "2026-06-01", "runners": []},
        {"race_id": "c", "runners": []},
    ]
    kept, stats = filter_races_after_cutoff(rows, "2026-03-01")
    assert stats["oos_enforced"] is True
    assert stats["excluded_on_or_before_cutoff"] == 1
    assert stats["excluded_missing_race_date"] == 1
    assert [r["race_id"] for r in kept] == ["b"]


def test_measurement_contract_paired_pct():
    recs = [
        {
            "race_id": "r1",
            "target": "place",
            "race_date": "2026-06-01",
            "meta": {"market_prob_column": "offered_place_decimal"},
            "runners": [
                {"model_prob": 0.3, "market_prob": 0.25},
                {"model_prob": 0.2, "market_prob": 0.2},
            ],
        }
    ]
    c = measurement_contract_summary(recs)
    assert c["paired_races"] == 1
    assert c["paired_race_pct"] == 100.0
    assert runners_fully_paired(recs[0]["runners"])
