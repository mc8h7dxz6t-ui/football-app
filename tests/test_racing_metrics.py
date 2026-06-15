"""Racing verification — win vs place, venue mapping."""

import json

from metrics.racing import (
    RacingRecord,
    RacingRunner,
    evaluate_racing_window,
    racing_record_from_dict,
    venue_mapping_summary,
)


def _sample_race(target="place", venue_mapped=True, race_id="r1"):
    return RacingRecord(
        race_id=race_id,
        target=target,
        venue_id="aintree" if venue_mapped else "unknown_x",
        venue_mapped=venue_mapped,
        place_positions=3,
        runners=[
            RacingRunner("h1", 0.35, 0.32, won=False, placed=True),
            RacingRunner("h2", 0.25, 0.28, won=True, placed=True),
            RacingRunner("h3", 0.20, 0.22, won=False, placed=True),
            RacingRunner("h4", 0.20, 0.18, won=False, placed=False),
        ],
    )


def test_place_vs_win_target():
    place = _sample_race("place")
    win = _sample_race("win")
    assert place.outcome_vector() == [1, 1, 1, 0]
    assert win.outcome_vector() == [0, 1, 0, 0]


def test_venue_mapping_summary():
    races = [_sample_race(venue_mapped=True), _sample_race(venue_mapped=False, race_id="r2")]
    v = venue_mapping_summary(races)
    assert v["n_races"] == 2
    assert v["n_mapped"] == 1
    assert v["mapped_pct"] == 0.5


def test_evaluate_racing_place_window():
    races = [_sample_race(race_id=f"r{i}") for i in range(100)]
    export = evaluate_racing_window(races, min_races=1000, oos_declared=True)
    assert export["target"]["kind"] == "place"
    assert export["model"]["macro_brier_per_race"] is not None
    assert export["gates"]["institutional_grade"] is False
    assert any("n_events" in r for r in export["gates"]["reasons"])


def _sharp_place_race(race_id="r1"):
    """Model assigns mass to actual placers; market is flat — model should win Brier."""
    return RacingRecord(
        race_id=race_id,
        target="place",
        venue_mapped=True,
        runners=[
            RacingRunner("h1", 0.34, 0.25, placed=True),
            RacingRunner("h2", 0.33, 0.25, placed=True),
            RacingRunner("h3", 0.32, 0.25, placed=True),
            RacingRunner("h4", 0.01, 0.25, placed=False),
        ],
    )


def test_evaluate_racing_institutional_pass():
    races = [_sharp_place_race(race_id=f"r{i}") for i in range(1000)]
    export = evaluate_racing_window(races, min_races=1000, oos_declared=True)
    assert export["gates"]["institutional_grade"] is True
    assert export["delta_vs_market"]["verdict"] == "model_beats_market"


def test_racing_record_from_dict_jsonl_shape():
    raw = {
        "race_id": "x",
        "target": "win",
        "venue_mapped": True,
        "runners": [
            {"runner_id": "a", "model_prob": 0.5, "market_prob": 0.48, "won": True},
            {"runner_id": "b", "model_prob": 0.5, "market_prob": 0.52, "won": False},
        ],
    }
    rec = racing_record_from_dict(raw)
    assert rec.target == "win"
    assert rec.runners[0].won is True


def test_mixed_targets_rejected():
    races = [_sample_race("place"), _sample_race("win", race_id="r2")]
    out = evaluate_racing_window(races)
    assert out["error"] == "mixed_targets"
