"""Sports refresh via feed registry (hibs upstream)."""

from __future__ import annotations

from pipeline.feed_sports import normalize_feed_sports, sports_from_registry
from feeds.registry import FeedRegistry


class _HibsSportsFeed:
    name = "hibs-upstream"
    enabled_by_default = True

    def fetch_sports_context(self, fixture_key: str):
        return {
            "fixture_id": 99,
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "home_stats": {"played": 30, "goals_for": 50, "goals_against": 20},
            "away_stats": {"played": 30, "goals_for": 40, "goals_against": 30},
            "source": "hibs-upstream",
        }


def test_sports_from_registry():
    reg = FeedRegistry([_HibsSportsFeed()])  # type: ignore[list-item]
    sports = sports_from_registry("Arsenal v Chelsea", reg)
    assert sports is not None
    assert sports["data_quality"]["home_ok"] is True
    assert sports["sources"] == ["hibs-upstream"]


def test_normalize_feed_sports():
    out = normalize_feed_sports(
        {
            "home_stats": {"played": 10},
            "away_stats": {"played": 8},
            "source": "test",
        }
    )
    assert out["data_quality"]["away_ok"] is True
