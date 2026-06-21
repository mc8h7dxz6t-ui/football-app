"""Feed SLA disclosure — enterprise benchmark vs actual scrape/budget-limited ingest."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from pipeline.rate_limits import get_budget

# Reference enterprise odds vendors (not licensed in base analytics product).
ENTERPRISE_BENCHMARKS: Dict[str, Dict[str, Any]] = {
    "sportradar": {
        "latency_p99_ms": 50,
        "uptime_sla_pct": 99.9,
        "delivery": "managed_stream",
        "license": "enterprise",
    },
    "genius_sports": {
        "latency_p99_ms": 75,
        "uptime_sla_pct": 99.5,
        "delivery": "managed_stream",
        "license": "enterprise",
    },
    "pinnacle_commercial": {
        "latency_p99_ms": 200,
        "uptime_sla_pct": 99.0,
        "delivery": "rest_or_stream",
        "license": "commercial",
    },
}

# What this stack actually runs.
ACTUAL_FEED_PROFILE: Dict[str, Any] = {
    "primary_mode": os.environ.get("FVE_FEED_MODE")
    or (
        "scrape"
        if os.environ.get("FVE_SCRAPE_HEAVY", "").strip().lower() in ("1", "true", "yes", "on")
        else "hibs"
        if os.environ.get("FVE_UPSTREAM_MODE", "").strip().lower() in ("hibs", "hibs-bet", "upstream")
        else "direct"
    ),
    "delivery": "scrape_and_budget_limited_apis",
    "enterprise_sla": False,
    "typical_latency_ms": "500-5000",
    "note": "Scrape sidecar + hourly API budgets — not Sportradar/Genius enterprise SLA.",
}


def feed_sla_status() -> Dict[str, Any]:
    """Honest feed SLA posture for institutional buyers."""
    budget = get_budget().status()
    disabled = [s.strip() for s in os.environ.get("DISABLED_FEEDS", "").split(",") if s.strip()]

    sources: List[Dict[str, Any]] = []
    for src, meta in budget.get("sources", {}).items():
        cap = meta.get("cap_per_hour")
        used = meta.get("used_this_hour")
        sources.append(
            {
                "source": src,
                "budget_limited": cap is not None and cap > 0,
                "cap_per_hour": cap,
                "used_this_hour": used,
                "remaining": meta.get("remaining"),
            }
        )

    return {
        "actual_profile": ACTUAL_FEED_PROFILE,
        "enterprise_benchmarks": ENTERPRISE_BENCHMARKS,
        "gap_summary": (
            "Enterprise odds SLA (Sportradar, Genius, Pinnacle commercial) not included. "
            "Ingest is scrape + budget-capped Matchbook/Odds API/API-Football."
        ),
        "api_budgets": budget,
        "disabled_feeds": disabled,
        "pinnacle_feed": {
            "configured": bool((os.environ.get("PINNACLE_API_KEY") or "").strip()),
            "mode": "commercial_api" if os.environ.get("PINNACLE_API_KEY") else "panel_or_scrape",
        },
        "betfair_feed": {
            "configured": bool((os.environ.get("BETFAIR_APP_KEY") or "").strip()),
            "streaming": False,
        },
        "evaluated_at": time.time(),
    }
