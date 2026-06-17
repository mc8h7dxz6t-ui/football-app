"""Venue router — aggregate live 1X2 marks (I3 recording)."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from inplay.evidence_store import record_marks_snapshot

DEFAULT_VENUES = ("pinnacle", "betfair", "matchbook")


def _venue_stub(name: str, fixture_id: int) -> Optional[Dict[str, Any]]:
    """Placeholder until venue adapters return live odds."""
    env_key = f"HIBS_INPLAY_{name.upper()}_ENABLED"
    if (os.getenv(env_key) or "").strip().lower() in ("0", "false", "no", "off"):
        return None
    # Simulated marks for dev/test when HIBS_INPLAY_MOCK_MARKS=1
    if (os.getenv("HIBS_INPLAY_MOCK_MARKS") or "").strip().lower() in ("1", "true", "yes", "on"):
        base = 2.0 + (fixture_id % 7) * 0.05
        return {"home": base, "draw": 3.4, "away": base + 0.3, "venue": name}
    return None


def fetch_exchange_marks(fixture_id: int) -> Dict[str, Any]:
    """Aggregate live 1X2 across Pinnacle / Betfair / Matchbook; record I3 snapshot."""
    marks: Dict[str, Any] = {}
    for venue in DEFAULT_VENUES:
        row = _venue_stub(venue, fixture_id)
        if row:
            marks[venue] = row
    venues_expected = len(DEFAULT_VENUES)
    venues_ok = len(marks)
    coverage = record_marks_snapshot(
        fixture_id=fixture_id,
        venues_expected=venues_expected,
        venues_ok=venues_ok,
        marks=marks,
    )
    return {
        "fixture_id": fixture_id,
        "venues": marks,
        "venues_ok": venues_ok,
        "venues_expected": venues_expected,
        "coverage_pct": coverage,
    }


def record_window_close_clv(
    *,
    fixture_id: int,
    outcome: str,
    odds_taken: float,
    closing_marks: Dict[str, Any],
    window_close_at: Optional[str] = None,
) -> str:
    """I4 — fair close from best available venue de-vig (simplified)."""
    from inplay.evidence_store import record_inplay_clv

    home_odds = None
    for venue_mark in closing_marks.values():
        if isinstance(venue_mark, dict) and venue_mark.get("home"):
            home_odds = float(venue_mark["home"])
            break
    fair = None
    if outcome == "home" and home_odds and home_odds > 1.0:
        fair = home_odds * 1.02  # margin lift placeholder
    return record_inplay_clv(
        fixture_id=fixture_id,
        outcome=outcome,
        odds_taken=odds_taken,
        odds_close_fair=fair,
        window_close_at=window_close_at,
    )
