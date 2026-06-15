"""Robust racing verification automation — emit, trim, verify, data room snapshot."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from metrics.racing import evaluate_racing_window, racing_record_from_dict
from metrics.racing_jsonl_store import append_records, load_jsonl, load_race_ids, trim_jsonl
from metrics.racing_sqlite import extract_settled_races_from_db


@dataclass
class RacingAutomationConfig:
    feature_store: Path
    jsonl_path: Path
    data_room_path: Path
    state_path: Path
    lock_path: Path
    target: str = "place"
    place_positions: int = 3
    table: Optional[str] = None
    max_races_in_file: int = 2500
    min_races_for_verify: int = 1000
    min_races_soft_ok: int = 1
    oos_declared: bool = True


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_state_snapshot(state_path: Path) -> Dict[str, Any]:
    if not state_path.is_file():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _run_outcome_for_status(status: str, *, skipped: bool = False) -> str:
    if skipped or status == "skipped_concurrent":
        return "skipped_concurrent"
    if status in ("error", "preflight_failed", "settlement_failed"):
        return "failed"
    return "completed"


def resolve_automation_config(
    *,
    feature_store: str = "",
    jsonl_path: str = "",
    racing_root: str = "",
    metrics_root: str = "",
) -> RacingAutomationConfig:
    """Resolve paths from env (VPS-friendly)."""
    rr = Path(
        racing_root
        or os.environ.get("HIBS_RACING_DEPLOY_PATH", "")
        or os.environ.get("HIBS_RACING_REPO", "")
        or Path.home() / "hibs-racing"
    )
    if not rr.is_absolute():
        rr = Path.cwd() / rr

    fs = Path(
        feature_store
        or os.environ.get("HIBS_RACING_FEATURE_STORE", "")
        or os.environ.get("FEATURE_STORE", "")
        or rr / "data" / "feature_store.sqlite"
    )
    if not fs.is_file() and fs.name == "feature_store.sqlite":
        alt = Path("/opt/hibs-racing/data/feature_store.sqlite")
        if alt.is_file():
            fs = alt

    jl = Path(
        jsonl_path
        or os.environ.get("RACING_VERIFICATION_JSONL", "")
        or rr / "data" / "verification" / "settled_races.jsonl"
    )
    dr = Path(
        os.environ.get("RACING_DATA_ROOM_JSON", "")
        or jl.parent / "data_room_racing.json"
    )
    state = Path(os.environ.get("RACING_VERIFICATION_STATE", "") or jl.parent / "automation_state.json")
    lock = Path(os.environ.get("RACING_VERIFICATION_LOCK", "") or jl.parent / ".verification.lock")

    target = os.environ.get("RACING_VERIFICATION_TARGET", "place").strip().lower()
    if target not in ("win", "place"):
        target = "place"

    return RacingAutomationConfig(
        feature_store=fs,
        jsonl_path=jl,
        data_room_path=dr,
        state_path=state,
        lock_path=lock,
        target=target,
        place_positions=int(os.environ.get("RACING_PLACE_POSITIONS", "3")),
        table=os.environ.get("RACING_VERIFICATION_TABLE") or None,
        max_races_in_file=int(os.environ.get("RACING_VERIFICATION_MAX_RACES", "2500")),
        min_races_for_verify=int(os.environ.get("RACING_VERIFICATION_MIN_RACES", "1000")),
    )


def preflight_feature_store(cfg: RacingAutomationConfig) -> Dict[str, Any]:
    """Read-only sqlite diagnostics (no crash on missing DB)."""
    out: Dict[str, Any] = {"path": str(cfg.feature_store), "ok": False}
    if not cfg.feature_store.is_file():
        out["error"] = "feature_store_missing"
        return out
    try:
        import sqlite3

        con = sqlite3.connect(f"file:{cfg.feature_store}?mode=ro", uri=True, timeout=15)
        try:
            tables = [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            out["tables"] = tables
            out["size_mb"] = round(cfg.feature_store.stat().st_size / 1_048_576, 2)
            out["ok"] = True
            # quick settled hint on upcoming_runners
            if "upcoming_runners" in tables:
                cols = {r[1] for r in con.execute("PRAGMA table_info(upcoming_runners)")}
                pos_col = next((c for c in ("finish_position", "position", "pos") if c in cols), None)
                score_col = next((c for c in ("score", "place_prob", "p_place") if c in cols), None)
                total = int(con.execute("SELECT COUNT(*) FROM upcoming_runners").fetchone()[0])
                settled = 0
                scored = 0
                if pos_col:
                    settled = int(
                        con.execute(
                            f"SELECT COUNT(*) FROM upcoming_runners WHERE [{pos_col}] IS NOT NULL AND [{pos_col}] > 0"
                        ).fetchone()[0]
                    )
                if score_col:
                    scored = int(
                        con.execute(
                            f"SELECT COUNT(*) FROM upcoming_runners WHERE [{score_col}] IS NOT NULL"
                        ).fetchone()[0]
                    )
                out["upcoming_runners"] = {
                    "total": total,
                    "with_position": settled,
                    "with_score": scored,
                }
        finally:
            con.close()
    except Exception as exc:
        out["error"] = str(exc)[:200]
    return out


@contextmanager
def verification_lock(lock_path: Path, *, wait: bool = False) -> Iterator[None]:
    """POSIX flock — skip second concurrent cron if wait=False."""
    import fcntl

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a+", encoding="utf-8")
    flags = fcntl.LOCK_EX
    if not wait:
        flags |= fcntl.LOCK_NB
    try:
        fcntl.flock(fh.fileno(), flags)
    except BlockingIOError as exc:
        fh.close()
        raise RuntimeError("verification already running (flock)") from exc
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(_utc_iso() + "\n")
        fh.flush()
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def run_racing_verification_pipeline(
    cfg: Optional[RacingAutomationConfig] = None,
    *,
    use_lock: bool = True,
    wait_lock: bool = False,
) -> Dict[str, Any]:
    """Emit new settled races → trim window → institutional verify → persist artifacts."""
    cfg = cfg or resolve_automation_config()
    started = time.time()
    report: Dict[str, Any] = {
        "ok": False,
        "started_at": _utc_iso(),
        "config": {
            "feature_store": str(cfg.feature_store),
            "jsonl_path": str(cfg.jsonl_path),
            "target": cfg.target,
        },
    }

    def _finish(**extra: Any) -> Dict[str, Any]:
        prev = _load_state_snapshot(cfg.state_path)
        report.update(extra)
        report["duration_sec"] = round(time.time() - started, 2)
        report["finished_at"] = _utc_iso()
        status = str(report.get("status", ""))
        skipped = bool(report.get("skipped"))
        report["run_outcome"] = _run_outcome_for_status(status, skipped=skipped)
        if report["run_outcome"] == "completed":
            report["last_full_run_at"] = report["finished_at"]
        elif prev.get("last_full_run_at"):
            report["last_full_run_at"] = prev["last_full_run_at"]
        if report["run_outcome"] == "skipped_concurrent":
            report["locked"] = True
            for key in (
                "window",
                "emit",
                "gates",
                "institutional_grade",
                "thin_window",
                "data_room",
                "settlement_coverage",
            ):
                if key not in report and key in prev:
                    report[key] = prev[key]
        else:
            report["locked"] = False
        try:
            cfg.state_path.parent.mkdir(parents=True, exist_ok=True)
            cfg.state_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        except OSError:
            pass
        return report

    pre = preflight_feature_store(cfg)
    report["preflight"] = pre
    if not pre.get("ok"):
        return _finish(ok=False, status="preflight_failed", hard_fail=True)

    results_path = os.environ.get("RACING_RESULTS_JSON", "").strip()
    if results_path and Path(results_path).is_file():
        from metrics.racing_settlement import apply_results_batch

        try:
            batch = json.loads(Path(results_path).read_text(encoding="utf-8"))
            races_payload = batch if isinstance(batch, list) else batch.get("races", [])
            report["settlement"] = apply_results_batch(
                cfg.feature_store, races_payload, table=cfg.table
            )
        except Exception as exc:
            return _finish(ok=False, status="settlement_failed", error=str(exc)[:200], hard_fail=True)

    from metrics.racing_settlement import settlement_coverage

    report["settlement_coverage"] = settlement_coverage(cfg.feature_store, table=cfg.table)

    def _body() -> Dict[str, Any]:
        existing = load_race_ids(cfg.jsonl_path)
        extracted = extract_settled_races_from_db(
            cfg.feature_store,
            target=cfg.target,
            place_positions=cfg.place_positions,
            table=cfg.table,
            only_settled=True,
        )
        append_stats = append_records(cfg.jsonl_path, extracted, existing_ids=existing)
        trim_stats = trim_jsonl(cfg.jsonl_path, max_races=cfg.max_races_in_file)

        lines = load_jsonl(cfg.jsonl_path)
        races = []
        parse_errors = 0
        parse_error_samples: List[str] = []
        for row in lines:
            if "_corrupt_line" in row:
                parse_errors += 1
                continue
            try:
                races.append(racing_record_from_dict(row))
            except (ValueError, KeyError, TypeError) as exc:
                parse_errors += 1
                if len(parse_error_samples) < 5:
                    parse_error_samples.append(str(exc)[:120])

        n_races = len(races)
        verify = evaluate_racing_window(
            races,
            min_races=cfg.min_races_for_verify,
            oos_declared=cfg.oos_declared,
        ) if n_races else {"error": "no_races", "n_races": 0}

        cfg.data_room_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.data_room_path.write_text(json.dumps(verify, indent=2), encoding="utf-8")

        gates = (verify.get("gates") or {}) if isinstance(verify, dict) else {}
        institutional = bool(gates.get("institutional_grade"))
        thin = n_races < cfg.min_races_for_verify

        status = "accumulating"
        if institutional:
            status = "institutional_grade"
        elif thin:
            status = "accumulating"
        else:
            status = "verified_not_institutional"

        return {
            "ok": True,
            "status": status,
            "hard_fail": False,
            "thin_window": thin,
            "emit": {
                "races_in_db_settled": len(extracted),
                **append_stats,
                **trim_stats,
            },
            "window": {
                "n_races": n_races,
                "parse_errors": parse_errors,
                "parse_error_samples": parse_error_samples,
                "max_races_cap": cfg.max_races_in_file,
                **{k: trim_stats.get(k) for k in (
                    "calendar_span_known",
                    "oldest_race_date",
                    "newest_race_date",
                    "calendar_days_span",
                )},
            },
            "gates": gates,
            "data_room": str(cfg.data_room_path),
            "institutional_grade": institutional,
        }

    try:
        if use_lock:
            with verification_lock(cfg.lock_path, wait=wait_lock):
                report.update(_body())
        else:
            report.update(_body())
    except RuntimeError as exc:
        if "flock" in str(exc):
            return _finish(ok=True, status="skipped_concurrent", skipped=True, hard_fail=False)
        return _finish(ok=False, status="error", error=str(exc), hard_fail=True)
    except Exception as exc:
        return _finish(ok=False, status="error", error=str(exc)[:300], hard_fail=True)

    return _finish()


def exit_code_for_report(report: Dict[str, Any]) -> int:
    """0 = success or benign skip/thin; 1 = hard failure for cron alert."""
    if report.get("hard_fail"):
        return 1
    if report.get("skipped"):
        return 0
    if not report.get("ok"):
        return 1
    return 0
