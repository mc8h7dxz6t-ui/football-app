"""Read settled races from hibs-racing feature_store.sqlite → verification JSONL."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from metrics.racing_emit import (
    build_settled_race_record,
    runner_record,
)
from metrics.racing_measurement import (
    CONFIG_HASH_COLS,
    ODDS_SOURCE_COLS,
    RACE_DATE_COLS,
    build_export_meta,
    market_prob_for_target,
    model_probs_for_export,
    runners_fully_paired,
)

# Column aliases (first match wins per role).
RACE_ID_COLS = ("race_id", "race_key", "event_id")
RUNNER_ID_COLS = ("runner_id", "id", "runner_key")
VENUE_COLS = ("course_id", "venue_id", "course", "meeting_id")
VENUE_MAPPED_COLS = ("venue_mapped", "matchbook_mapped", "course_mapped")
POSITION_COLS = ("finish_pos", "finish_position", "position", "pos", "plc")
MODEL_PROB_COLS = (
    "model_place_prob",
    "place_prob",
    "p_place",
    "prob_place",
    "combo_bayes_place",
    "model_score",
    "score",
    "prob",
)
MARKET_PLACE_COLS = (
    "offered_place_decimal",
    "place_decimal",
    "place_odds",
    "market_place_decimal",
)
WIN_DECIMAL_COLS = ("win_decimal", "sp_decimal", "starting_price_decimal", "odds_decimal")
SETTLED_FLAG_COLS = ("settled", "is_settled", "result_known")
PLACE_POSITIONS_COLS = ("place_positions", "places_paid", "each_way_places", "places")
ROW_DEDUP_TS_COLS = ("scored_at", "fetched_at", "built_at", "enriched_at")

PREFERRED_TABLES = (
    "settled_runners",
    "race_results",
    "scored_runner_snapshots",
    "ranker_features",
    "runners",
    "upcoming_runners",
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


def _dedupe_race_rows(
    rows: List[sqlite3.Row],
    *,
    runner_col: str,
    ts_col: Optional[str],
) -> List[sqlite3.Row]:
    """Keep one row per runner; latest timestamp wins when present."""
    if not rows:
        return rows
    best: Dict[str, sqlite3.Row] = {}
    for row in rows:
        rid = str(_row_get(row, runner_col))
        if rid not in best:
            best[rid] = row
            continue
        if not ts_col:
            continue
        cur_ts = str(_row_get(row, ts_col) or "")
        prev_ts = str(_row_get(best[rid], ts_col) or "")
        if cur_ts > prev_ts:
            best[rid] = row
    return list(best.values())


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


def _projection_columns(
    *,
    race_col: str,
    runner_col: str,
    venue_col: Optional[str],
    mapped_col: Optional[str],
    pos_col: Optional[str],
    model_col: Optional[str],
    mkt_place_col: Optional[str],
    win_col: Optional[str],
    pp_col: Optional[str],
    ts_col: Optional[str],
    settled_col: Optional[str],
    race_date_col: Optional[str],
    odds_source_col: Optional[str],
    config_hash_col: Optional[str],
) -> List[str]:
    """Columns required for export — never SELECT * (snapshots carry huge JSON blobs)."""
    seen: set[str] = set()
    ordered: List[str] = []
    for col in (
        race_col,
        runner_col,
        race_date_col,
        venue_col,
        mapped_col,
        pos_col,
        model_col,
        mkt_place_col,
        win_col,
        pp_col,
        ts_col,
        odds_source_col,
        config_hash_col,
        settled_col,
    ):
        if col and col not in seen:
            seen.add(col)
            ordered.append(col)
    return ordered


def _settled_where(pos_col: Optional[str], settled_col: Optional[str], *, only_settled: bool) -> str:
    if not only_settled:
        return ""
    if pos_col:
        return f" WHERE [{pos_col}] IS NOT NULL AND CAST([{pos_col}] AS REAL) > 0"
    if settled_col:
        return f" WHERE [{settled_col}] IS NOT NULL"
    return ""


def _eligible_race_ids(
    con: sqlite3.Connection,
    src: str,
    *,
    race_col: str,
    runner_col: str,
    pos_col: Optional[str],
    settled_col: Optional[str],
    only_settled: bool,
) -> List[str]:
    where = _settled_where(pos_col, settled_col, only_settled=only_settled)
    sql = f"""
        SELECT [{race_col}] AS rid
        FROM [{src}]{where}
        GROUP BY [{race_col}]
        HAVING COUNT(DISTINCT [{runner_col}]) >= 2
    """
    return [str(r[0]) for r in con.execute(sql).fetchall()]


def _build_race_record(
    rid: str,
    race_rows: List[sqlite3.Row],
    *,
    target: str,
    place_positions: int,
    race_meta: Dict[str, Any],
    runner_col: str,
    pos_col: Optional[str],
    model_col: Optional[str],
    mkt_place_col: Optional[str],
    win_col: Optional[str],
    ts_col: Optional[str],
    source_table: str,
    require_paired_place_market: bool,
) -> Optional[Dict[str, Any]]:
    race_rows = _dedupe_race_rows(race_rows, runner_col=runner_col, ts_col=ts_col)
    if len(race_rows) < 2:
        return None

    pp = int(race_meta.get("place_positions") or place_positions)
    venue_mapped = bool(race_meta.get("venue_mapped", True))

    raw_model: List[float] = []
    for row in race_rows:
        v = _row_get(row, model_col) if model_col else None
        try:
            raw_model.append(float(v) if v is not None else 0.0)
        except (TypeError, ValueError):
            raw_model.append(0.0)
    model_probs = model_probs_for_export(target, raw_model, model_col=model_col)

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

        place_dec = _row_get(row, mkt_place_col) if mkt_place_col else None
        win_dec = _row_get(row, win_col) if win_col else None
        market_prob, mkt_src = market_prob_for_target(
            target, place_decimal=place_dec, win_decimal=win_dec
        )

        runners_out.append(
            runner_record(
                runner_id=str(_row_get(row, runner_col)),
                model_prob=mp,
                market_prob=market_prob,
                won=won,
                placed=placed,
            )
        )

    if target == "place" and require_paired_place_market and not runners_fully_paired(runners_out):
        return None

    if target == "win" and not any(r["won"] for r in runners_out):
        return None
    if target == "place" and not any(r["placed"] for r in runners_out):
        return None

    market_col_label = mkt_place_col if target == "place" else (win_col or mkt_place_col)
    if target == "place":
        market_col_label = mkt_place_col

    return build_settled_race_record(
        race_id=rid,
        target=target,  # type: ignore[arg-type]
        runners=runners_out,
        venue_id=str(race_meta.get("venue_id") or ""),
        venue_mapped=venue_mapped,
        place_positions=pp,
        race_date=race_meta.get("race_date"),
        meta=build_export_meta(
            source_table=source_table,
            target=target,
            model_col=model_col,
            market_col=market_col_label,
            scored_at=race_meta.get("scored_at"),
            odds_source=race_meta.get("odds_source"),
            config_hash=race_meta.get("config_hash"),
        ),
    )


def _race_meta_from_rows(
    race_rows: Sequence[sqlite3.Row],
    *,
    venue_col: Optional[str],
    mapped_col: Optional[str],
    pp_col: Optional[str],
    race_date_col: Optional[str],
    ts_col: Optional[str],
    odds_source_col: Optional[str],
    config_hash_col: Optional[str],
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    for row in race_rows:
        if race_date_col and not meta.get("race_date"):
            rd = _row_get(row, race_date_col)
            if rd is not None and str(rd).strip():
                meta["race_date"] = str(rd)[:10]
        if ts_col and not meta.get("scored_at"):
            ts = _row_get(row, ts_col)
            if ts is not None:
                meta["scored_at"] = str(ts)
        if odds_source_col and not meta.get("odds_source"):
            osrc = _row_get(row, odds_source_col)
            if osrc is not None:
                meta["odds_source"] = str(osrc)
        if config_hash_col and not meta.get("config_hash"):
            ch = _row_get(row, config_hash_col)
            if ch is not None:
                meta["config_hash"] = str(ch)
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
    return meta


_RACE_BATCH_SIZE = 100


def extract_settled_races_from_db(
    db_path: str | Path,
    *,
    target: str = "place",
    place_positions: int = 3,
    table: Optional[str] = None,
    only_settled: bool = True,
    require_paired_place_market: bool = True,
) -> List[Dict[str, Any]]:
    """Group sqlite rows into settled race records for JSONL export."""
    if target not in ("win", "place"):
        raise ValueError("target must be win or place")
    if target != "place":
        require_paired_place_market = False

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
        ts_col = _pick(cols, ROW_DEDUP_TS_COLS)
        race_date_col = _pick(cols, RACE_DATE_COLS)
        odds_source_col = _pick(cols, ODDS_SOURCE_COLS)
        config_hash_col = _pick(cols, CONFIG_HASH_COLS)
        proj_cols = _projection_columns(
            race_col=race_col,
            runner_col=runner_col,
            venue_col=venue_col,
            mapped_col=mapped_col,
            pos_col=pos_col,
            model_col=model_col,
            mkt_place_col=mkt_place_col,
            win_col=win_col,
            pp_col=pp_col,
            ts_col=ts_col,
            settled_col=settled_col,
            race_date_col=race_date_col,
            odds_source_col=odds_source_col,
            config_hash_col=config_hash_col,
        )
        col_sql = ", ".join(f"[{c}]" for c in proj_cols)

        race_ids = _eligible_race_ids(
            con,
            src,
            race_col=race_col,
            runner_col=runner_col,
            pos_col=pos_col,
            settled_col=settled_col,
            only_settled=only_settled,
        )

        records: List[Dict[str, Any]] = []
        pos_filter = ""
        if only_settled and pos_col:
            pos_filter = f" AND [{pos_col}] IS NOT NULL AND CAST([{pos_col}] AS REAL) > 0"
        if target == "place" and require_paired_place_market and mkt_place_col:
            pos_filter += (
                f" AND [{mkt_place_col}] IS NOT NULL AND CAST([{mkt_place_col}] AS REAL) > 1"
            )

        for batch_start in range(0, len(race_ids), _RACE_BATCH_SIZE):
            batch = race_ids[batch_start : batch_start + _RACE_BATCH_SIZE]
            if not batch:
                continue
            placeholders = ",".join("?" for _ in batch)
            sql = (
                f"SELECT {col_sql} FROM [{src}] "
                f"WHERE [{race_col}] IN ({placeholders}){pos_filter}"
            )
            by_race: Dict[str, List[sqlite3.Row]] = {}
            for row in con.execute(sql, batch):
                if only_settled and not _is_settled_row(row, settled_col, pos_col):
                    continue
                rid = str(_row_get(row, race_col))
                by_race.setdefault(rid, []).append(row)

            for rid, race_rows in by_race.items():
                meta = _race_meta_from_rows(
                    race_rows,
                    venue_col=venue_col,
                    mapped_col=mapped_col,
                    pp_col=pp_col,
                    race_date_col=race_date_col,
                    ts_col=ts_col,
                    odds_source_col=odds_source_col,
                    config_hash_col=config_hash_col,
                )
                rec = _build_race_record(
                    rid,
                    race_rows,
                    target=target,
                    place_positions=place_positions,
                    race_meta=meta,
                    runner_col=runner_col,
                    pos_col=pos_col,
                    model_col=model_col,
                    mkt_place_col=mkt_place_col,
                    win_col=win_col,
                    ts_col=ts_col,
                    source_table=src,
                    require_paired_place_market=require_paired_place_market,
                )
                if rec is not None:
                    records.append(rec)

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
