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
    """Keep the most recent ``max_races`` lines (by file order)."""
    if max_races <= 0 or not path.is_file():
        return {"before": 0, "after": 0, "trimmed": 0}
    records = [r for r in load_jsonl(path) if r.get("race_id") and "_corrupt_line" not in r]
    before = len(records)
    if before <= max_races:
        return {"before": before, "after": before, "trimmed": 0}
    kept = records[-max_races:]
    atomic_write_jsonl(path, kept)
    return {"before": before, "after": len(kept), "trimmed": before - len(kept)}


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
