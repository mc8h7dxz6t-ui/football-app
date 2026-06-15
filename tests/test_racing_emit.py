"""Tests for racing JSONL emit + sqlite extraction."""

from __future__ import annotations

import json
import sqlite3

import pytest

from metrics.racing_emit import build_settled_race_record, normalize_race_probs, to_jsonl_line
from metrics.racing_sqlite import extract_settled_races_from_db


@pytest.fixture
def feature_store(tmp_path):
    db = tmp_path / "feature_store.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        """
        CREATE TABLE upcoming_runners (
            race_id TEXT,
            runner_id INTEGER,
            course_id TEXT,
            venue_mapped INTEGER,
            finish_position INTEGER,
            score REAL,
            win_decimal REAL,
            place_positions INTEGER
        )
        """
    )
    # 4-runner place race — top 3 placed
    rows = [
        ("r1", 1, "aintree", 1, 2, 0.35, 4.0, 3),
        ("r1", 2, "aintree", 1, 1, 0.30, 3.5, 3),
        ("r1", 3, "aintree", 1, 3, 0.25, 6.0, 3),
        ("r1", 4, "aintree", 1, 4, 0.10, 12.0, 3),
    ]
    con.executemany(
        "INSERT INTO upcoming_runners VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()
    return db


def test_normalize_race_probs_softmax():
    p = normalize_race_probs([2.0, 1.0, 0.5])
    assert abs(sum(p) - 1.0) < 1e-9
    assert p[0] > p[1] > p[2]


def test_jsonl_line_roundtrip():
    rec = build_settled_race_record(
        race_id="x",
        target="place",
        runners=[
            {"runner_id": "a", "model_prob": 0.5, "market_prob": 0.4, "won": False, "placed": True}
        ],
        place_positions=3,
    )
    parsed = json.loads(to_jsonl_line(rec))
    assert parsed["target"] == "place"


def test_extract_from_feature_store(feature_store):
    races = extract_settled_races_from_db(
        feature_store, target="place", require_paired_place_market=False
    )
    assert len(races) == 1
    assert races[0]["race_id"] == "r1"
    assert len(races[0]["runners"]) == 4
    assert sum(1 for r in races[0]["runners"] if r["placed"]) == 3
    assert races[0]["venue_id"] == "aintree"


@pytest.fixture
def scored_snapshots_db(tmp_path):
    """hibs-racing scored_runner_snapshots shape (finish_pos + model_place_prob)."""
    db = tmp_path / "feature_store.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        """
        CREATE TABLE scored_runner_snapshots (
            card_date TEXT,
            runner_id TEXT,
            race_id TEXT,
            course TEXT,
            field_size INTEGER,
            win_decimal REAL,
            offered_place_decimal REAL,
            places INTEGER,
            model_score REAL,
            model_place_prob REAL,
            finish_pos INTEGER,
            scored_at TEXT,
            odds_source TEXT,
            config_hash TEXT
        )
        """
    )
    rows = [
        ("2026-06-01", "r1:h1", "rac_1", "Carlisle", 3, 4.0, 3.5, 3, 0.8, 0.45, 1, "2026-06-01T11:00:00+00:00", "racing_api", "cfg1"),
        ("2026-06-01", "r1:h2", "rac_1", "Carlisle", 3, 5.0, 4.0, 3, 0.6, 0.35, 2, "2026-06-01T10:00:00+00:00", "racing_api", "cfg1"),
        ("2026-06-01", "r1:h3", "rac_1", "Carlisle", 3, 8.0, 6.0, 3, 0.4, 0.20, 4, "2026-06-01T10:00:00+00:00", "racing_api", "cfg1"),
        ("2026-06-01", "r1:h1", "rac_1", "Carlisle", 3, 4.0, 3.5, 3, 0.1, 0.10, 1, "2026-06-01T09:00:00+00:00", "racing_api", "cfg1"),
    ]
    con.executemany(
        "INSERT INTO scored_runner_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()
    return db


def test_extract_from_scored_runner_snapshots(scored_snapshots_db):
    races = extract_settled_races_from_db(
        scored_snapshots_db,
        target="place",
        table="scored_runner_snapshots",
    )
    assert len(races) == 1
    assert races[0]["race_id"] == "rac_1"
    assert races[0]["race_date"] == "2026-06-01"
    assert races[0]["meta"]["paired_benchmark_only"] is True
    assert races[0]["venue_id"] == "Carlisle"
    assert races[0]["place_positions"] == 3
    assert len(races[0]["runners"]) == 3
    by_id = {r["runner_id"]: r for r in races[0]["runners"]}
    assert by_id["r1:h1"]["won"] is True
    assert by_id["r1:h1"]["placed"] is True
    assert by_id["r1:h2"]["placed"] is True
    assert by_id["r1:h3"]["placed"] is False
    # latest scored_at row kept for h1; place prob not field-normalized
    assert by_id["r1:h1"]["model_prob"] == 0.45
    assert by_id["r1:h1"]["market_prob"] is not None


def test_extract_ignores_heavy_json_columns(tmp_path):
    """Projected SELECT must skip manifest_json-sized blobs on snapshots."""
    db = tmp_path / "heavy.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        """
        CREATE TABLE scored_runner_snapshots (
            race_id TEXT, runner_id TEXT, course TEXT, places INTEGER,
            finish_pos INTEGER, model_place_prob REAL, offered_place_decimal REAL,
            scored_at TEXT, manifest_json TEXT
        )
        """
    )
    blob = "x" * 500_000
    rows = [
        ("rac_1", "h1", "York", 3, 1, 0.5, 2.5, "2026-06-01T12:00:00+00:00", blob),
        ("rac_1", "h2", "York", 3, 2, 0.3, 3.0, "2026-06-01T12:00:00+00:00", blob),
        ("rac_1", "h3", "York", 3, 4, 0.2, 5.0, "2026-06-01T12:00:00+00:00", blob),
    ]
    con.executemany(
        "INSERT INTO scored_runner_snapshots VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()
    races = extract_settled_races_from_db(db, table="scored_runner_snapshots", target="place")
    assert len(races) == 1
    assert len(races[0]["runners"]) == 3


def test_hook_emit(tmp_path):
    from integrations.hibs_racing.settled_race_hook import emit_race_from_scored_runners

    out = tmp_path / "out.jsonl"
    emit_race_from_scored_runners(
        race_id="r99",
        target="place",
        venue_id="york",
        runners=[
            {"runner_id": "h1", "score": 0.4, "position": 1},
            {"runner_id": "h2", "score": 0.35, "position": 2},
            {"runner_id": "h3", "score": 0.15, "position": 5},
        ],
        out_path=out,
    )
    line = out.read_text(encoding="utf-8").strip()
    data = json.loads(line)
    assert data["race_id"] == "r99"
    assert data["runners"][0]["won"] is True
