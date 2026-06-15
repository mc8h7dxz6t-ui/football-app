"""JSONL store: batch dedupe, rolling trim, atomic rewrite."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    records: List[Dict[str, Any]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"_corrupt_line": i, "raw": line[:200]})
    return records


def load_race_ids(path: Path) -> Set[str]:
    return {str(r["race_id"]) for r in load_jsonl(path) if r.get("race_id")}


def append_records(
    path: Path,
    records: Iterable[Dict[str, Any]],
    *,
    existing_ids: Set[str] | None = None,
) -> Dict[str, int]:
    """Append new race_ids only; returns counts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ids = existing_ids if existing_ids is not None else load_race_ids(path)
    appended = 0
    skipped = 0
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            rid = str(rec.get("race_id", ""))
            if not rid:
                skipped += 1
                continue
            if rid in ids:
                skipped += 1
                continue
            fh.write(json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n")
            ids.add(rid)
            appended += 1
    return {"appended": appended, "skipped_duplicate": skipped, "total_ids": len(ids)}


def trim_jsonl(path: Path, *, max_races: int) -> Dict[str, int]:
    """Keep the most recent ``max_races`` lines (by race_date when known, else file order)."""
    if max_races <= 0 or not path.is_file():
        return {"before": 0, "after": 0, "trimmed": 0, "max_races": max_races}
    records = [r for r in load_jsonl(path) if r.get("race_id") and "_corrupt_line" not in r]
    before = len(records)
    if before <= max_races:
        span = window_span_from_records(records)
        return {"before": before, "after": before, "trimmed": 0, "max_races": max_races, **span}
    dated = [r for r in records if r.get("race_date") or r.get("card_date")]
    if len(dated) == len(records):
        records.sort(key=lambda r: str(r.get("race_date") or r.get("card_date"))[:10])
    kept = records[-max_races:]
    span = window_span_from_records(kept)
    atomic_write_jsonl(path, kept)
    return {
        "before": before,
        "after": len(kept),
        "trimmed": before - len(kept),
        "max_races": max_races,
        **span,
    }


_DATE_KEYS = ("race_date", "meeting_date", "settled_at", "off_time", "race_time")


def window_span_from_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calendar span for trim window (when JSONL rows carry date fields)."""
    dates: List[str] = []
    for rec in records:
        for key in _DATE_KEYS:
            val = rec.get(key)
            if val:
                dates.append(str(val)[:10])
                break
    if not dates:
        return {
            "calendar_span_known": False,
            "oldest_race_date": None,
            "newest_race_date": None,
            "calendar_days_span": None,
        }
    dates_sorted = sorted(dates)
    oldest, newest = dates_sorted[0], dates_sorted[-1]
    days: int | None = None
    try:
        from datetime import date

        d0 = date.fromisoformat(oldest)
        d1 = date.fromisoformat(newest)
        days = (d1 - d0).days + 1
    except ValueError:
        pass
    return {
        "calendar_span_known": True,
        "oldest_race_date": oldest,
        "newest_race_date": newest,
        "calendar_days_span": days,
    }


def atomic_write_jsonl(path: Path, records: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".jsonl.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for rec in records:
                if rec.get("race_id"):
                    fh.write(json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
