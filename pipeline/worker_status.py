"""Worker liveness — heartbeat file read by /health and preflight."""

from __future__ import annotations

import os
import time
from pathlib import Path

_HEARTBEAT_PATH = os.environ.get("FVE_WORKER_HEARTBEAT", "/tmp/fve_worker_heartbeat")
_STALE_SEC = float(os.environ.get("FVE_WORKER_STALE_SEC", "120"))


def heartbeat_path() -> Path:
    return Path(_HEARTBEAT_PATH)


def touch_worker_heartbeat() -> None:
    try:
        heartbeat_path().write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        pass


def worker_status() -> dict:
    path = heartbeat_path()
    if not path.is_file():
        return {"alive": False, "last_seen_sec_ago": None, "stale": True, "path": str(path)}
    try:
        ts = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return {"alive": False, "last_seen_sec_ago": None, "stale": True, "path": str(path)}
    age = max(0, int(time.time()) - ts)
    return {
        "alive": age < _STALE_SEC,
        "last_seen_sec_ago": age,
        "stale": age >= _STALE_SEC,
        "path": str(path),
    }
