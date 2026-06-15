#!/usr/bin/env python3
"""Backfill ranker_features.form_* from runners + raceform (fixes 0% form gap).

hibs-racing pipeline sometimes leaves ranker_features form columns empty while
runners (74%+) and raceform.db (1.8M rows) are populated. This script repairs the join.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

FORM_COLS = (
    "form_bf_flag",
    "form_cd_flag",
    "form_lto_position",
    "form_poor_runs_3",
    "form_trip_change_f",
    "horse_course_win_rate",
)

DISPLAY_FORM_COLS = ("form", "form_line", "form_str", "last_runs", "recent_form")
DEFAULT_FORM_RUNS = 6


def position_to_figure(pos: object) -> str:
    """Map finish position to RP-style form character (1-9, 0=10th, F/P/U, - unknown)."""
    raw = str(pos).strip().upper() if pos is not None else ""
    if not raw:
        return "-"
    if raw in {"F", "P", "U", "R", "BD", "SU", "PU", "UR", "RR"}:
        return raw[:2]
    try:
        n = int(float(raw))
    except ValueError:
        return raw[:1]
    if n <= 0:
        return "-"
    if n >= 10:
        return "0"
    return str(n)


def cols(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info([{table}])")}


def count_filled(con: sqlite3.Connection, table: str, column: str) -> tuple[int, int]:
    n = con.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
    if n == 0:
        return 0, 0
    f = con.execute(
        f"SELECT COUNT(*) FROM [{table}] WHERE [{column}] IS NOT NULL "
        f"AND TRIM(CAST([{column}] AS TEXT)) NOT IN ('', '0', '0.0')"
    ).fetchone()[0]
    return f, n


def backfill_from_runners_same_race(con: sqlite3.Connection) -> dict[str, int]:
    rf, run = cols(con, "ranker_features"), cols(con, "runners")
    out: dict[str, int] = {}
    for col in FORM_COLS:
        if col not in rf or col not in run:
            continue
        cur = con.execute(
            f"""
            UPDATE ranker_features AS rf
            SET [{col}] = (
                SELECT r.[{col}] FROM runners r
                WHERE r.runner_id = rf.runner_id AND r.race_id = rf.race_id
                  AND r.[{col}] IS NOT NULL
                  AND TRIM(CAST(r.[{col}] AS TEXT)) NOT IN ('', '0', '0.0')
                LIMIT 1
            )
            WHERE (rf.[{col}] IS NULL OR TRIM(CAST(rf.[{col}] AS TEXT)) IN ('', '0', '0.0'))
              AND EXISTS (
                SELECT 1 FROM runners r
                WHERE r.runner_id = rf.runner_id AND r.race_id = rf.race_id
                  AND r.[{col}] IS NOT NULL
                  AND TRIM(CAST(r.[{col}] AS TEXT)) NOT IN ('', '0', '0.0')
              )
            """
        )
        out[col] = cur.rowcount
    return out


def backfill_from_runners_by_horse(con: sqlite3.Connection) -> dict[str, int]:
    """When race_id differs (upcoming card), use latest runner row per horse_id."""
    rf, run, up = cols(con, "ranker_features"), cols(con, "runners"), cols(con, "upcoming_runners")
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "upcoming_runners" not in tables:
        return {}
    out: dict[str, int] = {}
    for col in FORM_COLS:
        if col not in rf or col not in run:
            continue
        cur = con.execute(
            f"""
            UPDATE ranker_features AS rf
            SET [{col}] = (
                SELECT r.[{col}] FROM runners r
                INNER JOIN upcoming_runners u ON u.runner_id = rf.runner_id
                WHERE r.horse_id = u.horse_id
                  AND r.[{col}] IS NOT NULL
                  AND TRIM(CAST(r.[{col}] AS TEXT)) NOT IN ('', '0', '0.0')
                ORDER BY r.race_date DESC
                LIMIT 1
            )
            WHERE (rf.[{col}] IS NULL OR TRIM(CAST(rf.[{col}] AS TEXT)) IN ('', '0', '0.0'))
              AND EXISTS (
                SELECT 1 FROM upcoming_runners u
                INNER JOIN runners r ON r.horse_id = u.horse_id
                WHERE u.runner_id = rf.runner_id
                  AND r.[{col}] IS NOT NULL
                  AND TRIM(CAST(r.[{col}] AS TEXT)) NOT IN ('', '0', '0.0')
              )
            """
        )
        out[f"{col}_by_horse"] = cur.rowcount
    return out


def backfill_lto_from_raceform(con: sqlite3.Connection, raceform: Path) -> int:
    """Set form_lto_position from raceform last run per horse name."""
    rf = cols(con, "ranker_features")
    if "form_lto_position" not in rf or not raceform.is_file():
        return 0
    up = cols(con, "upcoming_runners")
    if "horse_name" not in up:
        return 0

    rf_cols = cols(con, "ranker_features")
    con.execute("ATTACH DATABASE ? AS rfdb", (str(raceform),))
    try:
        rf_table_cols = {r[1] for r in con.execute("PRAGMA rfdb.table_info([table])")}
    except Exception:
        con.execute("DETACH DATABASE rfdb")
        return 0

    pos_col = next((c for c in ("pos", "position", "plc", "finish_pos") if c in rf_table_cols), None)
    horse_col = next((c for c in ("horse", "horse_name") if c in rf_table_cols), None)
    date_col = next((c for c in ("race_date", "date", "off_dt") if c in rf_table_cols), None)
    if not pos_col or not horse_col:
        con.execute("DETACH DATABASE rfdb")
        return 0

    order = f"rfdb.[{date_col}] DESC" if date_col else "rfdb.rowid DESC"
    cur = con.execute(
        f"""
        UPDATE ranker_features AS feat
        SET form_lto_position = (
            SELECT CAST(rfdb.[{pos_col}] AS REAL)
            FROM rfdb.[table] AS rfdb
            INNER JOIN upcoming_runners u ON u.runner_id = feat.runner_id
            WHERE LOWER(TRIM(rfdb.[{horse_col}])) = LOWER(TRIM(u.horse_name))
            ORDER BY {order}
            LIMIT 1
        )
        WHERE (feat.form_lto_position IS NULL OR feat.form_lto_position = 0)
          AND EXISTS (
            SELECT 1 FROM rfdb.[table] AS rfdb
            INNER JOIN upcoming_runners u ON u.runner_id = feat.runner_id
            WHERE LOWER(TRIM(rfdb.[{horse_col}])) = LOWER(TRIM(u.horse_name))
          )
        """
    )
    n = cur.rowcount
    con.execute("DETACH DATABASE rfdb")
    return n


def backfill_upcoming_form_display(
    con: sqlite3.Connection, raceform: Path, *, runs: int = DEFAULT_FORM_RUNS
) -> int:
    """Fill upcoming_runners form columns from last N raceform runs per horse."""
    up = cols(con, "upcoming_runners")
    targets = [c for c in DISPLAY_FORM_COLS if c in up]
    if not targets or not raceform.is_file():
        return 0

    con.execute("ATTACH DATABASE ? AS rfdb", (str(raceform),))
    rf_cols = {r[1] for r in con.execute("PRAGMA rfdb.table_info([table])")}
    horse_col = next((c for c in ("horse", "horse_name") if c in rf_cols), None)
    pos_col = next((c for c in ("pos", "position", "plc", "finish_pos") if c in rf_cols), None)
    date_col = next((c for c in ("race_date", "date", "off_dt") if c in rf_cols), None)
    if not horse_col or not pos_col:
        con.execute("DETACH DATABASE rfdb")
        return 0

    order = f"rfdb.[{date_col}] DESC" if date_col else "rfdb.rowid DESC"
    runs = max(1, min(int(runs), 12))
    total = 0
    rows = con.execute(
        f"""
        SELECT u.rowid, u.horse_name
        FROM upcoming_runners u
        WHERE EXISTS (
            SELECT 1 FROM rfdb.[table] rfdb
            WHERE LOWER(TRIM(rfdb.[{horse_col}])) = LOWER(TRIM(u.horse_name))
        )
        """
    ).fetchall()
    for rowid, horse_name in rows:
        positions = con.execute(
            f"""
            SELECT CAST(rfdb.[{pos_col}] AS TEXT)
            FROM rfdb.[table] rfdb
            WHERE LOWER(TRIM(rfdb.[{horse_col}])) = LOWER(TRIM(?))
            ORDER BY {order}
            LIMIT ?
            """,
            (horse_name, runs),
        ).fetchall()
        if not positions:
            continue
        figures = "".join(position_to_figure(p[0]) for p in positions)
        dash = "-".join(position_to_figure(p[0]) for p in positions)
        for target in targets:
            val = figures if target in ("form", "form_str", "last_runs") else dash
            cur = con.execute(
                f"""
                UPDATE upcoming_runners
                SET [{target}] = ?
                WHERE rowid = ?
                  AND ([{target}] IS NULL OR TRIM([{target}]) = '')
                """,
                (val, rowid),
            )
            total += cur.rowcount
    con.execute("DETACH DATABASE rfdb")
    return total


def backfill_runners_form_display(
    con: sqlite3.Connection, raceform: Path, *, runs: int = DEFAULT_FORM_RUNS
) -> int:
    """Mirror form string onto runners rows for today's card horses."""
    run_cols = cols(con, "runners")
    targets = [c for c in DISPLAY_FORM_COLS if c in run_cols]
    if not targets or "horse_name" not in run_cols or not raceform.is_file():
        return 0
    if "upcoming_runners" not in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        return 0

    con.execute("ATTACH DATABASE ? AS rfdb", (str(raceform),))
    rf_cols = {r[1] for r in con.execute("PRAGMA rfdb.table_info([table])")}
    horse_col = next((c for c in ("horse", "horse_name") if c in rf_cols), None)
    pos_col = next((c for c in ("pos", "position", "plc", "finish_pos") if c in rf_cols), None)
    date_col = next((c for c in ("race_date", "date", "off_dt") if c in rf_cols), None)
    if not horse_col or not pos_col:
        con.execute("DETACH DATABASE rfdb")
        return 0
    order = f"rfdb.[{date_col}] DESC" if date_col else "rfdb.rowid DESC"
    runs = max(1, min(int(runs), 12))
    total = 0
    horses = con.execute(
        """
        SELECT DISTINCT horse_name FROM upcoming_runners
        WHERE horse_name IS NOT NULL AND TRIM(horse_name) != ''
        """
    ).fetchall()
    for (horse_name,) in horses:
        positions = con.execute(
            f"""
            SELECT CAST(rfdb.[{pos_col}] AS TEXT)
            FROM rfdb.[table] rfdb
            WHERE LOWER(TRIM(rfdb.[{horse_col}])) = LOWER(TRIM(?))
            ORDER BY {order}
            LIMIT ?
            """,
            (horse_name, runs),
        ).fetchall()
        if not positions:
            continue
        figures = "".join(position_to_figure(p[0]) for p in positions)
        dash = "-".join(position_to_figure(p[0]) for p in positions)
        for target in targets:
            val = figures if target in ("form", "form_str", "last_runs") else dash
            cur = con.execute(
                f"""
                UPDATE runners
                SET [{target}] = ?
                WHERE LOWER(TRIM(horse_name)) = LOWER(TRIM(?))
                  AND ([{target}] IS NULL OR TRIM([{target}]) = '')
                """,
                (val, horse_name),
            )
            total += cur.rowcount
    con.execute("DETACH DATABASE rfdb")
    return total


