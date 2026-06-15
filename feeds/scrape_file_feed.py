"""Read scraped lines JSON from a local directory (no HTTP server required)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from feeds.base import FeedAdapter
from feeds.scrape_parse import payload_to_ticks
from pipeline.tick import PriceTick


def _scrape_dir() -> Path | None:
    raw = (os.environ.get("FVE_SCRAPE_LINES_DIR") or "").strip()
    if not raw:
        return None
    return Path(raw)


def scrape_file_enabled() -> bool:
    d = _scrape_dir()
    return d is not None and d.is_dir()


def _fixture_filename(fixture_key: str) -> str:
    safe = re.sub(r"[^\w\s-]", "", fixture_key).strip().replace(" ", "_")
    return f"{safe}.json"


class ScrapeFileFeed(FeedAdapter):
    name = "scrape-file"
    enabled_by_default = False
    tier = "soft"

    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        base = _scrape_dir()
        if not base:
            return []
        for name in (_fixture_filename(fixture_key), f"{fixture_key}.json"):
            path = base / name
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                return payload_to_ticks(fixture_key, payload, source="scrape-file")
        return []
