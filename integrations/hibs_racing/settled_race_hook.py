"""
hibs-racing integration — call after LightGBM scoring when results are known.

Copy this file into hibs-racing (e.g. ``src/hibs_racing/verification_emit.py``)
and invoke from ``daily_refresh.sh`` / post-``fetch-cards --score`` settlement:

    from hibs_racing.verification_emit import emit_race_from_scored_runners

    emit_race_from_scored_runners(
        race_id=race["id"],
        target="place",
        place_positions=3,
        venue_id=race.get("course_id", ""),
        venue_mapped=bool(race.get("matchbook_mapped", True)),
        runners=[
            {
                "runner_id": r["runner_id"],
                "model_prob": r["place_prob"],  # LightGBM head output
                "market_prob": 1.0 / r["place_decimal"] if r.get("place_decimal") else None,
                "position": r.get("finish_position"),
            },
            ...
        ],
        out_path=Path("data/verification/settled_races.jsonl"),
    )

Or shell-out (no Python dep on football-app):

    python /path/to/football-app/scripts/emit_racing_verification_jsonl.py \\
      --feature-store data/feature_store.sqlite
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

# Vendored from football-app metrics.racing_emit — keep in sync or import if on PYTHONPATH.
try:
    from metrics.racing_emit import append_jsonl, build_settled_race_record, runner_record
except ImportError:
    import json

    def runner_record(**kwargs):
        return kwargs

    def build_settled_race_record(**kwargs):
        rec = dict(kwargs)
        rec["place_positions"] = kwargs.get("place_positions") if kwargs.get("target") == "place" else None
        return rec

    def append_jsonl(path, record, *, dedupe_race_id=False):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")


PathLike = Union[str, Path]


def emit_race_from_scored_runners(
    *,
    race_id: str,
    runners: Iterable[Dict[str, Any]],
    target: str = "place",
    place_positions: int = 3,
    venue_id: str = "",
    venue_mapped: bool = True,
    out_path: PathLike = "data/verification/settled_races.jsonl",
    dedupe: bool = True,
) -> Dict[str, Any]:
    """Emit one JSONL line from in-memory LightGBM scored runners (hibs-racing hook)."""
    if target not in ("win", "place"):
        raise ValueError("target must be win or place")

    built: List[Dict[str, Any]] = []
    for r in runners:
        pos = r.get("position") or r.get("finish_position") or r.get("pos")
        try:
            position = int(float(pos)) if pos is not None else None
        except (TypeError, ValueError):
            position = None
        won = position == 1 if position else bool(r.get("won", False))
        placed = (
            (position is not None and position <= place_positions)
            if target == "place"
            else won
        )
        if position is None:
            placed = bool(r.get("placed", placed))
            won = bool(r.get("won", won))

        mp = r.get("model_prob")
        if mp is None:
            mp = r.get("place_prob") or r.get("p_place") or r.get("score")
        if mp is None:
            raise ValueError(f"runner {r.get('runner_id')} missing model_prob / score")

        mkt = r.get("market_prob")
        if mkt is None and r.get("place_decimal"):
            d = float(r["place_decimal"])
            mkt = 1.0 / d if d > 1.0 else None
        if mkt is None and r.get("win_decimal"):
            d = float(r["win_decimal"])
            mkt = 1.0 / d if d > 1.0 else None

        built.append(
            runner_record(
                runner_id=str(r.get("runner_id") or r.get("id")),
                model_prob=float(mp),
                market_prob=float(mkt) if mkt is not None else None,
                won=won,
                placed=placed,
            )
        )

    record = build_settled_race_record(
        race_id=race_id,
        target=target,  # type: ignore[arg-type]
        runners=built,
        venue_id=venue_id,
        venue_mapped=venue_mapped,
        place_positions=place_positions,
    )
    append_jsonl(str(out_path), record, dedupe_race_id=dedupe)
    return record
