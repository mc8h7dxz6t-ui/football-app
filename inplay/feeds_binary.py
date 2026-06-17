"""Binary Opta / Sportradar feed peel with evidence telemetry (I1)."""

from __future__ import annotations

import struct
import time
from typing import Any, Dict, Optional, Tuple

from inplay.evidence_store import record_feed_frame

OPTA_HEADER = struct.Struct(">HQI")  # u16 type, u64 seq, u32 fixture_id
SR_HEADER = struct.Struct(">BHQI")  # u8 ver, u16 type, u64 ts_ms, u32 match_id


def _peel_opta(body: bytes) -> Tuple[Dict[str, Any], bytes]:
    if len(body) < OPTA_HEADER.size:
        raise ValueError("opta frame too short")
    frame_type, seq, fixture_id = OPTA_HEADER.unpack_from(body)
    return {"vendor": "opta", "frame_type": frame_type, "seq": seq, "fixture_id": fixture_id}, body[
        OPTA_HEADER.size :
    ]


def _peel_sportradar(body: bytes) -> Tuple[Dict[str, Any], bytes]:
    if len(body) < SR_HEADER.size:
        raise ValueError("sportradar frame too short")
    ver, frame_type, ts_ms, match_id = SR_HEADER.unpack_from(body)
    return {
        "vendor": "sportradar",
        "version": ver,
        "frame_type": frame_type,
        "ts_ms": ts_ms,
        "fixture_id": match_id,
        "seq": int(ts_ms),
    }, body[SR_HEADER.size :]


def peel_binary_frame(vendor: str, payload: bytes) -> Tuple[Dict[str, Any], bytes]:
    """Peel vendor envelope — Rust kernel optional via inplay.kernel_bridge."""
    t0 = time.perf_counter()
    try:
        from inplay.kernel_bridge import peel_frame_rust

        meta, rest = peel_frame_rust(vendor, payload)
    except Exception:
        v = vendor.strip().lower()
        if v == "opta":
            meta, rest = _peel_opta(payload)
        elif v in ("sportradar", "sr"):
            meta, rest = _peel_sportradar(payload)
        else:
            raise ValueError(f"unknown vendor {vendor}")
    peel_ms = (time.perf_counter() - t0) * 1000.0
    meta["peel_ms"] = round(peel_ms, 3)
    return meta, rest


def ingest_binary_payload(vendor: str, payload: bytes) -> Dict[str, Any]:
    """Parse binary frame, record I1 telemetry, return metadata for event stack."""
    meta, body = peel_binary_frame(vendor, payload)
    fixture_id = int(meta.get("fixture_id") or 0)
    seq = int(meta.get("seq") or 0)
    record_feed_frame(
        vendor=str(meta.get("vendor") or vendor),
        fixture_id=fixture_id,
        seq=seq,
        frame_type=meta.get("frame_type"),
        peel_ms=meta.get("peel_ms"),
    )
    return {"meta": meta, "body_len": len(body)}
