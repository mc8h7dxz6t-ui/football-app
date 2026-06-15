"""Tests for racing verification automation."""

from __future__ import annotations

import json
import sqlite3

import pytest

from metrics.racing_automation import (
    exit_code_for_report,
    resolve_automation_config,
    run_racing_verification_pipeline,
    RacingAutomationConfig,
)
from metrics.racing_jsonl_store import append_records, load_race_ids, trim_jsonl


@pytest.fixture
def feature_store(tmp_path):
    db = tmp_path / "feature_store.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        """
        CREATE TABLE upcoming_runners (
            race_id TEXT, runner_id INTEGER, course_id TEXT,
            venue_mapped INTEGER, finish_position INTEGER, score REAL, win_decimal REAL
        )
        """
    )
    for rid, pos, score in [(1, 1, 0.4), (2, 2, 0.35), (3, 3, 0.15), (4, 5, 0.1)]:
        con.execute(
            "INSERT INTO upcoming_runners VALUES ('r1',?,?,1,?,?,4.0)",
            (rid, "aintree", pos, score),
        )
    con.commit()
    con.close()
    return db


def test_append_and_trim(tmp_path):
    jl = tmp_path / "a.jsonl"
    rec = {"race_id": "r1", "target": "place", "runners": []}
    stats = append_records(jl, [rec])
    assert stats["appended"] == 1
    stats2 = append_records(jl, [rec])
    assert stats2["skipped_duplicate"] == 1
    for i in range(5):
        append_records(jl, [{"race_id": f"r{i+2}", "target": "place", "runners": []}])
    trim = trim_jsonl(jl, max_races=3)
    assert trim["after"] == 3
    assert len(load_race_ids(jl)) == 3


def test_pipeline_accumulating(feature_store, tmp_path):
    cfg = RacingAutomationConfig(
        feature_store=feature_store,
        jsonl_path=tmp_path / "settled.jsonl",
        data_room_path=tmp_path / "data_room.json",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / ".lock",
        min_races_for_verify=1000,
    )
    report = run_racing_verification_pipeline(cfg, use_lock=False)
    assert report["ok"] is True
    assert report["run_outcome"] == "completed"
    assert report["status"] == "accumulating"
    assert report["window"]["n_races"] == 1
    assert (tmp_path / "data_room.json").is_file()
    assert exit_code_for_report(report) == 0


def test_pipeline_idempotent(feature_store, tmp_path):
    cfg = RacingAutomationConfig(
        feature_store=feature_store,
        jsonl_path=tmp_path / "settled.jsonl",
        data_room_path=tmp_path / "data_room.json",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / ".lock",
    )
    r1 = run_racing_verification_pipeline(cfg, use_lock=False)
    r2 = run_racing_verification_pipeline(cfg, use_lock=False)
    assert r1["emit"]["appended"] == 1
    assert r2["emit"]["appended"] == 0
    assert r2["emit"]["skipped_duplicate"] == 1


def test_exit_code_hard_fail():
    assert exit_code_for_report({"hard_fail": True}) == 1
    assert exit_code_for_report({"ok": True, "status": "accumulating"}) == 0
    assert exit_code_for_report({"skipped": True, "ok": True}) == 0


def test_skip_preserves_last_full_run(feature_store, tmp_path):
    cfg = RacingAutomationConfig(
        feature_store=feature_store,
        jsonl_path=tmp_path / "settled.jsonl",
        data_room_path=tmp_path / "data_room.json",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / ".lock",
        min_races_for_verify=1000,
    )
    first = run_racing_verification_pipeline(cfg, use_lock=False)
    assert first["run_outcome"] == "completed"
    assert first["last_full_run_at"]
    n_before = first["window"]["n_races"]

    import fcntl
    from contextlib import contextmanager

    @contextmanager
    def hold_lock():
        fh = cfg.lock_path.open("a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    with hold_lock():
        skipped = run_racing_verification_pipeline(cfg, use_lock=True, wait_lock=False)
    assert skipped["run_outcome"] == "skipped_concurrent"
    assert skipped["skipped"] is True
    assert skipped["locked"] is True
    assert skipped["last_full_run_at"] == first["last_full_run_at"]
    assert skipped["window"]["n_races"] == n_before


def test_trim_calendar_span(tmp_path):
    from metrics.racing_jsonl_store import window_span_from_records

    jl = tmp_path / "dated.jsonl"
    append_records(
        jl,
        [
            {"race_id": "a", "race_date": "2026-01-01", "target": "place", "runners": []},
            {"race_id": "b", "race_date": "2026-03-15", "target": "place", "runners": []},
        ],
    )
    trim = trim_jsonl(jl, max_races=10)
    assert trim["calendar_span_known"] is True
    assert trim["oldest_race_date"] == "2026-01-01"
    assert trim["newest_race_date"] == "2026-03-15"
    assert trim["calendar_days_span"] == 74
    span = window_span_from_records([])
    assert span["calendar_span_known"] is False


def test_resolve_config_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HIBS_RACING_DEPLOY_PATH", str(tmp_path / "hr"))
    cfg = resolve_automation_config()
    assert str(cfg.jsonl_path).endswith("settled_races.jsonl")
