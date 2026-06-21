"""In-play exchange depth — research-grade disclosure (not execution routing)."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from inplay.router import DEFAULT_VENUES, fetch_exchange_marks

# Institutional reference — not available in analytics product.
_DEPTH_BENCHMARK = {
    "betfair_l2": True,
    "co_location": True,
    "typical_fill_latency_ms": 50,
}


def _env_bool(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def exchange_depth_snapshot(fixture_id: int) -> Dict[str, Any]:
    """
  Research snapshot of venue marks + depth availability.

  Does not route orders or expose L2 books — honest institutional disclosure.
    """
    marks_payload = fetch_exchange_marks(fixture_id)
    venues: Dict[str, Any] = marks_payload.get("venues") or {}

    venue_rows: List[Dict[str, Any]] = []
    for name in DEFAULT_VENUES:
        mark = venues.get(name)
        has_mark = isinstance(mark, dict) and any(mark.get(s) for s in ("home", "draw", "away"))
        venue_rows.append(
            {
                "venue": name,
                "mark_available": has_mark,
                "l2_depth_available": False,
                "co_location": False,
                "mode": "mock" if _env_bool("HIBS_INPLAY_MOCK_MARKS") and has_mark else "stub",
                "mark": mark if has_mark else None,
            }
        )

    betfair_configured = bool((os.getenv("BETFAIR_APP_KEY") or "").strip())
    return {
        "fixture_id": fixture_id,
        "research_only": True,
        "execution_routing": False,
        "venues": venue_rows,
        "venues_ok": marks_payload.get("venues_ok"),
        "venues_expected": marks_payload.get("venues_expected"),
        "coverage_pct": marks_payload.get("coverage_pct"),
        "betfair_configured": betfair_configured,
        "betfair_stream_connected": False,
        "institutional_benchmark": _DEPTH_BENCHMARK,
        "gap_note": (
            "Pre-match FVE is production; live depth is display/telemetry. "
            "Betfair L2 + co-lo not included in analytics license."
        ),
        "evaluated_at": time.time(),
    }


def depth_health_slice() -> Dict[str, Any]:
    """Compact depth posture for /health."""
    return {
        "research_only": True,
        "l2_depth": False,
        "co_location": False,
        "betfair_configured": bool((os.getenv("BETFAIR_APP_KEY") or "").strip()),
        "mock_marks": _env_bool("HIBS_INPLAY_MOCK_MARKS"),
    }
