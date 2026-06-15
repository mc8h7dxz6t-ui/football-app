"""Build one institutional verification JSON object per settled race (JSONL line)."""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Literal, Optional, TextIO

TargetKind = Literal["win", "place"]


def _clamp_prob(p: float) -> float:
    return min(max(float(p), 0.0), 1.0)


def decimal_to_implied_prob(decimal: Optional[float]) -> Optional[float]:
    if decimal is None:
        return None
    d = float(decimal)
    if d <= 1.0:
        return None
    return 1.0 / d


def normalize_race_probs(raw: List[float]) -> List[float]:
    """Softmax when scores are logits; normalize when already non-negative."""
    if not raw:
        return []
    vals = [float(x) for x in raw]
    if all(0.0 <= v <= 1.0 for v in vals) and sum(vals) > 0:
        s = sum(vals)
        return [v / s for v in vals]
    # logits / unbounded LightGBM scores → softmax
    m = max(vals)
    exps = [math.exp(v - m) for v in vals]
    s = sum(exps) or 1.0
    return [e / s for e in exps]


def runner_record(
    *,
    runner_id: str,
    model_prob: float,
    market_prob: Optional[float] = None,
    won: bool = False,
    placed: bool = False,
) -> Dict[str, Any]:
    return {
        "runner_id": str(runner_id),
        "model_prob": round(_clamp_prob(model_prob), 6),
        "market_prob": round(_clamp_prob(market_prob), 6) if market_prob is not None else None,
        "won": bool(won),
        "placed": bool(placed),
    }


def build_settled_race_record(
    *,
    race_id: str,
    target: TargetKind,
    runners: List[Dict[str, Any]],
    venue_id: str = "",
    venue_mapped: bool = True,
    place_positions: int = 3,
) -> Dict[str, Any]:
    """Canonical JSONL record for ``verify_racing_window.py`` / ``metrics.racing``."""
    if target not in ("win", "place"):
        raise ValueError(f"target must be win or place, got {target!r}")
    if not runners:
        raise ValueError("runners required")
    return {
        "race_id": str(race_id),
        "target": target,
        "place_positions": int(place_positions) if target == "place" else None,
        "venue_id": str(venue_id or ""),
        "venue_mapped": bool(venue_mapped),
        "runners": runners,
    }


def to_jsonl_line(record: Dict[str, Any]) -> str:
    return json.dumps(record, separators=(",", ":"), sort_keys=True)


def append_jsonl(path: str, record: Dict[str, Any], *, dedupe_race_id: bool = False) -> None:
    """Append one line; optional skip if race_id already present in file."""
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if dedupe_race_id and p.is_file():
        rid = record.get("race_id")
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                if json.loads(line).get("race_id") == rid:
                    return
            except json.JSONDecodeError:
                continue
    with p.open("a", encoding="utf-8") as fh:
        write_jsonl_line(fh, record)


def write_jsonl_line(fh: TextIO, record: Dict[str, Any]) -> None:
    fh.write(to_jsonl_line(record) + "\n")
