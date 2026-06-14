"""WebSocket hub — push line snapshots/updates; clients never poll book APIs."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from typing import Any, DefaultDict, Deque, Dict, Set

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from pipeline.cache import get_cache
from pipeline.ingest import build_fixture_bundle, refresh_sports_context
from pipeline.line_bus import get_line_bus
from pipeline.ws_lines_merge import expand_line_update_for_client

log = logging.getLogger(__name__)

_WS_SEND_TIMEOUT_SEC = float(os.environ.get("WS_SEND_TIMEOUT_SEC", "2.0"))
_WS_MAX_PENDING_SENDS = int(os.environ.get("WS_MAX_PENDING_SENDS", "8"))


def _client_delta_mode() -> bool:
    """When false (default), hub expands delta bus messages to full lines for WS clients."""
    return os.environ.get("FVE_WS_CLIENT_DELTA", "0").strip().lower() in ("1", "true", "yes", "on")


class WsLineHub:
    def __init__(self) -> None:
        self._rooms: DefaultDict[str, Set[WebSocket]] = defaultdict(set)
        self._pending: Dict[int, int] = {}
        self._lines_state: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._started = False
        self._started_at = time.time()
        self._bus_messages = 0
        self._broadcasts = 0
        self._sends_ok = 0
        self._sends_fail = 0
        self._backpressure_drops = 0
        self._connect_count = 0
        self._disconnect_count = 0
        self._bus_ts: Deque[float] = deque(maxlen=5000)

    def ensure_started(self) -> None:
        if self._started:
            return
        bus = get_line_bus()
        bus.start_listener(self._on_bus_message)
        self._started = True
        self._started_at = time.time()

    def _record_bus_message(self) -> None:
        now = time.time()
        self._bus_messages += 1
        self._bus_ts.append(now)

    def _on_bus_message(self, fixture_key: str, message: Dict[str, Any]) -> None:
        self._record_bus_message()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.broadcast(fixture_key, message))

    def _prepare_client_message(self, fixture_key: str, message: Dict[str, Any]) -> Dict[str, Any]:
        if message.get("type") != "line_update":
            return message
        if _client_delta_mode():
            return message
        state = self._lines_state.setdefault(fixture_key, {})
        return expand_line_update_for_client(message, state)

    async def _send_json(self, fixture_key: str, websocket: WebSocket, message: Dict[str, Any]) -> bool:
        ws_id = id(websocket)
        pending = self._pending.get(ws_id, 0)
        if pending >= _WS_MAX_PENDING_SENDS:
            log.warning("WS backpressure drop fixture=%s pending=%s", fixture_key, pending)
            self._backpressure_drops += 1
            await self.disconnect(fixture_key, websocket)
            try:
                await websocket.close(code=1013)
            except Exception:
                pass
            return False
        self._pending[ws_id] = pending + 1
        try:
            await asyncio.wait_for(websocket.send_json(message), timeout=_WS_SEND_TIMEOUT_SEC)
            self._sends_ok += 1
            return True
        except Exception:
            self._sends_fail += 1
            return False
        finally:
            self._pending[ws_id] = max(0, self._pending.get(ws_id, 1) - 1)
            if self._pending.get(ws_id, 0) == 0:
                self._pending.pop(ws_id, None)

    async def connect(self, fixture_key: str, websocket: WebSocket) -> None:
        self.ensure_started()
        await websocket.accept()
        async with self._lock:
            self._rooms[fixture_key].add(websocket)
        self._connect_count += 1

        cache = get_cache()
        bundle = build_fixture_bundle(cache, fixture_key)
        lines = bundle.get("lines")
        if isinstance(lines, dict) and lines.get("shopped"):
            self._lines_state[fixture_key] = dict(lines)
        if bundle.get("ready", {}).get("lines") or bundle.get("ready", {}).get("sports"):
            await self._send_json(fixture_key, websocket, {"type": "snapshot", **bundle})
        else:
            await self._send_json(
                fixture_key,
                websocket,
                {
                    "type": "waiting",
                    "fixture_key": fixture_key,
                    "message": "No cached data yet — start worker.py ingest for this fixture.",
                    "ready": bundle.get("ready", {}),
                },
            )

    async def disconnect(self, fixture_key: str, websocket: WebSocket) -> None:
        self._pending.pop(id(websocket), None)
        async with self._lock:
            had = websocket in self._rooms.get(fixture_key, set())
            self._rooms[fixture_key].discard(websocket)
            if not self._rooms[fixture_key]:
                del self._rooms[fixture_key]
                self._lines_state.pop(fixture_key, None)
        if had:
            self._disconnect_count += 1

    async def broadcast(self, fixture_key: str, message: Dict[str, Any]) -> None:
        client_message = self._prepare_client_message(fixture_key, message)
        self._broadcasts += 1
        async with self._lock:
            clients = list(self._rooms.get(fixture_key, set()))
        dead: list[WebSocket] = []
        for ws in clients:
            ok = await self._send_json(fixture_key, ws, client_message)
            if not ok:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(fixture_key, ws)

    async def run_session(self, fixture_key: str, websocket: WebSocket) -> None:
        await self.connect(fixture_key, websocket)
        try:
            while True:
                msg = await websocket.receive_text()
                cmd = msg.strip().lower()
                if cmd == "ping":
                    await self._send_json(fixture_key, websocket, {"type": "pong"})
                elif cmd in ("snapshot", "bundle"):
                    cache = get_cache()
                    bundle = build_fixture_bundle(cache, fixture_key)
                    lines = bundle.get("lines")
                    if isinstance(lines, dict):
                        self._lines_state[fixture_key] = dict(lines)
                    await self._send_json(fixture_key, websocket, {"type": "snapshot", **bundle})
                elif cmd == "refresh_sports":
                    cache = get_cache()
                    sports = cache.get_sports(fixture_key) or {}
                    refresh_sports_context(
                        fixture_key,
                        {"fixture_id": sports.get("fixture_id")},
                        cache=cache,
                        force=True,
                    )
                    bundle = build_fixture_bundle(get_cache(), fixture_key)
                    await self._send_json(fixture_key, websocket, {"type": "sports_update", **bundle})
        except WebSocketDisconnect:
            pass
        finally:
            await self.disconnect(fixture_key, websocket)

    def status(self) -> Dict[str, Any]:
        now = time.time()
        window_start = now - 60.0
        while self._bus_ts and self._bus_ts[0] < window_start:
            self._bus_ts.popleft()
        active = sum(len(room) for room in self._rooms.values())
        uptime = max(now - self._started_at, 0.001)
        return {
            "active_clients": active,
            "rooms": len(self._rooms),
            "pending_sends": sum(self._pending.values()),
            "connect_count": self._connect_count,
            "disconnect_count": self._disconnect_count,
            "backpressure_drops": self._backpressure_drops,
            "sends_ok": self._sends_ok,
            "sends_fail": self._sends_fail,
            "bus_messages_total": self._bus_messages,
            "bus_messages_per_sec_60s": round(len(self._bus_ts) / 60.0, 3),
            "broadcasts_total": self._broadcasts,
            "client_delta_mode": _client_delta_mode(),
            "uptime_sec": round(uptime, 1),
        }


_hub: WsLineHub | None = None


def get_ws_hub() -> WsLineHub:
    global _hub
    if _hub is None:
        _hub = WsLineHub()
    return _hub
