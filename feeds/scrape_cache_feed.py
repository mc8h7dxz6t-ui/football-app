"""Optional scrape sidecar — poll a local HTTP cache (no HTML in FVE process).

Run a separate scraper/cron that writes JSON to disk or serves HTTP; FVE only
reads the cache. Keeps scrape risk and ToS exposure out of the hot ingest path.

Env:
  FVE_SCRAPE_LINES_URL=http://127.0.0.1:8091/lines/{fixture_key}
  FVE_SCRAPE_LINES_TOKEN=optional-bearer
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List

from bookmakers import classify_bookmaker
from feeds.base import FeedAdapter
from pipeline.tick import PriceTick

_SIDE_MAP = {
    "home": ("Home", "Home"),
    "draw": ("Draw", "Draw"),
    "away": ("Away", "Away"),
}


def _scrape_url_template() -> str:
    return (os.environ.get("FVE_SCRAPE_LINES_URL") or "").strip()


def scrape_cache_enabled() -> bool:
    return bool(_scrape_url_template())


class ScrapeCacheFeed(FeedAdapter):
    name = "scrape-cache"
    enabled_by_default = False
    tier = "soft"

    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        tmpl = _scrape_url_template()
        if not tmpl:
            return []
        url = tmpl.format(
            fixture_key=fixture_key,
            fixture_key_encoded=urllib.request.quote(fixture_key, safe=""),
        )
        headers = {"User-Agent": "fve-scrape-cache/1.0", "Accept": "application/json"}
        token = (os.environ.get("FVE_SCRAPE_LINES_TOKEN") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=float(os.environ.get("FVE_SCRAPE_TIMEOUT_SEC", "8"))) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError):
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict):
            return []
        return _payload_to_ticks(fixture_key, payload)


def _payload_to_ticks(fixture_key: str, payload: Dict[str, Any]) -> List[PriceTick]:
    ticks: List[PriceTick] = []
    # Shape A: hibs-style best_odds_1x2
    best = payload.get("best_odds_1x2") or {}
    sources = payload.get("best_odds_source") or {}
    for side, (market, selection) in _SIDE_MAP.items():
        try:
            odds = float(best.get(side) or 0)
        except (TypeError, ValueError):
            continue
        if odds <= 1.0:
            continue
        bookmaker = str(sources.get(side) or payload.get("source") or "scrape-cache")
        ticks.append(
            PriceTick(
                fixture_key=fixture_key,
                market=market,
                selection=selection,
                odds=odds,
                bookmaker=bookmaker,
                source="scrape-cache",
                category=classify_bookmaker(bookmaker),
                meta={"upstream": payload.get("scrape_source") or "sidecar"},
            )
        )
    # Shape B: list of bookmaker rows
    for row in payload.get("all_bookmaker_odds") or payload.get("bookmakers") or []:
        if not isinstance(row, dict):
            continue
        bm = str(row.get("bookmaker") or row.get("name") or "unknown")
        for side, (market, selection) in _SIDE_MAP.items():
            try:
                odds = float(row.get(side) or row.get(f"odds_{side}") or 0)
            except (TypeError, ValueError):
                continue
            if odds <= 1.0:
                continue
            ticks.append(
                PriceTick(
                    fixture_key=fixture_key,
                    market=market,
                    selection=selection,
                    odds=odds,
                    bookmaker=bm,
                    source="scrape-cache",
                    category=classify_bookmaker(bm),
                    meta={"upstream": payload.get("scrape_source") or "sidecar"},
                )
            )
    return ticks
