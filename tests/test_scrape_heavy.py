"""Scrape-heavy feed mode tests."""

from __future__ import annotations

import feeds.registry as registry_mod
from feeds.scrape_parse import payload_to_ticks
from feeds.scrape_file_feed import ScrapeFileFeed, scrape_file_enabled


def test_scrape_mode_registry(monkeypatch):
    monkeypatch.setenv("FVE_FEED_MODE", "scrape")
    monkeypatch.delenv("FVE_UPSTREAM_MODE", raising=False)
    reg = registry_mod.build_default_registry()
    names = [f.name for f in reg.enabled()]
    assert names == ["composite"]


def test_payload_to_ticks_bookmaker_rows():
    ticks = payload_to_ticks(
        "A v B",
        {
            "all_bookmaker_odds": [
                {"bookmaker": "Bet365", "home": 2.1, "draw": 3.4, "away": 3.2},
            ],
        },
    )
    assert len(ticks) == 3


def test_scrape_file_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("FVE_SCRAPE_LINES_DIR", str(tmp_path))
    assert scrape_file_enabled() is True
    feed = ScrapeFileFeed()
    (tmp_path / "Arsenal_v_Chelsea.json").write_text(
        '{"best_odds_1x2":{"home":2.0,"draw":3.5,"away":3.3}}',
        encoding="utf-8",
    )
    ticks = feed.fetch_ticks("Arsenal v Chelsea", {})
    assert len(ticks) == 3
