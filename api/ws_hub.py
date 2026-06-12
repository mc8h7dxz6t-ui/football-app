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
from pipeline.ingest import build_fixture_bundle, build_line_view, refresh_sports_context
from pipeline.line_bus import get_line_bus

log = logging.getLogger(__name__)

_WS_SEND_TIMEOUT_SEC = float(os.environ.get("WS_SEND_TIMEOUT_SEC", "2.0"))


class WsLineHub:
    def __init__(self) -> None:
        self._rooms: DefaultDict[str, Set[WebSocket]] = defaultdict(set)
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

    async def connect(self, fixture_key: str, websocket: WebSocket) -> None:
        self.ensure_started()
        await websocket.accept()
        async with self._lock:
            self._rooms[fixture_key].add(websocket)

        cache = get_cache()
        bundle = build_fixture_bundle(cache, fixture_key)
        if bundle.get("ready", {}).get("lines") or bundle.get("ready", {}).get("sports"):
            await websocket.send_json({"type": "snapshot", **bundle})
        else:
            await websocket.send_json(
                {
                    "type": "waiting",
                    "fixture_key": fixture_key,
                    "message": "No cached data yet — start worker.py ingest for this fixture.",
                    "ready": bundle.get("ready", {}),
                }
            )

    async def disconnect(self, fixture_key: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._rooms[fixture_key].discard(websocket)
            if not self._rooms[fixture_key]:
                del self._rooms[fixture_key]

    async def broadcast(self, fixture_key: str, message: Dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._rooms.get(fixture_key, set()))
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=_WS_SEND_TIMEOUT_SEC)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(fixture_key, ws)

    async def run_session(self, fixture_key: str, websocket: WebSocket) -> None:
        await self.connect(fixture_key, websocket)
        try:
            while True:
                # Optional client ping / subscribe extensions
                msg = await websocket.receive_text()
                if msg.strip().lower() == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg.strip().lower() in ("snapshot", "bundle"):
                    cache = get_cache()
                    bundle = build_fixture_bundle(cache, fixture_key)
                    await websocket.send_json({"type": "snapshot", **bundle})
                elif msg.strip().lower() == "refresh_sports":
                    cache = get_cache()
                    sports = cache.get_sports(fixture_key) or {}
                    refresh_sports_context(
                        fixture_key,
                        {"fixture_id": sports.get("fixture_id")},
                        cache=cache,
                        force=True,
                    )
                    bundle = build_fixture_bundle(get_cache(), fixture_key)
                    await websocket.send_json({"type": "sports_update", **bundle})
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
