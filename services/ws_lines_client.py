"""FVE WebSocket lines client helpers — delta merge for Python consumers."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from pipeline.ws_lines_merge import apply_line_update


class LinesSessionState:
    """Tracks merged lines from snapshot + delta line_update messages."""

    def __init__(self) -> None:
        self.fixture_key: str = ""
        self.lines: Dict[str, Any] = {}
        self.bundle: Dict[str, Any] = {}

    def on_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        msg_type = message.get("type")
        if msg_type == "snapshot":
            self.fixture_key = str(message.get("fixture_key") or "")
            self.bundle = dict(message)
            lines = message.get("lines")
            self.lines = dict(lines) if isinstance(lines, dict) else {}
            return message
        if msg_type == "line_update":
            self.lines = apply_line_update(self.lines, message)
            if self.bundle:
                self.bundle = {**self.bundle, "lines": self.lines}
            return {**message, "lines": self.lines}
        return message


def merge_ws_message(state: Dict[str, Any], message: Dict[str, Any]) -> Dict[str, Any]:
    """Functional helper — merge one WS message into a mutable state dict."""
    session = LinesSessionState()
    session.lines = dict(state.get("lines") or {})
    session.fixture_key = str(state.get("fixture_key") or "")
    out = session.on_message(message)
    state["lines"] = session.lines
    state["fixture_key"] = session.fixture_key
    return out


def run_ws_lines_loop(
    fixture_key: str,
    *,
    base_ws_url: str = "ws://localhost:8000",
    on_update: Callable[[Dict[str, Any]], None],
    raw_delta: bool = False,
) -> None:
    """
    Blocking WS consumer (requires `websocket-client`).

    When raw_delta=False, applies delta merge before on_update (same as hub expand mode).
    """
    try:
        import websocket  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("pip install websocket-client to use run_ws_lines_loop") from exc

    import os
    from urllib.parse import quote

    url = f"{base_ws_url.rstrip('/')}/ws/lines/{quote(fixture_key)}"
    state = LinesSessionState()

    def _on_message(_ws: Any, raw: str) -> None:
        msg = json.loads(raw)
        if not raw_delta and msg.get("type") == "line_update" and msg.get("mode") == "delta":
            msg = {**msg, "lines": apply_line_update(state.lines, msg)}
        state.on_message(msg)
        on_update(msg)

    ws = websocket.WebSocketApp(url, on_message=_on_message)
    ws.run_forever()
