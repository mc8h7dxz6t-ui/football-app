"""Pinnacle sharp line feed — panel/scrape cache or commercial API stub."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List

from engine.clv_benchmark import parse_pinnacle_1x2_from_panel
from feeds.base import FeedAdapter
from feeds.scrape_parse import payload_to_ticks
from pipeline.tick import PriceTick

_SIDE_MAP = {
    "home": ("Match Winner", "Home"),
    "draw": ("Match Winner", "Draw"),
    "away": ("Match Winner", "Away"),
}


def _pinnacle_scrape_url(fixture_key: str) -> str:
    tmpl = (os.environ.get("PINNACLE_SCRAPE_URL") or os.environ.get("FVE_SCRAPE_LINES_URL") or "").strip()
    if not tmpl:
        return ""
    return tmpl.format(
        fixture_key=fixture_key,
        fixture_key_encoded=urllib.request.quote(fixture_key, safe=""),
    )


def _ticks_from_pinnacle_triplet(fixture_key: str, triplet: Dict[str, Any]) -> List[PriceTick]:
    ticks: List[PriceTick] = []
    for side, (market, selection) in _SIDE_MAP.items():
        try:
            odds = float(triplet.get(side) or 0)
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
                bookmaker="Pinnacle",
                source="pinnacle",
                category="sharp",
                meta={"tier": "pinnacle"},
            )
        )
    return ticks


class PinnacleFeed(FeedAdapter):
    name = "pinnacle"
    enabled_by_default = bool(
        os.environ.get("PINNACLE_API_KEY")
        or os.environ.get("PINNACLE_SCRAPE_URL")
        or os.environ.get("FVE_SCRAPE_LINES_URL")
    )
    tier = "sharp"

    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        # Commercial API — Phase 2 when PINNACLE_API_KEY licensed
        if os.environ.get("PINNACLE_API_KEY"):
            pass

        panel = context.get("all_bookmaker_odds") or context.get("bookmaker_panel")
        if isinstance(panel, list):
            triplet = parse_pinnacle_1x2_from_panel(panel)
            ticks = _ticks_from_pinnacle_triplet(fixture_key, triplet)
            if ticks:
                return ticks

        url = _pinnacle_scrape_url(fixture_key)
        if not url:
            return []
        headers = {"User-Agent": "fve-pinnacle-feed/1.0", "Accept": "application/json"}
        token = (os.environ.get("FVE_SCRAPE_LINES_TOKEN") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=float(os.environ.get("FVE_SCRAPE_TIMEOUT_SEC", "8"))) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, dict):
            return []

        pin = parse_pinnacle_1x2_from_panel(
            payload.get("all_bookmaker_odds") or payload.get("bookmakers") or []
        )
        ticks = _ticks_from_pinnacle_triplet(fixture_key, pin)
        if ticks:
            return ticks
        return [t for t in payload_to_ticks(fixture_key, payload, source="pinnacle") if "pinnacle" in t.bookmaker.lower()]
