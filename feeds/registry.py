"""Compose enabled feed adapters."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from feeds.api_football_feed import ApiFootballFeed
from feeds.betfair_feed import BetfairFeed
from feeds.base import FeedAdapter
from feeds.composite_feed import CompositeFeed
from feeds.hibs_upstream_feed import HibsUpstreamFeed
from feeds.matchbook_feed import MatchbookFeed
from feeds.odds_api_feed import OddsApiFeed
from feeds.odds_backup_feed import OddsBackupFeed
from feeds.pinnacle_feed import PinnacleFeed
from feeds.scrape_cache_feed import ScrapeCacheFeed, scrape_cache_enabled
from feeds.scrape_file_feed import ScrapeFileFeed, scrape_file_enabled
from feeds.scrape_mode import feed_mode, scrape_mode_enabled


class FeedRegistry:
    def __init__(self, feeds: List[FeedAdapter]) -> None:
        self._feeds = {f.name: f for f in feeds}

    def enabled(self) -> List[FeedAdapter]:
        disabled = {s.strip() for s in os.environ.get("DISABLED_FEEDS", "").split(",") if s.strip()}
        enabled_extra = {s.strip() for s in os.environ.get("ENABLED_FEEDS", "").split(",") if s.strip()}
        out: List[FeedAdapter] = []
        for f in self._feeds.values():
            if f.name in disabled:
                continue
            if f.name in ("scrape-cache", "scrape-file") and (
                (f.name == "scrape-cache" and scrape_cache_enabled())
                or (f.name == "scrape-file" and scrape_file_enabled())
            ):
                out.append(f)
                continue
            if not f.enabled_by_default and f.name not in enabled_extra:
                continue
            out.append(f)
        return out

    def get(self, name: str) -> Optional[FeedAdapter]:
        return self._feeds.get(name)


def _build_scrape_registry() -> FeedRegistry:
    """Zero paid API on FVE — hibs upstream + local scrape files/HTTP only."""
    children: List[FeedAdapter] = []
    if HibsUpstreamFeed.upstream_mode_enabled() or (os.environ.get("HIBS_UPSTREAM_BASE_URL") or "").strip():
        children.append(HibsUpstreamFeed())
    children.append(ScrapeFileFeed())
    children.append(ScrapeCacheFeed())
    chain = (os.environ.get("FVE_FEED_CHAIN") or "hibs-upstream,scrape-file,scrape-cache").strip()
    return FeedRegistry([CompositeFeed(children, chain=[s.strip() for s in chain.split(",") if s.strip()])])


def _build_separate_registry() -> FeedRegistry:
    children: List[FeedAdapter] = [
        MatchbookFeed(),
        OddsBackupFeed(),
        ApiFootballFeed(),
        ScrapeFileFeed(),
        ScrapeCacheFeed(),
    ]
    return FeedRegistry([CompositeFeed(children)])


def build_default_registry() -> FeedRegistry:
    if scrape_mode_enabled():
        return _build_scrape_registry()

    if HibsUpstreamFeed.upstream_mode_enabled():
        return FeedRegistry([HibsUpstreamFeed()])

    mode = feed_mode()
    if scrape_mode_enabled():
        return _build_scrape_registry()
    if mode in ("separate", "composite", "chain"):
        return _build_separate_registry()

    feeds: List[FeedAdapter] = [
        MatchbookFeed(),
        BetfairFeed(),
        PinnacleFeed(),
        ApiFootballFeed(),
    ]
    if os.environ.get("ENABLE_ODDS_API_FEED", "").strip().lower() in ("1", "true", "yes", "on"):
        feeds.append(OddsApiFeed())
    if scrape_cache_enabled():
        feeds.append(ScrapeCacheFeed())
    if scrape_file_enabled():
        feeds.append(ScrapeFileFeed())
    return FeedRegistry(feeds)
