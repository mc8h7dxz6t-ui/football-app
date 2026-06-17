"""Tests for in-play I1–I5 evidence gates."""

from __future__ import annotations

import struct

import pytest

from inplay import evidence_store as store
from inplay.evidence import inplay_evidence_gates
from inplay.feeds_binary import ingest_binary_payload
from inplay.model_sanity import run_sanity_check
from inplay.router import fetch_exchange_marks


@pytest.fixture
def evidence_db(tmp_path, monkeypatch):
    db = tmp_path / "inplay_evidence.sqlite"
    monkeypatch.setenv("HIBS_INPLAY_EVIDENCE_DB", str(db))
    monkeypatch.setenv("HIBS_INPLAY_EVIDENCE_DEPLOY_DATE", "2026-06-01")
    monkeypatch.setenv("HIBS_INPLAY_MOCK_MARKS", "1")
    monkeypatch.setenv("HIBS_INPLAY_I1_MIN_FRAMES", "5")
    monkeypatch.setenv("HIBS_INPLAY_I2_MAX_DIFF", "0.10")
    monkeypatch.setenv("HIBS_INPLAY_I3_MIN_COVERAGE_PCT", "30")
    monkeypatch.setenv("HIBS_INPLAY_I4_MIN_ROWS", "3")
    monkeypatch.setenv("HIBS_INPLAY_I5_MIN_ROWS", "3")
    return db


def test_feed_ingest_records_i1(evidence_db):
    payload = struct.pack(">HQI", 1, 100, 42) + b"body"
    out = ingest_binary_payload("opta", payload)
    assert out["meta"]["fixture_id"] == 42
    tel = store.feed_telemetry()
    assert tel["n_frames"] >= 1


def test_marks_and_clv_i3_i4(evidence_db):
    fetch_exchange_marks(99)
    store.record_inplay_clv(
        fixture_id=99,
        outcome="home",
        odds_taken=2.1,
        odds_close_fair=2.0,
    )
    store.record_inplay_clv(
        fixture_id=99,
        outcome="home",
        odds_taken=2.2,
        odds_close_fair=2.0,
    )
    store.record_inplay_clv(
        fixture_id=99,
        outcome="away",
        odds_taken=3.5,
        odds_close_fair=3.8,
    )
    marks = store.marks_coverage_summary(since_iso="2026-06-01T00:00:00+00:00")
    assert marks["n_snapshots"] >= 1
    clv = store.clv_summary(since_iso="2026-06-01T00:00:00+00:00")
    assert clv["n"] == 3


def test_paper_i5(evidence_db):
    for i in range(5):
        store.record_inplay_paper_bet(
            fixture_id=100 + i,
            outcome="home",
            odds_taken=2.0 + i * 0.01,
            model_prob=0.45,
            status="settled",
        )
    paper = store.paper_summary(since_iso="2026-06-01T00:00:00+00:00")
    assert paper["with_verification_hash"] >= 5


def test_model_sanity_i2(evidence_db):
    for _ in range(12):
        run_sanity_check(home_lambda=1.4, away_lambda=1.1, fixture_id=1)
    rep = inplay_evidence_gates()
    i2 = next(g for g in rep["gates"] if g["id"] == "I2_model")
    assert i2["pass"] is True


def test_buyer_ready_all_green(evidence_db):
    for seq in range(10):
        ingest_binary_payload("opta", struct.pack(">HQI", 1, seq, 7) + b"x")
    for _ in range(6):
        fetch_exchange_marks(7)
    for i in range(5):
        store.record_inplay_clv(fixture_id=7, outcome="home", odds_taken=2.1, odds_close_fair=2.0)
    for i in range(5):
        store.record_inplay_paper_bet(fixture_id=7, outcome="home", odds_taken=2.0, model_prob=0.5)
    for _ in range(12):
        run_sanity_check(home_lambda=1.3, away_lambda=1.2, fixture_id=7)
    rep = inplay_evidence_gates()
    assert rep["buyer_ready"] is True
    assert rep["evidence_grade"] == "A"
