"""Validate racing JSONL lines — variable field sizes, multi-placer place races."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def validate_race_record(raw: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate one race dict before append/ingest. Never clips runner arrays."""
    errors: List[str] = []
    rid = raw.get("race_id")
    if not rid:
        errors.append("missing race_id")
    target = str(raw.get("target", "place")).lower()
    if target not in ("win", "place"):
        errors.append(f"invalid target={target!r}")
    runners = raw.get("runners")
    if not isinstance(runners, list):
        errors.append("runners must be a list")
        return False, errors
    n = len(runners)
    if n < 2:
        errors.append(f"runner_count={n} < 2 (variable fields allowed, but need ≥2)")
    seen_ids = set()
    for i, r in enumerate(runners):
        if not isinstance(r, dict):
            errors.append(f"runner[{i}] not an object")
            continue
        rk = r.get("runner_id") or r.get("id")
        if not rk:
            errors.append(f"runner[{i}] missing runner_id")
        elif rk in seen_ids:
            errors.append(f"duplicate runner_id={rk}")
        else:
            seen_ids.add(rk)
        if "model_prob" not in r and "score" not in r and "place_prob" not in r:
            errors.append(f"runner[{i}] missing model_prob/score")
    if target == "win":
        winners = sum(1 for r in runners if isinstance(r, dict) and r.get("won"))
        if winners != 1 and any(isinstance(r, dict) and "won" in r for r in runners):
            errors.append(f"win target expects exactly 1 won=true, got {winners}")
    if target == "place":
        pp = int(raw.get("place_positions", 3))
        placed = sum(1 for r in runners if isinstance(r, dict) and r.get("placed"))
        if placed == 0:
            # allow position-derived only at sqlite layer
            with_pos = sum(
                1
                for r in runners
                if isinstance(r, dict)
                and r.get("finish_position") is not None
                and int(r.get("finish_position", 0)) <= pp
            )
            if with_pos == 0:
                errors.append("place target: no placed=true and no finish_position within place_positions")
        elif placed > pp:
            errors.append(f"warn: placed_count={placed} > place_positions={pp} (dead-heat / extended places OK)")
    return len([e for e in errors if not e.startswith("warn:")]) == 0, errors


def validate_jsonl_line(line: str) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    import json

    line = line.strip()
    if not line or line.startswith("#"):
        return True, [], None
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        return False, [f"json: {exc}"], None
    if not isinstance(raw, dict):
        return False, ["root must be object"], None
    ok, errs = validate_race_record(raw)
    return ok, errs, raw


def record_runner_count_stats(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Field-size distribution — detects clipping (sudden max cap)."""
    counts: List[int] = []
    for rec in records:
        runners = rec.get("runners") or []
        if isinstance(runners, list):
            counts.append(len(runners))
    if not counts:
        return {"n_races": 0}
    return {
        "n_races": len(counts),
        "min_runners": min(counts),
        "max_runners": max(counts),
        "avg_runners": round(sum(counts) / len(counts), 2),
        "distribution": _histogram(counts),
    }


def _histogram(counts: List[int]) -> Dict[str, int]:
    hist: Dict[str, int] = {}
    for c in counts:
        key = str(c)
        hist[key] = hist.get(key, 0) + 1
    return dict(sorted(hist.items(), key=lambda x: int(x[0])))
