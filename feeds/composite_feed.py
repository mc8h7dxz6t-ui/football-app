"""Prioritized feed chain — try sources in order until 1X2 complete."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List

from feeds.base import FeedAdapter
from feeds.feed_utils import has_complete_1x2, merge_ticks_union
from pipeline.circuit_breaker import breakers
from pipeline.rate_limits import get_budget
from pipeline.tick import PriceTick

log = logging.getLogger(__name__)

_BUDGET_SOURCE = {
    "matchbook": "matchbook",
    "odds-backup": "odds_api",
    "the-odds-api": "odds_api",
    "api-football": "api_football",
    "hibs-upstream": "hibs_upstream",
    "scrape-file": "scrape",
    "scrape-cache": "scrape",
}

_LAST_STATUS: Dict[str, Any] = {
    "chain": [],
    "last_fixture_key": "",
    "sources_tried": [],
    "sources_with_ticks": [],
    "complete_1x2": False,
    "tick_count": 0,
    "updated_at": 0.0,
}


def composite_feed_status() -> Dict[str, Any]:
    return dict(_LAST_STATUS)


from feeds.scrape_mode import scrape_mode_enabled


def _parse_chain() -> List[str]:
    raw = (os.environ.get("FVE_FEED_CHAIN") or "").strip()
    if raw:
        return [s.strip() for s in raw.split(",") if s.strip()]
    if scrape_mode_enabled():
        return ["hibs-upstream", "scrape-file", "scrape-cache"]
    default = ["matchbook", "odds-backup", "api-football"]
    if os.environ.get("FVE_SCRAPE_LINES_URL", "").strip() or os.environ.get("FVE_SCRAPE_LINES_DIR", "").strip():
        default.append("scrape-file")
        default.append("scrape-cache")
    return default


class CompositeFeed(FeedAdapter):
    """Single adapter that walks a prioritized backup chain."""

    name = "composite"
    enabled_by_default = True
    tier = "exchange"

    def __init__(self, children: List[FeedAdapter], *, chain: List[str] | None = None) -> None:
        self._children = {c.name: c for c in children}
        self._chain = chain or _parse_chain()
        _LAST_STATUS["chain"] = list(self._chain)

    @property
    def poll_interval_sec(self) -> float:
        secs = [self._children[n].poll_interval_sec for n in self._chain if n in self._children]
        return min(secs) if secs else 5.0

    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        accumulated: List[PriceTick] = []
        tried: List[str] = []
        with_ticks: List[str] = []
        for name in self._chain:
            child = self._children.get(name)
            if not child:
                continue
            br = breakers.get(name)
            if not br.allow_call():
                log.debug("composite skip %s circuit open", name)
                continue
            budget_key = _BUDGET_SOURCE.get(name, name)
            if budget_key not in ("scrape", "hibs_upstream") and not get_budget().allow(budget_key):
                log.debug("composite skip %s budget exhausted", name)
                continue
            tried.append(name)
            br.call_started()
            try:
                batch = child.fetch_ticks(fixture_key, context)
                if budget_key not in ("scrape", "hibs_upstream"):
                    get_budget().record(budget_key)
                br.record_success()
            except Exception as exc:
                br.record_failure(str(exc))
                log.warning("composite child %s failed fixture=%s: %s", name, fixture_key, exc)
                batch = []
            if batch:
                with_ticks.append(name)
                accumulated = merge_ticks_union(accumulated, batch)
            if has_complete_1x2(accumulated):
                break

        complete = has_complete_1x2(accumulated)
        _LAST_STATUS.update(
            {
                "last_fixture_key": fixture_key,
                "sources_tried": tried,
                "sources_with_ticks": with_ticks,
                "complete_1x2": complete,
                "tick_count": len(accumulated),
                "updated_at": time.time(),
            }
        )
        return accumulated

    def fetch_sports_context(self, fixture_key: str) -> Dict[str, Any] | None:
        for name in ("api-football", "hibs-upstream", "scrape-cache"):
            child = self._children.get(name)
            if not child or not hasattr(child, "fetch_sports_context"):
                continue
            ctx = child.fetch_sports_context(fixture_key)  # type: ignore[attr-defined]
            if ctx:
                return ctx
        return None
