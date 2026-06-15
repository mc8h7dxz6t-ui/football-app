"""Tests for hibs-bet upstream feed (mocked HTTP)."""

from __future__ import annotations

from feeds.hibs_upstream_feed import HibsUpstreamFeed
from services.hibs_lines_client import HibsLinesClient


class _FakeClient(HibsLinesClient):
    def __init__(self, payload: dict) -> None:
        super().__init__(base_url="https://hibs.example")
        self._payload = payload

    def configured(self) -> bool:
        return True

    def fetch_fixture_lines(self, fixture_key: str) -> dict:
        out = dict(self._payload)
        out.setdefault("fixture_key", fixture_key)
        return out


def test_hibs_upstream_feed_maps_best_odds_to_ticks():
    feed = HibsUpstreamFeed(
        client=_FakeClient(
            {
                "best_odds_1x2": {"home": 2.1, "draw": 3.4, "away": 3.8},
                "best_odds_source": {"home": "Bet365", "draw": "Bet365", "away": "Bet365"},
            }
        )
    )
    ticks = feed.fetch_ticks("Arsenal v Chelsea", {})
    assert len(ticks) == 3
    homes = [t for t in ticks if t.market == "Home"]
    assert homes and homes[0].odds == 2.1
    assert homes[0].source == "hibs-upstream"


def test_hibs_upstream_feed_sports_context():
    feed = HibsUpstreamFeed(
        client=_FakeClient(
            {
                "fixture_id": 99,
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "home_stats": {"played": 10, "goals_for": 20, "goals_against": 5},
                "away_stats": {"played": 10, "goals_for": 8, "goals_against": 12},
            }
        )
    )
    sports = feed.fetch_sports_context("Arsenal v Chelsea")
    assert sports is not None
    assert sports["fixture_id"] == 99
    assert sports["home_stats"]["goals_for"] == 20
