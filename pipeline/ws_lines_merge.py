"""Merge delta line_update messages into a full lines view (WS clients)."""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional


def merge_changed_markets(
    shopped: Optional[Dict[str, Any]],
    changed: Dict[str, Any],
) -> Dict[str, Any]:
    """Deep-merge changed market legs into an existing shopped tree."""
    base = copy.deepcopy(shopped) if isinstance(shopped, dict) else {}
    for market, channels in (changed or {}).items():
        if not isinstance(channels, dict):
            continue
        market_node = base.setdefault(market, {})
        if not isinstance(market_node, dict):
            market_node = {}
            base[market] = market_node
        for channel, quote in channels.items():
            if isinstance(quote, dict):
                market_node[channel] = copy.deepcopy(quote)
    return base


def apply_line_update(
    lines: Optional[Dict[str, Any]],
    message: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Apply a bus/WS line_update onto a lines view (from snapshot or prior merge).

    Supports mode=delta (changed_markets) and mode=full (lines subtree).
    """
    out = copy.deepcopy(lines) if isinstance(lines, dict) else {}
    mode = (message.get("mode") or "").strip().lower()
    if mode == "full":
        full = message.get("lines")
        if isinstance(full, dict):
            out.update(copy.deepcopy(full))
        return out
    if mode == "delta" or message.get("changed_markets"):
        out["shopped"] = merge_changed_markets(out.get("shopped"), message.get("changed_markets") or {})
        if message.get("tick_count") is not None:
            out["tick_count"] = message["tick_count"]
        if message.get("sharp_fair_probs") is not None:
            out["sharp_fair_probs"] = message["sharp_fair_probs"]
        if not out.get("fixture_key") and message.get("fixture_key"):
            out["fixture_key"] = message["fixture_key"]
        return out
    # Legacy: bare update without mode
    if isinstance(message.get("lines"), dict):
        out.update(copy.deepcopy(message["lines"]))
    return out


def expand_line_update_for_client(message: Dict[str, Any], lines_state: Dict[str, Any]) -> Dict[str, Any]:
    """Merge delta into lines_state and return a client-friendly line_update."""
    merged = apply_line_update(lines_state, message)
    lines_state.clear()
    lines_state.update(merged)
    return {
        "type": "line_update",
        "mode": "full",
        "fixture_key": message.get("fixture_key") or merged.get("fixture_key"),
        "ts": message.get("ts"),
        "lines": merged,
    }
