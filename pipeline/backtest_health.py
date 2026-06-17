"""Cached backtest calibration slice for FVE /health (no hot-path I/O)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_CACHE: Dict[str, Any] = {"t": 0.0, "payload": None}
_TTL_SEC = float(os.getenv("FVE_BACKTEST_HEALTH_TTL_SEC", "300"))


def backtest_cache_path() -> Path:
    explicit = (os.getenv("FVE_BACKTEST_CACHE") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return Path(__file__).resolve().parents[1] / "data" / "backtest_cache.json"


def load_settled_records(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        rows = raw.get("records") or raw.get("rows")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


def backtest_health_slice(*, force: bool = False) -> Dict[str, Any]:
    """Headline calibration from optional cached settled predictions."""
    now = time.monotonic()
    if (
        not force
        and _CACHE["payload"] is not None
        and (now - float(_CACHE["t"])) < _TTL_SEC
    ):
        return dict(_CACHE["payload"])

    path = backtest_cache_path()
    records = load_settled_records(path)
    if not records:
        payload = {
            "available": False,
            "cache_path": str(path),
            "n": 0,
            "message": "No backtest cache — export settled rows to FVE_BACKTEST_CACHE",
        }
        _CACHE["t"] = now
        _CACHE["payload"] = payload
        return dict(payload)

    try:
        from backtest import evaluate, evaluate_vs_market

        headline = evaluate(records)
        vs_market = evaluate_vs_market(records)
        payload = {
            "available": True,
            "cache_path": str(path),
            "n": headline.get("n"),
            "brier_score": headline.get("brier_score"),
            "log_loss": headline.get("log_loss"),
            "top_pick_accuracy_pct": headline.get("top_pick_accuracy_pct"),
            "uniform_baseline_brier": headline.get("uniform_baseline_brier"),
            "calibration_bins": (headline.get("calibration") or [])[:6],
            "vs_market": {
                "n_paired": vs_market.get("n_paired"),
                "brier_delta_vs_market": vs_market.get("brier_delta_vs_market"),
                "verdict": vs_market.get("verdict"),
            },
        }
    except Exception as exc:
        payload = {
            "available": False,
            "cache_path": str(path),
            "n": len(records),
            "error": str(exc)[:120],
        }

    _CACHE["t"] = now
    _CACHE["payload"] = payload
    return dict(payload)