def report_upcoming_form(con: sqlite3.Connection) -> None:
    if "upcoming_runners" not in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        return
    up = cols(con, "upcoming_runners")
    print("upcoming_runners form fill:")
    for col in DISPLAY_FORM_COLS:
        if col not in up:
            continue
        f, n = count_filled(con, "upcoming_runners", col)
        pct = 100.0 * f / n if n else 0
        print(f"  {col}: {f}/{n} ({pct:.1f}%)")
        if f:
            sample = con.execute(
                f"SELECT horse_name, [{col}] FROM upcoming_runners "
                f"WHERE [{col}] IS NOT NULL AND TRIM([{col}]) != '' LIMIT 1"
            ).fetchone()
            if sample:
                print(f"    sample: {sample[0]} -> {sample[1]}")


def report_form(con: sqlite3.Connection) -> None:
    print("ranker_features form fill rates:")
    for col in FORM_COLS:
        if col not in cols(con, "ranker_features"):
            continue
        f, n = count_filled(con, "ranker_features", col)
        pct = 100.0 * f / n if n else 0
        print(f"  {col}: {f}/{n} ({pct:.1f}%)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("feature_store", type=Path, help="path to feature_store.sqlite")
    p.add_argument("--raceform", type=Path, help="path to raceform.db")
    p.add_argument("--runs", type=int, default=DEFAULT_FORM_RUNS, help="last N runs (default 6)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not args.feature_store.is_file():
        print(f"ERROR: missing {args.feature_store}")
        return 1

    raceform = args.raceform or args.feature_store.parent / "raceform.db"
    if not raceform.is_file():
        print(f"ERROR: raceform missing at {raceform}")
        return 1

    con = sqlite3.connect(args.feature_store)
    print(f"==> Before ({args.feature_store})")
    report_form(con)
    report_upcoming_form(con)

    if args.dry_run:
        con.close()
        return 0

    u1 = backfill_from_runners_same_race(con)
    u2 = backfill_from_runners_by_horse(con)
    lto = backfill_lto_from_raceform(con, raceform)
    disp = backfill_upcoming_form_display(con, raceform, runs=args.runs)
    run_disp = backfill_runners_form_display(con, raceform, runs=args.runs)
    con.commit()

    print("==> Updates")
    for k, v in {**u1, **u2}.items():
        if v:
            print(f"  {k}: {v} rows")
    if lto:
        print(f"  form_lto_position from raceform: {lto} rows")
    if disp:
        print(f"  upcoming_runners display form ({args.runs} runs): {disp} cells")
    if run_disp:
        print(f"  runners display form ({args.runs} runs): {run_disp} cells")

    print("==> After")
    report_form(con)
    report_upcoming_form(con)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
