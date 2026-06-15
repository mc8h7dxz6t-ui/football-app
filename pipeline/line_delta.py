"""Delta line updates — publish changed markets only on the wire."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

_LAST_SHOPPED: Dict[str, Dict[str, Any]] = {}


def _delta_enabled() -> bool:
    return os.environ.get("FVE_WS_DELTA_UPDATES", "1").strip().lower() in ("1", "true", "yes", "on")


def diff_shopped(
    previous: Optional[Dict[str, Any]],
    current: Dict[str, Any],
    *,
    epsilon: float = 0.001,
) -> Dict[str, Any]:
    """Return markets whose best quote changed (per channel leg)."""
    if not previous:
        return dict(current)
    changed: Dict[str, Any] = {}
    for market, channels in current.items():
        if not isinstance(channels, dict):
            continue
        prev_channels = previous.get(market) if isinstance(previous.get(market), dict) else {}
        market_delta: Dict[str, Any] = {}
        for channel, quote in channels.items():
            if not isinstance(quote, dict):
                continue
            prev_q = prev_channels.get(channel) if isinstance(prev_channels, dict) else {}
            try:
                odds = float(quote.get("odds") or 0)
                prev_odds = float((prev_q or {}).get("odds") or 0)
            except (TypeError, ValueError):
                odds, prev_odds = 0.0, 0.0
            if abs(odds - prev_odds) >= epsilon or quote.get("bookmaker") != (prev_q or {}).get("bookmaker"):
                market_delta[channel] = quote
        if market_delta:
            changed[market] = market_delta
    return changed


def build_line_update_message(
    fixture_key: str,
    lines_view: Dict[str, Any],
    *,
    force_full: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Build a line_update bus/WS payload.

    Default: delta (`changed_markets` only). Set FVE_WS_DELTA_UPDATES=0 for full `lines` tree.
    """
    shopped = lines_view.get("shopped") or {}
    if not shopped:
        return None

    prev = _LAST_SHOPPED.get(fixture_key)
    changed = diff_shopped(prev, shopped)
    _LAST_SHOPPED[fixture_key] = shopped

    if not changed:
        return None

    ts = time.time()
    if force_full or not _delta_enabled():
        return {
            "type": "line_update",
            "mode": "full",
            "fixture_key": fixture_key,
            "ts": ts,
            "lines": lines_view,
        }

    return {
        "type": "line_update",
        "mode": "delta",
        "fixture_key": fixture_key,
        "ts": ts,
        "changed_markets": changed,
        "tick_count": lines_view.get("tick_count"),
        "sharp_fair_probs": lines_view.get("sharp_fair_probs"),
    }


def reset_line_delta_state(fixture_key: str | None = None) -> None:
    """Test helper — clear remembered shopped snapshots."""
    if fixture_key is None:
        _LAST_SHOPPED.clear()
    else:
        _LAST_SHOPPED.pop(fixture_key, None)
