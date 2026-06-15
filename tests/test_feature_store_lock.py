"""Tests for feature_store flock and settlement transactions."""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from metrics import racing_settlement as settlement
from metrics.feature_store_lock import FeatureStoreLockTimeout, feature_store_lock
from metrics.racing_settlement import apply_results_batch, apply_race_results


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "feature_store.sqlite"
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
    for rid, score in [(10, 0.5), (11, 0.5)]:
        con.execute(
            "INSERT INTO upcoming_runners VALUES ('r2', ?, ?, 'cheltenham', 1)",
            (rid, score),
        )
    con.commit()
    con.close()
    return path


def test_batch_single_transaction_commits_all(db):
    payload = [
        {"race_id": "r1", "runners": [{"runner_id": 1, "finish_position": 1}]},
        {"race_id": "r2", "runners": [{"runner_id": 10, "finish_position": 1}]},
    ]
    out = apply_results_batch(db, payload, use_lock=False)
    assert out["transaction"] == "single"
    assert out["rows_updated"] == 2
    con = sqlite3.connect(db)
    assert con.execute(
        "SELECT finish_position FROM upcoming_runners WHERE race_id='r1' AND runner_id=1"
    ).fetchone()[0] == 1
    assert con.execute(
        "SELECT finish_position FROM upcoming_runners WHERE race_id='r2' AND runner_id=10"
    ).fetchone()[0] == 1
    con.close()


def test_batch_rolls_back_on_failure(db, monkeypatch):
    original = settlement._apply_race_on_connection

    def _boom(con, src, wcols, race_id, runners, **kwargs):
        if race_id == "r2":
            raise RuntimeError("simulated batch failure")
        return original(con, src, wcols, race_id, runners, **kwargs)

    monkeypatch.setattr(settlement, "_apply_race_on_connection", _boom)
    payload = [
        {"race_id": "r1", "runners": [{"runner_id": 1, "finish_position": 1}]},
        {"race_id": "r2", "runners": [{"runner_id": 10, "finish_position": 1}]},
    ]
    with pytest.raises(RuntimeError, match="simulated"):
        apply_results_batch(db, payload, use_lock=False)

    con = sqlite3.connect(db)
    assert (
        con.execute(
            "SELECT finish_position FROM upcoming_runners WHERE race_id='r1' AND runner_id=1"
        ).fetchone()[0]
        is None
    )
    con.close()


def test_feature_store_lock_blocks_without_wait(db):
    hold = threading.Event()
    release = threading.Event()

    def holder():
        with feature_store_lock(db, wait=False):
            hold.set()
            release.wait(timeout=5)

    t = threading.Thread(target=holder)
    t.start()
    assert hold.wait(timeout=2)

    with pytest.raises(FeatureStoreLockTimeout):
        apply_race_results(
            db,
            "r1",
            [{"runner_id": 1, "finish_position": 1}],
            use_lock=True,
            wait_lock=False,
        )

    release.set()
    t.join(timeout=5)


def test_feature_store_lock_waits_then_acquires(db):
    hold = threading.Event()
    release = threading.Event()
    acquired = threading.Event()

    def holder():
        with feature_store_lock(db, wait=False):
            hold.set()
            release.wait(timeout=5)

    t = threading.Thread(target=holder)
    t.start()
    assert hold.wait(timeout=2)

    def waiter():
        apply_race_results(
            db,
            "r1",
            [{"runner_id": 1, "finish_position": 1}],
            use_lock=True,
            wait_lock=True,
        )
        acquired.set()

    w = threading.Thread(target=waiter)
    w.start()
    time.sleep(0.3)
    release.set()
    assert acquired.wait(timeout=5)
    w.join(timeout=5)
    t.join(timeout=5)

    con = sqlite3.connect(db)
    assert (
        con.execute(
            "SELECT finish_position FROM upcoming_runners WHERE runner_id=1"
        ).fetchone()[0]
        == 1
    )
    con.close()
