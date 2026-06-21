"""Tests for FVE prematch paper ledger."""

from __future__ import annotations

import os

import pytest

from engine.paper_ledger import (
    ledger_health_slice,
    pick_verification_hash,
    prematch_evidence_gates,
    record_value_picks,
    settle_open_picks,
)


@pytest.fixture
def paper_db(tmp_path, monkeypatch):
    db = tmp_path / "fve.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("FVE_PAPER_LEDGER", "1")
    monkeypatch.setenv("FVE_PAUSED", "0")
    recon = tmp_path / "paper_recon.json"
    monkeypatch.setattr(
        "engine.paper_ledger._RECON_PATH",
        str(recon),
    )
    return db


def test_verification_hash_deterministic():
    h = pick_verification_hash("id1", "ts", "fk", "home", 2.1, 5.0)
    assert h == pick_verification_hash("id1", "ts", "fk", "home", 2.1, 5.0)
    assert len(h) == 64


def test_record_and_health(paper_db):
    picks = [
        {
            "pick_id": "p1",
            "market": "home",
            "odds": 2.2,
            "stake": 10.0,
            "model_prob": 0.5,
            "edge_pct": 3.0,
        }
    ]
    out = record_value_picks("test-fixture", picks)
    assert out["recorded"] == 1
    health = ledger_health_slice()
    assert health["n_rows"] == 1
    assert health["with_verification_hash"] == 1
    assert health["recon_clean"] is True


def test_settle_and_clv(paper_db):
    record_value_picks(
        "f1",
        [{"pick_id": "p-settle-1", "market": "home", "odds": 2.5, "stake": 5.0, "edge_pct": 4.0}],
    )
    res = settle_open_picks(
        results={
            "f1": {
                "home_goals": 2,
                "away_goals": 1,
                "closing_odds": {"home": 2.3},
            }
        }
    )
    assert res["settled"] == 1
    health = ledger_health_slice()
    assert health["settled"] == 1
    assert health["clv_n"] == 1


def test_prematch_evidence_gates_structure(paper_db, monkeypatch):
    monkeypatch.setattr(
        "pipeline.worker_status.worker_status",
        lambda: {"alive": True, "status": "ok"},
    )
    rep = prematch_evidence_gates()
    assert "V10_worker" in [g["id"] for g in rep["gates"]]
    assert rep["vertical"] == "fve_prematch"
