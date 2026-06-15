"""Shared FVE test fixtures — in-memory cache/bus, no Redis or book APIs."""

from __future__ import annotations

import pytest

from api import ws_hub as ws_hub_mod
from pipeline import cache as cache_mod
from pipeline import line_bus as line_bus_mod
from pipeline.cache import LineCache
from pipeline.line_bus import LineBus
from pipeline.tick import PriceTick

_FIXTURE_KEY = "Arsenal v Chelsea"
_NO_REDIS = "redis://127.0.0.1:59999/0"


def sample_team_stats(*, strong: bool = True) -> dict:
    """Minimal standings-shaped stats for match_model / value-scan."""
    if strong:
        return {
            "played": 30,
            "goals_for": 50,
            "goals_against": 20,
            "home_played": 15,
            "home_goals_for": 28,
            "home_goals_against": 8,
            "away_played": 15,
            "away_goals_for": 22,
            "away_goals_against": 12,
        }
    return {
        "played": 30,
        "goals_for": 20,
        "goals_against": 50,
        "home_played": 15,
        "home_goals_for": 12,
        "home_goals_against": 22,
        "away_played": 15,
        "away_goals_for": 8,
        "away_goals_against": 28,
    }


def seed_fixture_cache(
    cache: LineCache,
    fixture_key: str = _FIXTURE_KEY,
    *,
    with_sports: bool = True,
) -> None:
    ticks = [
        PriceTick(fixture_key, "Home", "Home", 2.35, "Bet365", "test", category="soft"),
        PriceTick(fixture_key, "Draw", "Draw", 3.5, "Bet365", "test", category="soft"),
        PriceTick(fixture_key, "Away", "Away", 3.2, "Bet365", "test", category="soft"),
        PriceTick(fixture_key, "Home", "Home", 2.2, "Betfair", "test", category="exchange"),
        PriceTick(fixture_key, "Draw", "Draw", 3.45, "Betfair", "test", category="exchange"),
        PriceTick(fixture_key, "Away", "Away", 3.15, "Betfair", "test", category="exchange"),
    ]
    cache.merge_ticks(fixture_key, ticks, source="test", feed_name="test")
    if with_sports:
        home_stats = sample_team_stats(strong=True)
        away_stats = sample_team_stats(strong=False)
        cache.put_sports(
            fixture_key,
            {
                "fixture_id": 12345,
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "home_stats": home_stats,
                "away_stats": away_stats,
                "data_quality": {
                    "home_ok": bool(home_stats.get("played")),
                    "away_ok": bool(away_stats.get("played")),
                },
            },
        )


@pytest.fixture()
def fixture_key() -> str:
    return _FIXTURE_KEY


@pytest.fixture()
def memory_cache(monkeypatch) -> LineCache:
    """Isolated in-memory LineCache + line bus + WS hub."""
    cache = LineCache(redis_url=_NO_REDIS)
    bus = LineBus(redis_url=_NO_REDIS)
    monkeypatch.setattr(cache_mod, "_cache", cache)
    monkeypatch.setattr(line_bus_mod, "_bus", bus)
    monkeypatch.setattr(ws_hub_mod, "_hub", None)
    return cache


@pytest.fixture()
def api_client(memory_cache):
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as client:
        yield client
