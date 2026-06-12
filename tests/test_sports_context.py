"""Tests for sports context bundle (no network)."""

from __future__ import annotations

from pipeline.cache import LineCache
from pipeline.ingest import build_fixture_bundle
from pipeline.sports_context import build_sports_payload, sports_refresh_due


def _fixture_row():
    return {
        "fixture": {"id": 999, "date": "2026-06-15T15:00:00+00:00", "status": {"short": "NS"}, "venue": {"name": "Test"}},
        "league": {"id": 39, "name": "Premier League", "season": 2025},
        "teams": {
            "home": {"id": 1, "name": "Arsenal"},
            "away": {"id": 2, "name": "Chelsea"},
        },
    }


def _standings():
    return {
        1: {
            "name": "Arsenal",
            "played": 10,
            "goals_for": 20,
            "goals_against": 8,
            "home_played": 5,
            "home_goals_for": 12,
            "home_goals_against": 3,
            "away_played": 5,
            "away_goals_for": 8,
            "away_goals_against": 5,
        },
        2: {
            "name": "Chelsea",
            "played": 10,
            "goals_for": 15,
            "goals_against": 12,
            "home_played": 5,
            "home_goals_for": 9,
            "home_goals_against": 4,
            "away_played": 5,
            "away_goals_for": 6,
            "away_goals_against": 8,
        },
    }


def test_build_sports_payload_model_probs():
    sports = build_sports_payload(
        fixture=_fixture_row(),
        standings_table=_standings(),
        use_xg=False,
    )
    assert sports["fixture_id"] == 999
    assert sports["data_quality"]["home_ok"] is True
    assert set(sports["model_probs"]) >= {"Home", "Draw", "Away"}


def test_sports_refresh_due_ttl():
    assert sports_refresh_due(None) is True
    assert sports_refresh_due({"updated_at": 1_000_000, "ttl_sec": 3600}, now=1_000_100) is False
    assert sports_refresh_due({"updated_at": 1_000_000, "ttl_sec": 60}, now=1_000_200) is True


def test_fixture_bundle_includes_sports():
    cache = LineCache(redis_url="redis://127.0.0.1:59999/0", ttl_sec=30)
    sports = build_sports_payload(fixture=_fixture_row(), standings_table=_standings(), use_xg=False)
    cache.put_sports("Arsenal v Chelsea", sports)
    bundle = build_fixture_bundle(cache, "Arsenal v Chelsea")
    assert bundle["sports"]["home_team"]["name"] == "Arsenal"
    assert bundle["ready"]["sports"] is True
    assert bundle["ready"]["lines"] is False
