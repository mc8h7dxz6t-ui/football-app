"""Redis client factory — TCP_NODELAY + Dragonfly-compatible URL."""

from __future__ import annotations

import os
import socket
from typing import Any

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _tcp_nodelay_enabled() -> bool:
    return os.environ.get("FVE_TCP_NODELAY", "1").strip().lower() in ("1", "true", "yes", "on")


def create_redis_client(*, url: str | None = None, decode_responses: bool = True, **extra: Any):
    """Build a redis client; optional TCP_NODELAY on the socket."""
    import redis
    from redis.connection import Connection

    class _NoDelayConnection(Connection):
        def connect(self) -> None:
            super().connect()
            if _tcp_nodelay_enabled() and self._sock is not None:
                try:
                    self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass

    kwargs: dict[str, Any] = {
        "decode_responses": decode_responses,
        "socket_connect_timeout": float(os.environ.get("REDIS_CONNECT_TIMEOUT_SEC", "1")),
        "socket_keepalive": True,
        **extra,
    }
    if _tcp_nodelay_enabled():
        kwargs["connection_class"] = _NoDelayConnection
    return redis.from_url(url or _REDIS_URL, **kwargs)


def redis_backend_label(url: str | None = None) -> str:
    """Human label for health — Dragonfly uses redis:// URL."""
    u = (url or _REDIS_URL).lower()
    if "dragonfly" in u or os.environ.get("FVE_REDIS_BACKEND", "").lower() == "dragonfly":
        return "dragonfly"
    return "redis"
