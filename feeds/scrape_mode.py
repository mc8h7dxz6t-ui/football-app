"""Feed mode helpers — scrape-heavy / zero paid API."""

from __future__ import annotations

import os


def feed_mode() -> str:
    return (os.environ.get("FVE_FEED_MODE") or "").strip().lower()


def scrape_mode_enabled() -> bool:
    if feed_mode() in ("scrape", "scrape-heavy", "zero-api"):
        return True
    return os.environ.get("FVE_SCRAPE_HEAVY", "").strip().lower() in ("1", "true", "yes", "on")


def scrape_watchlist_enabled() -> bool:
    if scrape_mode_enabled():
        return True
    if os.environ.get("FVE_SCRAPE_WATCHLIST", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    key = (os.environ.get("API_SPORTS_KEY") or os.environ.get("API_FOOTBALL_KEY") or "").strip()
    auto = os.environ.get("FVE_AUTO_WATCHLIST", "1").strip().lower() not in ("0", "false", "no")
    return auto and not key
