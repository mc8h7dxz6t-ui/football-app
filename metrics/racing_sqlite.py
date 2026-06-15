"""Read settled races from hibs-racing feature_store.sqlite → verification JSONL."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from metrics.racing_emit import (
    build_settled_race_record,
    decimal_to_implied_prob,
    normalize_race_probs,
    runner_record,
)

# Column aliases (first match wins per role).
RACE_ID_COLS = ("race_id", "race_key", "event_id")
RUNNER_ID_COLS = ("runner_id", "id", "runner_key")
VENUE_COLS = ("course_id", "venue_id", "course", "meeting_id")
VENUE_MAPPED_COLS = ("venue_mapped", "matchbook_mapped", "course_mapped")
POSITION_COLS = ("finish_position", "position", "pos", "plc")
MODEL_PROB_COLS = ("place_prob", "p_place", "prob_place", "model_place_prob", "score", "prob")
MARKET_PLACE_COLS = ("place_decimal", "place_odds", "market_place_decimal")
WIN_DECIMAL_COLS = ("win_decimal", "sp_decimal", "starting_price_decimal", "odds_decimal")
SETTLED_FLAG_COLS = ("settled", "is_settled", "result_known")
PLACE_POSITIONS_COLS = ("place_positions", "places_paid", "each_way_places")

PREFERRED_TABLES = (
    "settled_runners",
    "race_results",
    "runners",
    "upcoming_runners",
    "ranker_features",
)


def _columns(con: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info([{table}])")]


def _pick(cols: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    return None


def _tables(con: sqlite3.Connection) -> List[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1"
    ).fetchall()
    return [r[0] for r in rows]


def _choose_source_table(con: sqlite3.Connection, *, table: Optional[str] = None) -> str:
    names = _tables(con)
    if table:
        if table not in names:
            raise ValueError(f"table {table!r} not in database")
        return table
    for pref in PREFERRED_TABLES:
        if pref in names:
            return pref
    for name in names:
        cols = _columns(con, name)
        if _pick(cols, RACE_ID_COLS) and _pick(cols, RUNNER_ID_COLS):
            return name
    raise ValueError("no table with race_id + runner_id found")


def _row_get(row: sqlite3.Row, col: Optional[str]) -> Any:
    if col is None:
        return None
    return row[col]


def _is_settled_row(row: sqlite3.Row, settled_col: Optional[str], position_col: Optional[str]) -> bool:
    if settled_col:
        val = _row_get(row, settled_col)
        if val is not None:
            return str(val).strip().lower() in ("1", "true", "yes", "on")
    if position_col:
        pos = _row_get(row, position_col)
        if pos is not None and str(pos).strip() != "":
            try:
                return int(float(pos)) > 0
            except (TypeError, ValueError):
                return True
    return False


def _outcome_flags(
    position: Optional[int],
    *,
    target: str,
    place_positions: int,
) -> Tuple[bool, bool]:
    if position is None or position <= 0:
        return False, False
    won = position == 1
    placed = position <= place_positions if target == "place" else won
    return won, placed


def extract_settled_races_from_db(
    db_path: str | Path,
    *,
    target: str = "place",
    place_positions: int = 3,
    table: Optional[str] = None,
    only_settled: bool = True,
) -> List[Dict[str, Any]]:
    """Group sqlite rows into settled race records for JSONL export."""
    if target not in ("win", "place"):
        raise ValueError("target must be win or place")

    con = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        src = _choose_source_table(con, table=table)
        cols = _columns(con, src)
        race_col = _pick(cols, RACE_ID_COLS)
        runner_col = _pick(cols, RUNNER_ID_COLS)
        if not race_col or not runner_col:
            raise ValueError(f"{src} missing race/runner id columns")

        venue_col = _pick(cols, VENUE_COLS)
        mapped_col = _pick(cols, VENUE_MAPPED_COLS)
        pos_col = _pick(cols, POSITION_COLS)
        model_col = _pick(cols, MODEL_PROB_COLS)
        mkt_place_col = _pick(cols, MARKET_PLACE_COLS)
        win_col = _pick(cols, WIN_DECIMAL_COLS)
        settled_col = _pick(cols, SETTLED_FLAG_COLS)
        pp_col = _pick(cols, PLACE_POSITIONS_COLS)

        rows = con.execute(f"SELECT * FROM [{src}]").fetchall()
        by_race: Dict[str, List[sqlite3.Row]] = {}
        race_meta: Dict[str, Dict[str, Any]] = {}

        for row in rows:
            if only_settled and not _is_settled_row(row, settled_col, pos_col):
                continue
            rid = str(_row_get(row, race_col))
            by_race.setdefault(rid, []).append(row)
            meta = race_meta.setdefault(rid, {})
            if venue_col and not meta.get("venue_id"):
                meta["venue_id"] = str(_row_get(row, venue_col) or "")
            if mapped_col and "venue_mapped" not in meta:
                mv = _row_get(row, mapped_col)
                if mv is not None:
                    meta["venue_mapped"] = str(mv).strip().lower() in ("1", "true", "yes", "on")
            if pp_col and "place_positions" not in meta:
                try:
                    meta["place_positions"] = int(_row_get(row, pp_col))
                except (TypeError, ValueError):
                    pass

        records: List[Dict[str, Any]] = []
        for rid, race_rows in by_race.items():
            if len(race_rows) < 2:
                continue
            meta = race_meta.get(rid, {})
            pp = int(meta.get("place_positions") or place_positions)
            venue_mapped = bool(meta.get("venue_mapped", True))

            raw_model: List[float] = []
            for row in race_rows:
                v = _row_get(row, model_col) if model_col else None
                try:
                    raw_model.append(float(v) if v is not None else 0.0)
                except (TypeError, ValueError):
                    raw_model.append(0.0)
            model_probs = normalize_race_probs(raw_model) if any(raw_model) else [0.0] * len(race_rows)

            runners_out: List[Dict[str, Any]] = []
            for row, mp in zip(race_rows, model_probs):
                pos_raw = _row_get(row, pos_col)
                position: Optional[int] = None
                if pos_raw is not None and str(pos_raw).strip() != "":
                    try:
                        position = int(float(pos_raw))
                    except (TypeError, ValueError):
                        position = None
                won, placed = _outcome_flags(position, target=target, place_positions=pp)

                market_prob = None
                if mkt_place_col:
                    market_prob = decimal_to_implied_prob(_row_get(row, mkt_place_col))
                if market_prob is None and win_col:
                    market_prob = decimal_to_implied_prob(_row_get(row, win_col))

                runners_out.append(
                    runner_record(
                        runner_id=str(_row_get(row, runner_col)),
                        model_prob=mp,
                        market_prob=market_prob,
                        won=won,
                        placed=placed,
                    )
                )

            if target == "win" and not any(r["won"] for r in runners_out):
                continue
            if target == "place" and not any(r["placed"] for r in runners_out):
                continue

            records.append(
                build_settled_race_record(
                    race_id=rid,
                    target=target,  # type: ignore[arg-type]
                    runners=runners_out,
                    venue_id=str(meta.get("venue_id") or ""),
                    venue_mapped=venue_mapped,
                    place_positions=pp,
                )
            )
        return records
    finally:
        con.close()


def emit_jsonl_from_db(
    db_path: str | Path,
    out_path: str | Path,
    *,
    target: str = "place",
    place_positions: int = 3,
    table: Optional[str] = None,
    dedupe: bool = True,
) -> Dict[str, Any]:
    from metrics.racing_emit import append_jsonl

    races = extract_settled_races_from_db(
        db_path,
        target=target,
        place_positions=place_positions,
        table=table,
    )
    out = Path(out_path)
    n_new = 0
    for rec in races:
        before = out.stat().st_size if out.is_file() else 0
        append_jsonl(str(out), rec, dedupe_race_id=dedupe)
        after = out.stat().st_size if out.is_file() else 0
        if after > before:
            n_new += 1
    return {"races_extracted": len(races), "lines_appended": n_new, "output": str(out)}
