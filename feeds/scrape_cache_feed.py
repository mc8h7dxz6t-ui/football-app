"""Optional scrape sidecar — poll a local HTTP cache (no HTML in FVE process)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List

from feeds.base import FeedAdapter
from feeds.scrape_parse import payload_to_ticks
from pipeline.tick import PriceTick


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
        return payload_to_ticks(fixture_key, payload, source="scrape-cache")
