"""Fast JSON/msgpack codec for worker ↔ API ↔ Redis hot paths."""

from __future__ import annotations

import json
import os
from typing import Any

_CODEC = (os.environ.get("FVE_BUS_CODEC") or "json").strip().lower()
_USE_ORJSON = _CODEC in ("json", "orjson", "") or _CODEC == "auto"
_USE_MSGPACK = _CODEC == "msgpack"


def codec_name() -> str:
    if _USE_MSGPACK:
        return "msgpack"
    try:
        import orjson  # noqa: F401

        return "orjson"
    except ImportError:
        return "json"


def dumps(obj: Any) -> bytes:
    if _USE_MSGPACK:
        import msgpack

        return msgpack.packb(obj, use_bin_type=True, default=str)
    try:
        import orjson

        return orjson.dumps(obj, default=str)
    except ImportError:
        return json.dumps(obj, default=str).encode("utf-8")


def loads(raw: bytes | str | None) -> Any:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if _USE_MSGPACK:
        import msgpack

        return msgpack.unpackb(raw, raw=False)
    try:
        import orjson

        return orjson.loads(raw)
    except ImportError:
        return json.loads(raw.decode("utf-8"))
