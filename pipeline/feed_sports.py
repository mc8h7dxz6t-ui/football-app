"""Sports context from feed adapters (hibs upstream) before API-Football."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from feeds.registry import FeedRegistry, build_default_registry
from feeds.scrape_mode import scrape_mode_enabled


def normalize_feed_sports(raw: Dict[str, Any]) -> Dict[str, Any]:
    home = raw.get("home_stats") if isinstance(raw.get("home_stats"), dict) else {}
    away = raw.get("away_stats") if isinstance(raw.get("away_stats"), dict) else {}
    ttl = int(raw.get("ttl_sec") or os.environ.get("SPORTS_CACHE_TTL_SEC", "3600"))
    source = str(raw.get("source") or "feed")
    return {
        "fixture_id": raw.get("fixture_id"),
        "home_team": raw.get("home_team"),
        "away_team": raw.get("away_team"),
        "home_stats": home,
        "away_stats": away,
        "kickoff_iso": raw.get("kickoff_iso"),
        "league_name": raw.get("league"),
        "data_quality": raw.get("data_quality")
        or {
            "home_ok": bool(home.get("played")),
            "away_ok": bool(away.get("played")),
        },
        "sources": list(raw.get("sources") or [source]),
        "updated_at": float(raw.get("updated_at") or time.time()),
        "ttl_sec": ttl,
    }


def sports_from_registry(
    fixture_key: str,
    registry: Optional[FeedRegistry] = None,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    reg = registry or build_default_registry()
    for feed in reg.enabled():
        fetch = getattr(feed, "fetch_sports_context", None)
        if not callable(fetch):
            continue
        raw = fetch(fixture_key)
        if isinstance(raw, dict) and raw.get("home_stats") and raw.get("away_stats"):
            return normalize_feed_sports(raw)
    if scrape_mode_enabled() and context:
        from scrapers.fotmob_client import sports_for_fixture

        raw = sports_for_fixture(fixture_key, context)
        if raw:
            return normalize_feed_sports(raw)
    return None
