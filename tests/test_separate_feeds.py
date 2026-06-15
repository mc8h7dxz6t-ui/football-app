"""Tests for separate feed chain + scrape cache."""

from __future__ import annotations

from typing import Any, Dict, List

import feeds.registry as registry_mod
from feeds.composite_feed import CompositeFeed
from feeds.feed_utils import has_complete_1x2, merge_ticks_union
from feeds.scrape_cache_feed import ScrapeCacheFeed, _payload_to_ticks
from pipeline.tick import PriceTick


def test_merge_ticks_union_keeps_best_odds():
    a = PriceTick("x v y", "Home", "Home", 2.1, "A", "test", category="soft")
    b = PriceTick("x v y", "Home", "Home", 2.2, "A", "test", category="soft")
    merged = merge_ticks_union([a], [b])
    assert len(merged) == 1
    assert merged[0].odds == 2.2


def test_has_complete_1x2():
    ticks = [
        PriceTick("x", "Home", "Home", 2.0, "B", "t", category="soft"),
        PriceTick("x", "Draw", "Draw", 3.0, "B", "t", category="soft"),
        PriceTick("x", "Away", "Away", 4.0, "B", "t", category="soft"),
    ]
    assert has_complete_1x2(ticks)


def test_scrape_payload_to_ticks_best_odds_shape():
    ticks = _payload_to_ticks(
        "Arsenal v Chelsea",
        {
            "best_odds_1x2": {"home": 2.1, "draw": 3.4, "away": 3.2},
            "best_odds_source": {"home": "Bet365", "draw": "Bet365", "away": "Bet365"},
            "scrape_source": "oddschecker-sidecar",
        },
    )
    assert len(ticks) == 3
    assert ticks[0].source == "scrape-cache"


class _StubFeed:
    def __init__(self, name: str, ticks: List[PriceTick]) -> None:
        self.name = name
        self.tier = "soft"
        self._ticks = ticks
        self.calls = 0

    @property
    def poll_interval_sec(self) -> float:
        return 1.0

    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        self.calls += 1
        return list(self._ticks)


def test_composite_stops_when_1x2_complete(monkeypatch):
    monkeypatch.setenv("FVE_FEED_CHAIN", "primary,backup")
    home = PriceTick("A v B", "Home", "Home", 2.0, "X", "p", category="exchange")
    draw = PriceTick("A v B", "Draw", "Draw", 3.0, "X", "p", category="exchange")
    away = PriceTick("A v B", "Away", "Away", 4.0, "X", "p", category="exchange")
    primary = _StubFeed("primary", [home, draw, away])
    backup = _StubFeed("backup", [PriceTick("A v B", "Home", "Home", 9.9, "Y", "b", category="soft")])
    comp = CompositeFeed([primary, backup], chain=["primary", "backup"])
    out = comp.fetch_ticks("A v B", {})
    assert has_complete_1x2(out)
    assert backup.calls == 0


def test_separate_feed_mode_registry(monkeypatch):
    monkeypatch.delenv("FVE_UPSTREAM_MODE", raising=False)
    monkeypatch.delenv("HIBS_UPSTREAM_BASE_URL", raising=False)
    monkeypatch.setenv("FVE_FEED_MODE", "separate")
    reg = registry_mod.build_default_registry()
    names = [f.name for f in reg.enabled()]
    assert names == ["composite"]


def test_scrape_cache_enabled_when_url_set(monkeypatch):
    monkeypatch.delenv("FVE_UPSTREAM_MODE", raising=False)
    monkeypatch.delenv("FVE_FEED_MODE", raising=False)
    monkeypatch.setenv("FVE_SCRAPE_LINES_URL", "http://127.0.0.1:8091/lines/{fixture_key}")
    reg = registry_mod.build_default_registry()
    names = {f.name for f in reg.enabled()}
    assert "scrape-cache" in names
