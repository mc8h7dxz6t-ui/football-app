"""Tests for settlement write + JSONL validation."""

from __future__ import annotations

import json
import sqlite3

import pytest

from metrics.racing_settlement import apply_race_results, apply_results_batch, settlement_coverage
from metrics.racing_validate import validate_race_record, record_runner_count_stats


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "fs.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE upcoming_runners (
            race_id TEXT, runner_id INTEGER, score REAL, course_id TEXT, venue_mapped INTEGER
        )
        """
    )
    for rid, score in [(1, 0.4), (2, 0.35), (3, 0.25)]:
        con.execute(
            "INSERT INTO upcoming_runners VALUES ('r1', ?, ?, 'aintree', 1)",
            (rid, score),
        )
    con.commit()
    con.close()
    return path


def test_apply_preserves_score_when_omitted(db):
    apply_race_results(db, "r1", [{"runner_id": 1, "finish_position": 1}])
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT score, finish_position FROM upcoming_runners WHERE runner_id=1"
    ).fetchone()
    con.close()
    assert row[0] == 0.4
    assert row[1] == 1


def test_apply_updates_score_when_provided(db):
    apply_race_results(db, "r1", [{"runner_id": 2, "finish_position": 2, "score": 0.99}])
    con = sqlite3.connect(db)
    row = con.execute("SELECT score FROM upcoming_runners WHERE runner_id=2").fetchone()
    con.close()
    assert row[0] == 0.99


def test_settlement_coverage(db):
    apply_race_results(db, "r1", [{"runner_id": 1, "finish_position": 1}])
    cov = settlement_coverage(db)
    assert cov["runners_with_position"] == 1
    assert cov["runners_with_score"] == 3


def test_settlement_coverage_without_position_column(tmp_path):
    path = tmp_path / "bare.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE upcoming_runners (race_id TEXT, runner_id INTEGER, score REAL)")
    con.execute("INSERT INTO upcoming_runners VALUES ('r1', 1, 0.4)")
    con.commit()
    con.close()
    cov = settlement_coverage(path)
    assert cov["ok"] is True
    assert cov["runners_with_position"] == 0
    assert cov["runners_with_score"] == 1


def test_place_multi_placer_validation():
    rec = {
        "race_id": "r1",
        "target": "place",
        "place_positions": 3,
        "runners": [
            {"runner_id": "a", "model_prob": 0.3, "placed": True},
            {"runner_id": "b", "model_prob": 0.3, "placed": True},
            {"runner_id": "c", "model_prob": 0.3, "placed": True},
            {"runner_id": "d", "model_prob": 0.1, "placed": True},
        ],
    }
    ok, errs = validate_race_record(rec)
    assert ok  # warn only for 4 placers with pp=3
    assert any("warn" in e for e in errs)


def test_variable_field_sizes_no_clip():
    sizes = [5, 12, 8, 20, 6]
    records = [
        {
            "race_id": f"r{i}",
            "target": "place",
            "runners": [{"runner_id": f"h{j}", "model_prob": 0.1, "placed": j < 3} for j in range(n)],
        }
        for i, n in enumerate(sizes)
    ]
    stats = record_runner_count_stats(records)
    assert stats["max_runners"] == 20
    assert stats["min_runners"] == 5


def test_batch_settle_from_json(db):
    payload = [
        {
            "race_id": "r1",
            "runners": [
                {"runner_id": 1, "finish_position": 1},
                {"runner_id": 2, "finish_position": 2},
                {"runner_id": 3, "finish_position": 3},
            ],
        }
    ]
    out = apply_results_batch(db, payload)
    assert out["rows_updated"] == 3
    assert out["transaction"] == "single"
