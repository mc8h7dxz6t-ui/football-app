"""WebSocket hub — push line snapshots/updates; clients never poll book APIs."""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from typing import Any, DefaultDict, Dict, Set

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from pipeline.cache import get_cache
from pipeline.ingest import build_fixture_bundle, refresh_sports_context
from pipeline.line_bus import get_line_bus

log = logging.getLogger(__name__)

_WS_SEND_TIMEOUT_SEC = float(os.environ.get("WS_SEND_TIMEOUT_SEC", "2.0"))
_WS_MAX_PENDING_SENDS = int(os.environ.get("WS_MAX_PENDING_SENDS", "8"))


class WsLineHub:
    def __init__(self) -> None:
        self._rooms: DefaultDict[str, Set[WebSocket]] = defaultdict(set)
        self._pending: Dict[int, int] = {}
        self._lock = asyncio.Lock()
        self._started = False

    def ensure_started(self) -> None:
        if self._started:
            return
        bus = get_line_bus()
        bus.start_listener(self._on_bus_message)
        self._started = True

    def _on_bus_message(self, fixture_key: str, message: Dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.broadcast(fixture_key, message))

    async def _send_json(self, fixture_key: str, websocket: WebSocket, message: Dict[str, Any]) -> bool:
        ws_id = id(websocket)
        pending = self._pending.get(ws_id, 0)
        if pending >= _WS_MAX_PENDING_SENDS:
            log.warning("WS backpressure drop fixture=%s pending=%s", fixture_key, pending)
            await self.disconnect(fixture_key, websocket)
            try:
                await websocket.close(code=1013)
            except Exception:
                pass
            return False
        self._pending[ws_id] = pending + 1
        try:
            await asyncio.wait_for(websocket.send_json(message), timeout=_WS_SEND_TIMEOUT_SEC)
            return True
        except Exception:
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

        cache = get_cache()
        bundle = build_fixture_bundle(cache, fixture_key)
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
            self._rooms[fixture_key].discard(websocket)
            if not self._rooms[fixture_key]:
                del self._rooms[fixture_key]

    async def broadcast(self, fixture_key: str, message: Dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._rooms.get(fixture_key, set()))
        dead: list[WebSocket] = []
        for ws in clients:
            ok = await self._send_json(fixture_key, ws, message)
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


_hub: WsLineHub | None = None


def get_ws_hub() -> WsLineHub:
    global _hub
    if _hub is None:
        _hub = WsLineHub()
    return _hub
