"""Line update bus — Redis pub/sub + in-process callbacks for WebSocket fan-out."""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_CHANNEL_PREFIX = "fve:bus:lines:"


def channel_for(fixture_key: str) -> str:
    return f"{_CHANNEL_PREFIX}{fixture_key}"


class LineBus:
    def __init__(self, redis_url: str = _REDIS_URL) -> None:
        self._redis = None
        self._redis_ok = False
        self._local_handlers: List[Callable[[str, Dict[str, Any]], None]] = []
        self._listener_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        try:
            import redis

            self._redis = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=1)
            self._redis.ping()
            self._redis_ok = True
        except Exception:
            self._redis = None

    @property
    def backend(self) -> str:
        return "redis" if self._redis_ok else "local"

    def publish(self, fixture_key: str, message: Dict[str, Any]) -> None:
        payload = json.dumps(message, default=str)
        if self._redis_ok and self._redis:
            self._redis.publish(channel_for(fixture_key), payload)
            return
        self._dispatch(fixture_key, message)

    def subscribe_local(self, handler: Callable[[str, Dict[str, Any]], None]) -> None:
        self._local_handlers.append(handler)

    def _dispatch(self, fixture_key: str, message: Dict[str, Any]) -> None:
        for handler in list(self._local_handlers):
            try:
                handler(fixture_key, message)
            except Exception:
                log.exception("line_bus local handler failed")

    def start_listener(self, handler: Callable[[str, Dict[str, Any]], None]) -> None:
        """Background thread: forward Redis pub/sub messages to handler."""
        if not self._redis_ok or not self._redis:
            self.subscribe_local(handler)
            return
        if self._listener_thread and self._listener_thread.is_alive():
            self.subscribe_local(handler)
            return

        self.subscribe_local(handler)

        def _run() -> None:
            pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
            pubsub.psubscribe(f"{_CHANNEL_PREFIX}*")
            log.info("line_bus redis listener started")
            while not self._stop.is_set():
                msg = pubsub.get_message(timeout=1.0)
                if not msg or msg.get("type") not in ("pmessage", "message"):
                    continue
                try:
                    channel = str(msg.get("channel", ""))
                    fk = channel.replace(_CHANNEL_PREFIX, "", 1)
                    data = json.loads(msg["data"])
                    self._dispatch(fk, data)
                except Exception:
                    log.exception("line_bus listener parse error")

        self._listener_thread = threading.Thread(target=_run, name="line-bus-listener", daemon=True)
        self._listener_thread.start()

    def stop(self) -> None:
        self._stop.set()


_bus: Optional[LineBus] = None


def get_line_bus() -> LineBus:
    global _bus
    if _bus is None:
        _bus = LineBus()
    return _bus
