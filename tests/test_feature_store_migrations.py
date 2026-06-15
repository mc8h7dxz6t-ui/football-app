"""Tests for versioned feature_store migrations."""

from __future__ import annotations

import sqlite3

import pytest

from metrics.feature_store_migrations import (
    FEATURE_STORE_MIGRATIONS,
    MigrationDriftError,
    MigrationSpec,
    apply_feature_store_migrations,
    migration_status,
)
from metrics.racing_settlement import apply_race_results


@pytest.fixture
def bare_db(tmp_path):
    path = tmp_path / "feature_store.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE upcoming_runners (
            race_id TEXT, runner_id INTEGER, course_id TEXT
        )
        """
    )
    con.execute("INSERT INTO upcoming_runners VALUES ('r1', 1, 'aintree')")
    con.commit()
    con.close()
    return path


def test_migration_applies_and_records_ledger(bare_db):
    con = sqlite3.connect(bare_db)
    try:
        report = apply_feature_store_migrations(con, "upcoming_runners")
        assert report["applied"] == ["001"]
        cols = {r[1] for r in con.execute("PRAGMA table_info(upcoming_runners)")}
        assert "finish_position" in cols
        assert "score" in cols
        status = migration_status(con)
        assert status["pending"] == []
        assert len(status["applied"]) == 1
    finally:
        con.close()


def test_migration_idempotent_second_run(bare_db):
    con = sqlite3.connect(bare_db)
    try:
        first = apply_feature_store_migrations(con, "upcoming_runners")
        second = apply_feature_store_migrations(con, "upcoming_runners")
        assert first["applied"] == ["001"]
        assert second["applied"] == []
        assert second["already_applied"] == ["001"]
    finally:
        con.close()


def test_checksum_drift_raises(bare_db, monkeypatch):
    con = sqlite3.connect(bare_db)
    try:
        apply_feature_store_migrations(con, "upcoming_runners")
        tampered = [
            MigrationSpec("001", "settlement_columns_tampered", FEATURE_STORE_MIGRATIONS[0].upgrade)
        ]
        with pytest.raises(MigrationDriftError):
            apply_feature_store_migrations(con, "upcoming_runners", migrations=tampered)
    finally:
        con.close()


def test_settlement_triggers_migration(bare_db):
    apply_race_results(
        bare_db,
        "r1",
        [{"runner_id": 1, "finish_position": 1, "score": 0.42}],
        use_lock=False,
    )
    con = sqlite3.connect(bare_db)
    try:
        n = con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        assert n == 1
        row = con.execute(
            "SELECT finish_position, score FROM upcoming_runners WHERE runner_id=1"
        ).fetchone()
        assert row == (1, 0.42)
    finally:
        con.close()
