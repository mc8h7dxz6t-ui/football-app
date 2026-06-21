"""Institutional competitive-gap disclosure — honest buyer-facing posture."""

from __future__ import annotations

import os
from typing import Any, Dict, List

from engine.clv_benchmark import clv_benchmark_disclosure
from feeds.feed_sla import feed_sla_status


def execution_disclosure() -> Dict[str, Any]:
    """Sub-100ms exchange execution is not in analytics license."""
    from execution.risk import RiskConfig

    risk = RiskConfig()
    exec_disabled = os.environ.get("EXECUTION_DISABLED", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    return {
        "mode": "analytics",
        "execution_disabled": exec_disabled,
        "live_enabled": risk.live_enabled(),
        "sub_100ms_exchange": False,
        "co_location": False,
        "institutional_benchmark": {
            "latency_target_ms": 100,
            "venue": "betfair_exchange",
            "note": "Harvested Execution / live routing not in base analytics license.",
        },
        "actual": {
            "auto_trade": risk.auto_trade,
            "kill_switch": risk.kill_switch,
            "max_stake_gbp": risk.max_stake_gbp,
        },
    }


def inplay_depth_disclosure() -> Dict[str, Any]:
    """Betfair in-play depth + co-location — research display only."""
    mock = (os.getenv("HIBS_INPLAY_MOCK_MARKS") or "").strip().lower() in ("1", "true", "yes", "on")
    return {
        "prematch_only_product": True,
        "inplay_mode": "research_display",
        "betfair_l2_depth": False,
        "co_location": False,
        "mock_marks_enabled": mock,
        "institutional_benchmark": {
            "betfair_inplay_depth": True,
            "co_location": True,
            "note": "Live exchange depth requires commercial Betfair Stream + co-lo; not shipped.",
        },
    }


def competitive_gaps() -> List[Dict[str, str]]:
    """Plain-language gap table matching data-room honesty."""
    return [
        {
            "gap": "Pinnacle closing line as CLV benchmark",
            "institutional_standard": "Pinnacle close (commercial API or panel)",
            "current": "Tier ladder: Pinnacle panel → exchange → fair synthetic → API-Football",
            "status": "bridged_with_disclosure",
        },
        {
            "gap": "Betfair in-play depth + co-location",
            "institutional_standard": "L2 order book + low-latency venue access",
            "current": "Pre-match FVE; in-play marks are research/display (I3 telemetry)",
            "status": "research_only",
        },
        {
            "gap": "Sub-100ms exchange execution",
            "institutional_standard": "Co-located Betfair/Matchbook order routing",
            "current": "Analytics license; EXECUTION_DISABLED; paper ledger only",
            "status": "not_in_license",
        },
        {
            "gap": "Enterprise odds SLA (Sportradar, etc.)",
            "institutional_standard": "99.9% managed stream, sub-100ms p99",
            "current": "Scrape sidecar + hourly API budgets",
            "status": "budget_limited",
        },
    ]


def institutional_disclosure() -> Dict[str, Any]:
    """Aggregate institutional honesty block for /health and data room."""
    return {
        "product_posture": "analytics_only",
        "competitive_gaps": competitive_gaps(),
        "clv_benchmark": clv_benchmark_disclosure(),
        "feed_sla": feed_sla_status(),
        "execution": execution_disclosure(),
        "inplay_depth": inplay_depth_disclosure(),
    }
