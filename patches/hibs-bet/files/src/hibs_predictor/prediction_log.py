"""
Persistent prediction audit trail + post-match result join for calibration / ROI analysis.

Enabled by default (set HIBS_PREDICTION_LOG_ENABLED=0 to disable). After each fixture bundle
build, log_predictions_from_fixtures runs when HIBS_PREDICTION_LOG_ALWAYS=1 (default on).
CLV: HIBS_CLV_LOG_ENABLED=1 (default when log on) stores opening
1X2 + best-bet odds at capture; pred-log-sync joins closing 1X2 from API-Football fixture odds
when available and computes clv_pp (stake implied vs close, percentage points).
All logging is best-effort and must never break predictions.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

def _db_path() -> str:
    load_dotenv()
    from hibs_predictor.project_paths import resolve_data_path

    return str(resolve_data_path("HIBS_PREDICTION_LOG_DB", "data/prediction_audit.sqlite"))


def _enabled() -> bool:
    load_dotenv()
    raw = (os.getenv("HIBS_PREDICTION_LOG_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _always_log() -> bool:
    """When on, bundle finalization logs every fixture prediction (see min interval)."""
    load_dotenv()
    return (os.getenv("HIBS_PREDICTION_LOG_ALWAYS") or "1").strip().lower() not in ("0", "false", "no", "off")


def _auto_log_max_fixtures() -> int:
    try:
        return max(1, int(os.getenv("HIBS_PREDICTION_LOG_AUTO_MAX", "500")))
    except ValueError:
        return 500


def prediction_log_enabled() -> bool:
    """Public check for audit DB features (calibration shrink, etc.)."""
    return _enabled()


def _clv_enabled() -> bool:
    load_dotenv()
    if not _enabled():
        return False
    raw = (os.getenv("HIBS_CLV_LOG_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _min_interval_sec() -> int:
    try:
        return max(0, int(os.getenv("HIBS_PREDICTION_LOG_MIN_INTERVAL_SEC", "3600")))
    except ValueError:
        return 3600


def _retain_days() -> int:
    try:
        return max(7, int(os.getenv("HIBS_PREDICTION_LOG_RETAIN_DAYS", "365")))
    except ValueError:
        return 365


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, mode=0o755, exist_ok=True)


def init_db() -> None:
    path = _db_path()
    _ensure_dir(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prediction_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                fixture_id INTEGER NOT NULL,
                league_code TEXT,
                kickoff_iso TEXT,
                home_name TEXT,
                away_name TEXT,
                one_x2_mode TEXT,
                xg_source TEXT,
                data_quality_pct REAL,
                prediction_json TEXT NOT NULL,
                enrich_summary_json TEXT,
                result_home INTEGER,
                result_away INTEGER,
                result_outcome TEXT,
                result_status TEXT,
                result_recorded_at TEXT,
                result_xg_home REAL,
                result_xg_away REAL
            )
            """
        )
        _migrate_prediction_log_schema(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_predlog_fixture ON prediction_snapshots(fixture_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_predlog_captured ON prediction_snapshots(captured_at)"
        )
        conn.commit()
    finally:
        conn.close()


def _migrate_prediction_log_schema(conn: sqlite3.Connection) -> None:
    """Append-only schema upgrades for existing audit DBs."""
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(prediction_snapshots)").fetchall()}
    if "result_xg_home" not in cols:
        conn.execute("ALTER TABLE prediction_snapshots ADD COLUMN result_xg_home REAL")
    if "result_xg_away" not in cols:
        conn.execute("ALTER TABLE prediction_snapshots ADD COLUMN result_xg_away REAL")
    conn.commit()


def parse_result_xg_from_statistics(
    stats_response: Any,
    *,
    home_team_id: Optional[int] = None,
    away_team_id: Optional[int] = None,
    home_name: Optional[str] = None,
    away_name: Optional[str] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """Extract home/away xG from API-Football fixtures/statistics response."""
    if not isinstance(stats_response, list) or len(stats_response) < 2:
        return None, None
    try:
        from hibs_predictor.live_scores import parse_live_statistics

        _, xg_h, xg_a = parse_live_statistics(
            stats_response,
            home_name=home_name,
            away_name=away_name,
        )
        if xg_h is not None and xg_a is not None:
            return float(xg_h), float(xg_a)
    except Exception:
        pass
    if home_team_id and away_team_id:
        try:
            from hibs_predictor.betting_engine import TeamStrengthCalculator

            pseudo = {"statistics": stats_response}
            xh = TeamStrengthCalculator._team_xg_from_fixture_statistics(pseudo, int(home_team_id))
            xa = TeamStrengthCalculator._team_xg_from_fixture_statistics(pseudo, int(away_team_id))
            if xh is not None and xa is not None:
                return float(xh), float(xa)
        except Exception:
            pass
    return None, None


def _fixture_id(fixture: Dict[str, Any]) -> Optional[int]:
    """Numeric API-Football fixture id for audit + settlement (never FotMob slugs)."""
    from hibs_predictor.live_scores import api_fixture_id_for_row

    return api_fixture_id_for_row(fixture)


def _kickoff_iso(fixture: Dict[str, Any]) -> str:
    raw = fixture.get("date") or ""
    if isinstance(raw, str):
        return raw
    return ""


def _implied_from_decimal(odds: Any) -> Optional[float]:
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o <= 1.0:
        return None
    return 1.0 / o


def compute_clv_pp(implied_open: Optional[float], implied_close: Optional[float]) -> Optional[float]:
    """CLV in percentage points: positive when closing implied > stake (line moved toward your pick)."""
    if implied_open is None or implied_close is None:
        return None
    return round((implied_close - implied_open) * 100.0, 2)


def parse_closing_1x2_from_odds_response(odds_raw: Any) -> Dict[str, Optional[float]]:
    """Best Match Winner prices across bookmakers in API-Football odds response."""
    best: Dict[str, Optional[float]] = {"home": None, "draw": None, "away": None}
    if not isinstance(odds_raw, list):
        return best
    for entry in odds_raw:
        if not isinstance(entry, dict):
            continue
        for bm in entry.get("bookmakers", []) or []:
            for bet in bm.get("bets", []) or []:
                if bet.get("name") != "Match Winner":
                    continue
                for v in bet.get("values", []) or []:
                    val = (v.get("value") or "").lower()
                    try:
                        price = float(v.get("odd", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    if price <= 1.0:
                        continue
                    if val in best:
                        cur = best[val]
                        best[val] = price if cur is None else max(cur, price)
    return best


def _decimal_odds_side(raw: Any) -> Optional[float]:
    try:
        v = float(raw)
        return v if v > 1.0 else None
    except (TypeError, ValueError):
        return None


def _opening_1x2_odds(fixture: Dict[str, Any], prediction: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Best-effort opening 1X2 from bookmaker_odds, line_odds, then fixture decimal fields."""
    opening: Dict[str, Optional[float]] = {s: None for s in ("home", "draw", "away")}
    for src in (prediction.get("bookmaker_odds"), prediction.get("line_odds")):
        if not isinstance(src, dict):
            continue
        for side in opening:
            if opening[side] is None:
                opening[side] = _decimal_odds_side(src.get(side))
    for side, fk in (("home", "odds_home"), ("draw", "odds_draw"), ("away", "odds_away")):
        if opening[side] is None:
            opening[side] = _decimal_odds_side(fixture.get(fk))
    return opening


def _audit_odds_retry_enabled() -> bool:
    return (os.getenv("HIBS_AUDIT_ODDS_RETRY") or "1").strip().lower() in ("1", "true", "yes", "on")


def audit_require_odds_for_batch() -> bool:
    """When true, batch audit logging skips fixtures that still lack 1X2 after retry."""
    return (os.getenv("HIBS_AUDIT_REQUIRE_ODDS") or "1").strip().lower() in ("1", "true", "yes", "on")


def prediction_has_stored_1x2(pred: Dict[str, Any]) -> bool:
    """True when prediction_json can support value replay / CLV opening (1X2 decimals)."""
    for src in (pred.get("bookmaker_odds"), pred.get("line_odds")):
        if not isinstance(src, dict):
            continue
        for side in ("home", "draw", "away"):
            if _decimal_odds_side(src.get(side)) is not None:
                return True
    return False


def _shallow_prediction_copy(pred: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(pred)
    if isinstance(pred.get("bookmaker_odds"), dict):
        out["bookmaker_odds"] = dict(pred["bookmaker_odds"])
    if isinstance(pred.get("line_odds"), dict):
        out["line_odds"] = dict(pred["line_odds"])
    return out


def _apply_fixture_1x2_to_prediction(fixture: Dict[str, Any], prediction: Dict[str, Any]) -> None:
    from hibs_predictor.betting_engine import _best_1x2_odds_from_fixture, _line_odds_from_fixture_markets

    best = _best_1x2_odds_from_fixture(fixture)
    if best:
        bo = dict(prediction.get("bookmaker_odds") or {})
        for side, price in best.items():
            if _decimal_odds_side(bo.get(side)) is None:
                bo[side] = price
        prediction["bookmaker_odds"] = bo
    side_lines = _line_odds_from_fixture_markets(fixture)
    if side_lines:
        lo = dict(prediction.get("line_odds") or {})
        for k, v in side_lines.items():
            lo.setdefault(k, v)
        prediction["line_odds"] = lo
    if best and len(best) == 3:
        fixture["odds_available"] = True


def _merge_odds_bundle_into_fixture(fixture: Dict[str, Any], bundle: Dict[str, Any]) -> None:
    for key in (
        "odds_home",
        "odds_draw",
        "odds_away",
        "odds_available",
        "market_odds",
        "all_bookmaker_odds",
        "best_odds_1x2",
        "best_odds_source",
        "odds_cross_max_implied_diff_pct",
        "odds_cross_book_max_implied_diff_pct",
        "sharp_anchor_implied",
        "sharp_anchor_implied_shin",
        "odds_primary_source",
    ):
        if key in bundle and bundle[key] is not None:
            fixture[key] = bundle[key]


def ensure_snapshot_odds(fixture: Dict[str, Any], prediction: Dict[str, Any]) -> Dict[str, Any]:
    """
    Best-effort 1X2 capture before persisting audit rows.

    Order: stored prediction → fixture triplet / line shop → one Odds API + API-Football bundle fetch.
    Never raises; returns metadata for ops metrics.
    """
    meta: Dict[str, Any] = {
        "had_odds": prediction_has_stored_1x2(prediction),
        "captured": False,
        "source": None,
    }
    if meta["had_odds"]:
        meta["captured"] = True
        meta["source"] = "stored"
        return meta

    _apply_fixture_1x2_to_prediction(fixture, prediction)
    if prediction_has_stored_1x2(prediction):
        meta["captured"] = True
        meta["source"] = "fixture"
        return meta

    if not _audit_odds_retry_enabled():
        return meta

    league = str(fixture.get("league") or "").strip()
    if not league:
        return meta

    try:
        from hibs_predictor.data_aggregator import DataAggregator

        agg = DataAggregator()
        bundle = agg._fetch_odds_bundle(fixture, league)
        if isinstance(bundle, dict) and bundle:
            _merge_odds_bundle_into_fixture(fixture, bundle)
            _apply_fixture_1x2_to_prediction(fixture, prediction)
            if prediction_has_stored_1x2(prediction):
                meta["captured"] = True
                meta["source"] = str(bundle.get("odds_primary_source") or "fetch_bundle")
            elif bundle.get("odds_thin_rescue"):
                meta["thin_rescue"] = bundle.get("odds_thin_rescue")
    except Exception as exc:
        meta["fetch_error"] = str(exc)[:120]
        try:
            from hibs_predictor.app_logging import get_logger

            get_logger("prediction_log").warning(
                "audit odds retry failed fixture_id=%s league=%s: %s",
                _fixture_id(fixture),
                league,
                exc,
            )
        except Exception:
            pass

    return meta


def _clv_opening_capture(fixture: Dict[str, Any], prediction: Dict[str, Any]) -> Dict[str, Any]:
    """Opening-line capture for CLV analysis."""
    opening = _opening_1x2_odds(fixture, prediction)
    best = prediction.get("best_bet")
    best_row = (prediction.get("value_bets") or {}).get(best) if best else None
    best_odds = None
    if isinstance(best_row, dict):
        try:
            o = best_row.get("odds")
            best_odds = float(o) if o is not None and float(o) > 1.0 else None
        except (TypeError, ValueError):
            best_odds = None
    clv = {
        "opening_odds_1x2": opening,
        "best_bet_outcome": best,
        "best_bet_odds": best_odds,
        "best_bet_edge_pct": (best_row or {}).get("edge_pct") if isinstance(best_row, dict) else None,
        "odds_cross_max_implied_diff_pct": float(fixture.get("odds_cross_max_implied_diff_pct") or 0.0),
        "closing_odds_1x2": None,
        "clv_pp": None,
    }
    try:
        from hibs_predictor.price_truth import enrich_clv_price_truth, model_probs_from_prediction

        clv = enrich_clv_price_truth(clv, model_probs=model_probs_from_prediction(prediction))
    except Exception:
        pass
    return clv


def _resolve_data_quality_pct(fixture: Dict[str, Any], prediction: Optional[Dict[str, Any]] = None) -> float:
    """Persist non-zero data_quality_pct on historic rows (fixture → prediction → recompute)."""
    for src in (fixture, prediction or {}):
        dq = src.get("data_quality") if isinstance(src, dict) else None
        if not isinstance(dq, dict):
            continue
        try:
            pct = float(dq.get("score_pct") or 0)
        except (TypeError, ValueError):
            pct = 0.0
        if pct > 0:
            return pct
    try:
        from hibs_predictor.data_quality import compute_fixture_data_quality

        computed = compute_fixture_data_quality(fixture)
        return float(computed.get("score_pct") or 0)
    except Exception:
        return 0.0


def _enrich_summary(fixture: Dict[str, Any], prediction: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    dq = fixture.get("data_quality") or {}
    hp = fixture.get("home_position") or {}
    ap = fixture.get("away_position") or {}
    out: Dict[str, Any] = {
        "home_recent_n": int(fixture.get("home_recent_n") or 0),
        "away_recent_n": int(fixture.get("away_recent_n") or 0),
        "odds_available": bool(fixture.get("odds_available")),
        "has_home_stats": bool((fixture.get("home_stats") or {}).get("played")),
        "has_away_stats": bool((fixture.get("away_stats") or {}).get("played")),
        "home_table": bool(hp.get("position")),
        "away_table": bool(ap.get("position")),
        "data_quality_pct": float(dq.get("score_pct") or 0),
        "full_scope": bool(dq.get("full_scope")),
        "injuries_n": len(fixture.get("fixture_injuries") or []),
        "lineup_confirmed": bool(fixture.get("lineup_confirmed")),
    }
    meta = fixture.get("team_news_meta") or {}
    if meta.get("home_absences") or meta.get("away_absences"):
        out["team_news_absences"] = {
            "home": int(meta.get("home_absences") or 0),
            "away": int(meta.get("away_absences") or 0),
        }
    lmeta = fixture.get("lineup_meta") or {}
    if lmeta.get("home_scorers_out_of_xi") or lmeta.get("away_scorers_out_of_xi"):
        out["lineup_scorers_out"] = {
            "home": len(lmeta.get("home_scorers_out_of_xi") or []),
            "away": len(lmeta.get("away_scorers_out_of_xi") or []),
        }
    if lmeta.get("home_xi_n") or lmeta.get("away_xi_n"):
        out["lineup_xi_n"] = {
            "home": int(lmeta.get("home_xi_n") or 0),
            "away": int(lmeta.get("away_xi_n") or 0),
        }
    if fixture.get("attack_availability_home") is not None:
        out["attack_availability_home"] = fixture.get("attack_availability_home")
    if fixture.get("attack_availability_away") is not None:
        out["attack_availability_away"] = fixture.get("attack_availability_away")
    if _clv_enabled() and prediction:
        out["clv"] = _clv_opening_capture(fixture, prediction)
    return out


def maybe_log_prediction_snapshot(
    fixture: Dict[str, Any],
    prediction: Dict[str, Any],
    *,
    skip_interval: bool = False,
    odds_meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a snapshot row if logging is enabled and interval / dedupe rules pass."""
    if not _enabled():
        return
    if prediction.get("prediction_unavailable"):
        return
    fid = _fixture_id(fixture)
    if not fid:
        return
    if odds_meta is None:
        fixture = dict(fixture)
        prediction = _shallow_prediction_copy(prediction)
        odds_meta = ensure_snapshot_odds(fixture, prediction)
    try:
        init_db()
        path = _db_path()
        now_iso = datetime.now(timezone.utc).isoformat()
        interval = _min_interval_sec()
        conn = sqlite3.connect(path, timeout=15)
        try:
            if interval > 0 and not skip_interval:
                cur = conn.execute(
                    "SELECT captured_at FROM prediction_snapshots WHERE fixture_id = ? ORDER BY id DESC LIMIT 1",
                    (fid,),
                )
                r = cur.fetchone()
                if r and r[0]:
                    try:
                        raw_ts = str(r[0]).replace("Z", "+00:00")
                        last = datetime.fromisoformat(raw_ts)
                        if last.tzinfo is None:
                            last = last.replace(tzinfo=timezone.utc)
                        if (datetime.now(timezone.utc) - last).total_seconds() < float(interval):
                            return
                    except Exception:
                        pass
            league = str(fixture.get("league") or "")
            home_nm = str(prediction.get("home") or "?")
            away_nm = str(prediction.get("away") or "?")
            mode = str(prediction.get("one_x2_mode") or os.getenv("HIBS_1X2_MODE", "ensemble"))
            xg_src = str(fixture.get("xg_source") or prediction.get("xg_source") or "")
            dq = fixture.get("data_quality") or {}
            dq_pct = _resolve_data_quality_pct(fixture, prediction)
            enrich = _enrich_summary(fixture, prediction)
            enrich["odds_capture"] = {
                "captured": bool(odds_meta.get("captured")),
                "source": odds_meta.get("source"),
            }
            if odds_meta.get("fetch_error"):
                enrich["odds_capture"]["fetch_error"] = odds_meta["fetch_error"]
            try:
                from hibs_predictor.price_truth import attach_price_panel_to_prediction

                attach_price_panel_to_prediction(fixture, prediction)
            except Exception:
                pass
            pred_json = json.dumps(prediction, default=str)
            sum_json = json.dumps(enrich, default=str)
            conn.execute(
                """
                INSERT INTO prediction_snapshots (
                    captured_at, fixture_id, league_code, kickoff_iso, home_name, away_name,
                    one_x2_mode, xg_source, data_quality_pct, prediction_json, enrich_summary_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    now_iso,
                    fid,
                    league,
                    _kickoff_iso(fixture),
                    home_nm,
                    away_nm,
                    mode,
                    xg_src,
                    dq_pct,
                    pred_json,
                    sum_json,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        # Never break the betting pipeline; log for institutional ops visibility.
        try:
            from hibs_predictor.app_logging import get_logger

            get_logger("prediction_log").warning(
                "prediction snapshot skipped fixture_id=%s: %s",
                fid,
                exc,
                exc_info=True,
            )
        except Exception:
            pass
        return


def _has_scored_snapshot(conn: sqlite3.Connection, fixture_id: int) -> bool:
    cur = conn.execute(
        """
        SELECT 1 FROM prediction_snapshots
        WHERE fixture_id = ? AND result_outcome IS NOT NULL AND TRIM(result_outcome) != ''
        LIMIT 1
        """,
        (fixture_id,),
    )
    return cur.fetchone() is not None


def _latest_unscored_snapshot_id(conn: sqlite3.Connection, fixture_id: int) -> Optional[int]:
    cur = conn.execute(
        """
        SELECT id FROM prediction_snapshots
        WHERE fixture_id = ?
          AND (result_outcome IS NULL OR TRIM(result_outcome) = '')
        ORDER BY id DESC LIMIT 1
        """,
        (fixture_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def insert_historic_snapshot(
    fixture: Dict[str, Any],
    prediction: Dict[str, Any],
    result: Dict[str, Any],
) -> str:
    """
    Insert or update an audit row for historic backfill (FT result known at write time).

    Uses ``api_fixture_id`` as ``fixture_id``. Sets ``captured_at`` to kickoff minus 2 hours
    (synthetic pre-kickoff lock). Skips when a scored row already exists; updates the latest
    unscored row for the same fixture only.

    Returns: ``inserted``, ``updated_unscored``, ``skipped_scored``, ``skipped_no_id``, or ``error``.
    """
    if not _enabled():
        return "skipped_disabled"
    if prediction.get("prediction_unavailable"):
        return "skipped_unavailable"
    fid = _fixture_id(fixture)
    if not fid:
        return "skipped_no_id"
    try:
        hg = int(result["home"])
        ag = int(result["away"])
    except (KeyError, TypeError, ValueError):
        return "error"
    outcome = str(result.get("outcome") or _outcome_from_goals(hg, ag))
    status = str(result.get("status") or "FT")
    ko_iso = _kickoff_iso(fixture)
    ko_dt = _parse_kickoff_iso(ko_iso)
    if ko_dt is None:
        captured_iso = datetime.now(timezone.utc).isoformat()
    else:
        captured_iso = (ko_dt - timedelta(hours=2)).isoformat()
    recorded_iso = datetime.now(timezone.utc).isoformat()
    try:
        init_db()
        path = _db_path()
        league = str(fixture.get("league") or "")
        home_nm = str(prediction.get("home") or fixture.get("home", {}).get("name") or "?")
        away_nm = str(prediction.get("away") or fixture.get("away", {}).get("name") or "?")
        mode = str(prediction.get("one_x2_mode") or os.getenv("HIBS_1X2_MODE", "ensemble"))
        xg_src = str(fixture.get("xg_source") or prediction.get("xg_source") or "")
        dq = fixture.get("data_quality") or {}
        dq_pct = _resolve_data_quality_pct(fixture, prediction)
        pred_json = json.dumps(prediction, default=str)
        sum_json = json.dumps(_enrich_summary(fixture, prediction), default=str)
        conn = sqlite3.connect(path, timeout=15)
        try:
            if _has_scored_snapshot(conn, fid):
                return "skipped_scored"
            row_id = _latest_unscored_snapshot_id(conn, fid)
            if row_id is not None:
                conn.execute(
                    """
                    UPDATE prediction_snapshots SET
                        captured_at = ?, league_code = ?, kickoff_iso = ?, home_name = ?, away_name = ?,
                        one_x2_mode = ?, xg_source = ?, data_quality_pct = ?,
                        prediction_json = ?, enrich_summary_json = ?,
                        result_home = ?, result_away = ?, result_outcome = ?,
                        result_status = ?, result_recorded_at = ?
                    WHERE id = ?
                    """,
                    (
                        captured_iso,
                        league,
                        ko_iso,
                        home_nm,
                        away_nm,
                        mode,
                        xg_src,
                        dq_pct,
                        pred_json,
                        sum_json,
                        hg,
                        ag,
                        outcome,
                        status,
                        recorded_iso,
                        row_id,
                    ),
                )
                conn.commit()
                return "updated_unscored"
            conn.execute(
                """
                INSERT INTO prediction_snapshots (
                    captured_at, fixture_id, league_code, kickoff_iso, home_name, away_name,
                    one_x2_mode, xg_source, data_quality_pct, prediction_json, enrich_summary_json,
                    result_home, result_away, result_outcome, result_status, result_recorded_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    captured_iso,
                    fid,
                    league,
                    ko_iso,
                    home_nm,
                    away_nm,
                    mode,
                    xg_src,
                    dq_pct,
                    pred_json,
                    sum_json,
                    hg,
                    ag,
                    outcome,
                    status,
                    recorded_iso,
                ),
            )
            conn.commit()
            return "inserted"
        finally:
            conn.close()
    except Exception:
        return "error"


def _outcome_from_goals(h: int, a: int) -> str:
    if h > a:
        return "home"
    if h < a:
        return "away"
    return "draw"


def _parse_kickoff_iso(iso: str) -> Optional[datetime]:
    if not iso or len(iso) < 10:
        return None
    try:
        raw = str(iso).replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _apply_clv_to_enrich_summary(
    enrich: Dict[str, Any],
    closing: Dict[str, Optional[float]],
    *,
    prediction: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    clv = enrich.get("clv")
    if not isinstance(clv, dict):
        return enrich
    opening = clv.get("opening_odds_1x2") or {}
    clv["closing_odds_1x2"] = closing
    outcome = clv.get("best_bet_outcome")
    open_odds = clv.get("best_bet_odds")
    if open_odds is None and outcome:
        open_odds = (opening or {}).get(str(outcome))
    close_side = closing.get(str(outcome)) if outcome else None
    impl_open = _implied_from_decimal(open_odds)
    impl_close = _implied_from_decimal(close_side)
    clv["clv_pp"] = compute_clv_pp(impl_open, impl_close)
    try:
        from hibs_predictor.price_truth import enrich_clv_price_truth, model_probs_from_prediction

        model = model_probs_from_prediction(prediction or {})
        enrich["clv"] = enrich_clv_price_truth(clv, model_probs=model or None)
    except Exception:
        enrich["clv"] = clv
    return enrich


def _kickoff_date_str(kickoff_iso: str) -> Optional[str]:
    ko = _parse_kickoff_iso(kickoff_iso)
    if ko is None:
        return None
    return ko.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _match_api_fixture_row(
    candidates: List[Dict[str, Any]],
    *,
    home_name: str,
    away_name: str,
) -> Optional[Dict[str, Any]]:
    from hibs_predictor.team_aliases import team_names_match

    home_nm = (home_name or "").strip()
    away_nm = (away_name or "").strip()
    if not home_nm or not away_nm:
        return None
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        teams = raw.get("teams") or {}
        h = ((teams.get("home") or {}).get("name") or "").strip()
        a = ((teams.get("away") or {}).get("name") or "").strip()
        if team_names_match(home_nm, h) and team_names_match(away_nm, a):
            return raw
    return None


def _resolve_api_fixture_id(
    row: sqlite3.Row,
    *,
    fetch_fixture_fn: Any,
    fetch_by_league_fn: Any = None,
    fetch_by_date_fn: Any = None,
) -> Tuple[Optional[int], Optional[Dict[str, Any]], str]:
    """
    Resolve numeric API fixture id + raw row for settlement.

    Returns (api_fixture_id, raw_fixture_row, resolution_note).
    """
    from hibs_predictor.live_scores import parse_fixture_id_int

    stored = row["fixture_id"]
    fid_int = parse_fixture_id_int(stored)
    raw: Optional[Dict[str, Any]] = None
    if fid_int is not None:
        try:
            raw = fetch_fixture_fn(fid_int)
        except Exception:
            raw = None
        if isinstance(raw, dict) and raw.get("fixture"):
            return fid_int, raw, "direct_id"
    home_nm = str(row["home_name"] or "")
    away_nm = str(row["away_name"] or "")
    kick = str(row["kickoff_iso"] or "")
    league = str(row["league_code"] or "")
    day = _kickoff_date_str(kick)
    if not day:
        return None, None, "no_kickoff_date"
    try:
        from hibs_predictor.config import LEAGUES
        from hibs_predictor.season import season_candidates

        league_api_id = LEAGUES.get(league, {}).get("api_sports_id")
    except Exception:
        league_api_id = None
    candidates: List[Dict[str, Any]] = []
    if fetch_by_league_fn is not None and league_api_id:
        try:
            season = season_candidates(league_code=league)[0]
            candidates = fetch_by_league_fn(int(league_api_id), int(season), date_from=day, date_to=day) or []
        except Exception:
            candidates = []
    if not candidates and fetch_by_date_fn is not None:
        try:
            candidates = fetch_by_date_fn(day, league_id=int(league_api_id) if league_api_id else None) or []
        except Exception:
            candidates = []
    matched = _match_api_fixture_row(candidates, home_name=home_nm, away_name=away_nm)
    if not matched:
        return None, None, "unresolved_teams"
    fx = matched.get("fixture") or {}
    try:
        resolved = int(fx.get("id"))
    except (TypeError, ValueError):
        return None, None, "unresolved_id"
    return resolved, matched, "resolved_by_teams"


def _apply_ft_to_snapshots(
    conn: sqlite3.Connection,
    *,
    fixture_id: int,
    hi: int,
    ai: int,
    status: str,
    res_xg_h: Optional[float],
    res_xg_a: Optional[float],
    stored_fixture_id: Any,
) -> int:
    oc = _outcome_from_goals(hi, ai)
    rec_at = datetime.now(timezone.utc).isoformat()
    try:
        stored_int = int(stored_fixture_id)
    except (TypeError, ValueError):
        stored_int = fixture_id
    cur = conn.execute(
        """
        UPDATE prediction_snapshots
        SET result_home=?, result_away=?, result_outcome=?, result_status=?, result_recorded_at=?,
            result_xg_home=?, result_xg_away=?, fixture_id=?
        WHERE fixture_id=? AND result_recorded_at IS NULL
        """,
        (hi, ai, oc, status, rec_at, res_xg_h, res_xg_a, fixture_id, stored_int),
    )
    n = int(cur.rowcount or 0)
    conn.commit()
    return n


def sync_finished_results(
    fetch_fixture_fn: Any,
    *,
    fetch_odds_fn: Any = None,
    fetch_statistics_fn: Any = None,
    fetch_by_league_fn: Any = None,
    fetch_by_date_fn: Any = None,
    max_fixtures: int = 400,
    min_after_kickoff_hours: float = 2.5,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    For snapshots missing results, fetch fixture status via API and fill goals when FT.

    ``fetch_fixture_fn``: ``ApiSportsFootballClient.fetch_fixture`` (fixture_id -> raw response row).
    ``fetch_odds_fn``: optional ``fetch_odds`` for closing 1X2 when ``HIBS_CLV_LOG_ENABLED=1``.
    ``fetch_statistics_fn``: optional ``fetch_fixture_statistics`` for post-match xG join.
    Returns stats dict with ``updated`` row count (can exceed distinct fixtures).
    """
    stats: Dict[str, Any] = {
        "updated": 0,
        "pending_fixture_groups": 0,
        "skipped_too_recent": 0,
        "not_ft_yet": 0,
        "api_fetch_failed": 0,
        "unresolved_fixture": 0,
        "resolved_by_teams": 0,
    }
    path = _db_path()
    if not _enabled() and not os.path.isfile(path):
        stats["message"] = "prediction_log_disabled"
        return stats
    init_db()
    conn = sqlite3.connect(path, timeout=20)
    conn.row_factory = sqlite3.Row
    updated = 0
    now = datetime.now(timezone.utc)
    min_after = timedelta(hours=float(min_after_kickoff_hours))
    try:
        rows = conn.execute(
            """
            SELECT fixture_id, MIN(kickoff_iso) AS kickoff_iso,
                   MAX(home_name) AS home_name, MAX(away_name) AS away_name,
                   MAX(league_code) AS league_code
            FROM prediction_snapshots
            WHERE result_recorded_at IS NULL
            GROUP BY fixture_id
            ORDER BY MIN(kickoff_iso)
            LIMIT ?
            """,
            (int(max_fixtures),),
        ).fetchall()
        stats["pending_fixture_groups"] = len(rows)
        for r in rows:
            kick_raw = r["kickoff_iso"] or ""
            ko = _parse_kickoff_iso(str(kick_raw))
            if ko is not None and now < ko + min_after:
                stats["skipped_too_recent"] += 1
                continue
            fid_int, raw, note = _resolve_api_fixture_id(
                r,
                fetch_fixture_fn=fetch_fixture_fn,
                fetch_by_league_fn=fetch_by_league_fn,
                fetch_by_date_fn=fetch_by_date_fn,
            )
            if note == "resolved_by_teams":
                stats["resolved_by_teams"] += 1
            if fid_int is None or not isinstance(raw, dict):
                if note in ("unresolved_teams", "unresolved_id", "no_kickoff_date"):
                    stats["unresolved_fixture"] += 1
                else:
                    stats["api_fetch_failed"] += 1
                continue
            status = ((raw.get("fixture") or {}).get("status") or {}).get("short") or ""
            goals = raw.get("goals") or {}
            gh, ga = goals.get("home"), goals.get("away")
            if status != "FT" or gh is None or ga is None:
                stats["not_ft_yet"] += 1
                continue
            try:
                hi, ai = int(gh), int(ga)
            except (TypeError, ValueError):
                continue
            res_xg_h: Optional[float] = None
            res_xg_a: Optional[float] = None
            if fetch_statistics_fn is not None:
                try:
                    stats_raw = fetch_statistics_fn(fid_int)
                    teams = raw.get("teams") or {}
                    hid = ((teams.get("home") or {}).get("id"))
                    aid = ((teams.get("away") or {}).get("id"))
                    hnm = ((teams.get("home") or {}).get("name"))
                    anm = ((teams.get("away") or {}).get("name"))
                    res_xg_h, res_xg_a = parse_result_xg_from_statistics(
                        stats_raw,
                        home_team_id=int(hid) if hid is not None else None,
                        away_team_id=int(aid) if aid is not None else None,
                        home_name=str(hnm) if hnm else None,
                        away_name=str(anm) if anm else None,
                    )
                except Exception:
                    res_xg_h = res_xg_a = None
            updated += _apply_ft_to_snapshots(
                conn,
                fixture_id=fid_int,
                hi=hi,
                ai=ai,
                status=status,
                res_xg_h=res_xg_h,
                res_xg_a=res_xg_a,
                stored_fixture_id=r["fixture_id"],
            )
            if _clv_enabled() and fetch_odds_fn is not None:
                try:
                    odds_raw = fetch_odds_fn(fid_int)
                    closing = parse_closing_1x2_from_odds_response(odds_raw)
                except Exception as odds_exc:
                    try:
                        from hibs_predictor.app_logging import get_logger

                        get_logger("prediction_log").warning(
                            "CLV closing odds fetch failed fixture_id=%s: %s",
                            fid_int,
                            odds_exc,
                        )
                    except Exception:
                        pass
                    closing = {"home": None, "draw": None, "away": None}
                snap_rows = conn.execute(
                    """
                    SELECT id, enrich_summary_json, prediction_json FROM prediction_snapshots
                    WHERE fixture_id IN (?, ?) AND enrich_summary_json IS NOT NULL
                    """,
                    (fid_int, int(r["fixture_id"])),
                ).fetchall()
                for sr in snap_rows:
                    try:
                        enrich = json.loads(sr["enrich_summary_json"])
                    except Exception:
                        continue
                    if not isinstance(enrich, dict):
                        continue
                    if "clv" not in enrich and enrich.get("odds_available"):
                        try:
                            pred = json.loads(sr["prediction_json"] or "{}")
                        except Exception:
                            pred = {}
                        if isinstance(pred, dict) and pred:
                            enrich["clv"] = _clv_opening_capture({}, pred)
                    if "clv" not in enrich:
                        continue
                    try:
                        pred = json.loads(sr["prediction_json"] or "{}")
                    except Exception:
                        pred = {}
                    enrich = _apply_clv_to_enrich_summary(
                        enrich,
                        closing,
                        prediction=pred if isinstance(pred, dict) else None,
                    )
                    conn.execute(
                        "UPDATE prediction_snapshots SET enrich_summary_json=? WHERE id=?",
                        (json.dumps(enrich, default=str), sr["id"]),
                    )
                conn.commit()
    finally:
        conn.close()
    stats["updated"] = int(updated)
    scored = conn_row_count_scored() if updated else None
    if scored is not None:
        stats["n_scored_total"] = scored
    if verbose:
        stats["message"] = (
            f"Updated {updated} snapshot row(s); "
            f"pending groups {stats['pending_fixture_groups']}; "
            f"resolved_by_teams {stats['resolved_by_teams']}; "
            f"unresolved {stats['unresolved_fixture']}"
        )
    return stats


def conn_row_count_scored() -> Optional[int]:
    try:
        conn = sqlite3.connect(_db_path(), timeout=10)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM prediction_snapshots WHERE result_outcome IS NOT NULL AND result_outcome != ''"
            ).fetchone()
            return int(row[0] if row else 0)
        finally:
            conn.close()
    except Exception:
        return None


def run_pred_log_sync_for_web(
    *,
    max_fixtures: int = 400,
    min_after_kickoff_hours: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Backfill FT scores for pending audit rows (same as ``pred-log-sync`` CLI).

    Read-only for predictions/DQ — only updates result columns on existing snapshots.
    """
    if not prediction_log_enabled():
        return {
            "ok": False,
            "enabled": False,
            "updated": 0,
            "message": (
                "Model monitor off — set HIBS_PREDICTION_LOG_ENABLED=1 and restart the app."
            ),
        }
    init_db()
    path = _db_path()
    try:
        conn = sqlite3.connect(path, timeout=15)
        try:
            row = conn.execute("SELECT COUNT(*) FROM prediction_snapshots").fetchone()
            n_snap = int(row[0] if row else 0)
        finally:
            conn.close()
    except Exception:
        n_snap = 0
    if n_snap <= 0:
        return {
            "ok": False,
            "enabled": True,
            "updated": 0,
            "message": (
                "No snapshots yet — open the dashboard before kick-off so fixtures are logged."
            ),
        }
    try:
        from hibs_predictor.data_aggregator import DataAggregator

        agg = DataAggregator()
    except Exception as exc:
        return {"ok": False, "enabled": True, "updated": 0, "message": str(exc)}
    if "api_sports" not in agg.clients:
        return {
            "ok": False,
            "enabled": True,
            "updated": 0,
            "message": "API_SPORTS_FOOTBALL_KEY is required to sync finished results.",
        }
    min_h = min_after_kickoff_hours
    if min_h is None:
        try:
            min_h = float(os.getenv("HIBS_PRED_LOG_SYNC_MIN_HOURS", "2.5"))
        except ValueError:
            min_h = 2.5
    client = agg.clients["api_sports"]
    fetch_stats = getattr(client, "fetch_fixture_statistics", None)
    sync_stats = sync_finished_results(
        client.fetch_fixture,
        fetch_odds_fn=client.fetch_odds,
        fetch_statistics_fn=fetch_stats,
        fetch_by_league_fn=client.fetch_fixtures_by_league,
        fetch_by_date_fn=getattr(client, "fetch_fixtures_by_date", None),
        max_fixtures=int(max_fixtures),
        min_after_kickoff_hours=float(min_h),
    )
    updated = int(sync_stats.get("updated") or 0)
    msg = sync_stats.get("message") or (
        f"Updated {updated} snapshot row(s) with full-time results."
        if updated
        else "No pending fixtures needed an update (already synced or not FT yet)."
    )
    return {
        "ok": True,
        "enabled": True,
        "updated": updated,
        "message": msg,
        "sync_stats": sync_stats,
        "today": monitor_today_dict(),
        "yesterday": monitor_yesterday_dict(),
    }


def _auto_sync_enabled() -> bool:
    load_dotenv()
    if not _enabled():
        return False
    raw = (os.getenv("HIBS_PRED_LOG_SYNC_AUTO") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _auto_sync_min_interval_sec() -> int:
    try:
        return max(300, int(os.getenv("HIBS_PRED_LOG_SYNC_AUTO_MIN_SEC", "1800")))
    except ValueError:
        return 1800


def _auto_sync_state_path() -> str:
    from hibs_predictor.cache import default_cache_dir

    return os.path.join(default_cache_dir(), ".pred_log_sync_auto.last")


def pending_settlement_count() -> int:
    """Distinct fixtures with snapshots but no FT result yet."""
    if not os.path.isfile(_db_path()):
        return 0
    init_db()
    try:
        conn = sqlite3.connect(_db_path(), timeout=10)
        try:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT fixture_id) FROM prediction_snapshots
                WHERE result_recorded_at IS NULL OR result_outcome IS NULL OR result_outcome = ''
                """
            ).fetchone()
            return int(row[0] if row else 0)
        finally:
            conn.close()
    except Exception:
        return 0


def maybe_auto_sync_prediction_results(*, force: bool = False) -> Dict[str, Any]:
    """
    Throttled settlement pass after dashboard bundle builds (same as pred-log-sync).

    Does not block predictions or DQ — only fills result columns on existing snapshots.
    """
    if not _auto_sync_enabled():
        return {"ok": True, "skipped": True, "reason": "auto_sync_disabled"}
    state_path = _auto_sync_state_path()
    now_ts = datetime.now(timezone.utc).timestamp()
    if not force and os.path.isfile(state_path):
        try:
            last = float(open(state_path, encoding="utf-8").read().strip())
            if now_ts - last < _auto_sync_min_interval_sec():
                return {"ok": True, "skipped": True, "reason": "throttled", "pending": pending_settlement_count()}
        except Exception:
            pass
    pending = pending_settlement_count()
    if pending == 0 and not force:
        return {"ok": True, "skipped": True, "reason": "no_pending", "pending": 0}
    result = run_pred_log_sync_for_web()
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as fh:
            fh.write(str(now_ts))
    except Exception:
        pass
    updated = int(result.get("updated") or 0)
    if updated:
        print(f"[Prediction log] auto-sync settled {updated} snapshot row(s) ({pending} pending before)")
    result["auto_sync"] = True
    result["pending_before"] = pending
    return result


def recent_logged_results_dict(*, limit: int = 12) -> Dict[str, Any]:
    """Latest FT-settled engine monitor rows for dashboard / API (deduped per fixture)."""
    empty: Dict[str, Any] = {
        "enabled": prediction_log_enabled(),
        "rows": [],
        "summary": {"best_pick": {"wins": 0, "losses": 0, "pending": 0}, "value_pick": {"attempts": 0}},
        "pending_settlement": 0,
        "n_scored_total": 0,
    }
    if not prediction_log_enabled() or not os.path.isfile(_db_path()):
        return empty
    init_db()
    cap = max(1, min(50, int(limit)))
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        n_scored = conn.execute(
            """
            SELECT COUNT(DISTINCT fixture_id) FROM prediction_snapshots
            WHERE result_outcome IS NOT NULL AND result_outcome != ''
            """
        ).fetchone()
        candidates = conn.execute(
            """
            SELECT id, fixture_id, league_code, captured_at, kickoff_iso,
                   home_name, away_name, prediction_json, enrich_summary_json,
                   result_home, result_away, result_outcome, result_status, result_recorded_at
            FROM prediction_snapshots
            WHERE result_outcome IS NOT NULL AND result_outcome != ''
            ORDER BY result_recorded_at DESC, id DESC
            LIMIT ?
            """,
            (cap * 4,),
        ).fetchall()
    finally:
        conn.close()
    seen: set[int] = set()
    rows: List[sqlite3.Row] = []
    for r in candidates:
        fid = int(r["fixture_id"])
        if fid in seen:
            continue
        seen.add(fid)
        rows.append(r)
        if len(rows) >= cap:
            break
    meta, table = _monitor_rows_to_table(rows)
    pending = pending_settlement_count()
    return {
        "enabled": True,
        "rows": table,
        "summary": meta,
        "pending_settlement": pending,
        "n_scored_total": int(n_scored[0] if n_scored else 0),
    }


def prune_old_rows(days: Optional[int] = None) -> int:
    """Delete snapshots older than retain policy. Returns deleted row count."""
    d = days if days is not None else _retain_days()
    init_db()
    path = _db_path()
    conn = sqlite3.connect(path, timeout=20)
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(d))).isoformat()
        cur = conn.execute("DELETE FROM prediction_snapshots WHERE captured_at < ?", (cutoff,))
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def purge_pipeline_excluded_rows(
    *,
    league_codes: Optional[List[str]] = None,
    kickoff_from: Optional[str] = None,
    kickoff_to: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Remove audit rows for leagues excluded from the production pipeline (config/league_profiles.yaml).

    Used to drop Scottish Championship / lower-league backfill before elite cohort replace.
    """
    from hibs_predictor.league_profiles import pipeline_excluded_league_codes

    codes = [str(c).strip().upper() for c in (league_codes or sorted(pipeline_excluded_league_codes())) if str(c).strip()]
    if not codes:
        return {"ok": True, "deleted_total": 0, "by_league": {}, "leagues": []}
    if not _enabled() and not os.path.isfile(_db_path()):
        return {"ok": False, "error": "prediction_log_disabled", "deleted_total": 0, "by_league": {}}

    init_db()
    conn = sqlite3.connect(_db_path(), timeout=20)
    by_league: Dict[str, int] = {}
    try:
        for code in codes:
            params: List[Any] = [code]
            sql = "DELETE FROM prediction_snapshots WHERE UPPER(league_code) = ?"
            if kickoff_from:
                sql += " AND kickoff_iso >= ?"
                params.append(str(kickoff_from))
            if kickoff_to:
                sql += " AND kickoff_iso <= ?"
                params.append(str(kickoff_to) + "T23:59:59")
            cur = conn.execute(sql, params)
            by_league[code] = int(cur.rowcount or 0)
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "deleted_total": sum(by_league.values()),
        "by_league": by_league,
        "leagues": codes,
        "kickoff_from": kickoff_from,
        "kickoff_to": kickoff_to,
        "db_path": _db_path(),
    }


def elite_backfill_league_codes() -> List[str]:
    """Leagues kept in the elite 3-month historic replace cohort."""
    from hibs_predictor.league_profiles import apply_production_pipeline_filter, elite_pitch_league_codes

    elite = elite_pitch_league_codes()
    codes = list(elite) if elite else ["EPL", "SCOTLAND", "CHAMPIONSHIP", "LA_LIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1"]
    if "CHAMPIONSHIP" not in codes:
        codes.insert(2, "CHAMPIONSHIP")
    if "UCL" not in codes:
        codes.extend(["UCL", "EUROPA_LEAGUE", "UECL"])
    return apply_production_pipeline_filter(codes)


def purge_outside_elite_backfill_window(
    *,
    kickoff_from: str,
    kickoff_to: str,
) -> Dict[str, Any]:
    """Delete kickoff-window rows whose league is not in the elite backfill cohort."""
    keep = elite_backfill_league_codes()
    if not keep:
        return {"ok": True, "deleted_total": 0, "kept_leagues": [], "deleted_by_league": {}}
    init_db()
    conn = sqlite3.connect(_db_path(), timeout=20)
    deleted_by_league: Dict[str, int] = {}
    try:
        placeholders = ",".join("?" for _ in keep)
        params: List[Any] = [str(kickoff_from), str(kickoff_to) + "T23:59:59", *keep]
        rows = conn.execute(
            f"""
            SELECT UPPER(league_code) AS lg, COUNT(*) AS n
            FROM prediction_snapshots
            WHERE kickoff_iso >= ? AND kickoff_iso <= ?
              AND UPPER(league_code) NOT IN ({placeholders})
            GROUP BY UPPER(league_code)
            """,
            params,
        ).fetchall()
        for lg, n in rows:
            deleted_by_league[str(lg)] = int(n)
        cur = conn.execute(
            f"""
            DELETE FROM prediction_snapshots
            WHERE kickoff_iso >= ? AND kickoff_iso <= ?
              AND UPPER(league_code) NOT IN ({placeholders})
            """,
            params,
        )
        conn.commit()
        deleted_total = int(cur.rowcount or 0)
    finally:
        conn.close()
    return {
        "ok": True,
        "deleted_total": deleted_total,
        "deleted_by_league": deleted_by_league,
        "kept_leagues": keep,
        "kickoff_from": kickoff_from,
        "kickoff_to": kickoff_to,
        "db_path": _db_path(),
    }


def replace_elite_backfill_cohort(
    *,
    kickoff_from: str = "2026-02-01",
    kickoff_to: str = "2026-05-31",
) -> Dict[str, Any]:
    """Drop pipeline-excluded + non-elite window rows; elite historic pass repopulates."""
    excluded = purge_pipeline_excluded_rows()
    window = purge_outside_elite_backfill_window(kickoff_from=kickoff_from, kickoff_to=kickoff_to)
    return {
        "ok": True,
        "kickoff_from": kickoff_from,
        "kickoff_to": kickoff_to,
        "pipeline_excluded_purge": excluded,
        "non_elite_window_purge": window,
        "deleted_total": int(excluded.get("deleted_total") or 0) + int(window.get("deleted_total") or 0),
        "kept_leagues": window.get("kept_leagues") or [],
    }


def _safe_prob(p: Any) -> float:
    try:
        x = float(p)
    except (TypeError, ValueError):
        return 1.0 / 3.0
    return max(1e-6, min(1.0 - 1e-6, x))


def _rows_with_results(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(
        conn.execute(
            """
            SELECT id, prediction_json, result_outcome, data_quality_pct,
                   result_home, result_away, result_status
            FROM prediction_snapshots
            WHERE result_outcome IS NOT NULL AND result_outcome != ''
            """
        ).fetchall()
    )


def _monitor_days() -> int:
    try:
        return max(1, int(os.getenv("HIBS_MONITOR_DAYS", "28")))
    except ValueError:
        return 28


def _monitor_cutoff_iso(*, days: Optional[int] = None) -> str:
    d = days if days is not None else _monitor_days()
    return (datetime.now(timezone.utc) - timedelta(days=int(d))).isoformat()


def _audit_pipeline_cron_markers() -> Tuple[str, ...]:
    return (
        "run_daily_audit_pipeline",
        "hibs-bet: daily bundle",
        "pred-log-sync",
    )


def _crontab_has_audit_pipeline(body: str) -> bool:
    return any(m in (body or "") for m in _audit_pipeline_cron_markers())


def pred_log_sync_cron_status() -> Dict[str, Any]:
    """Whether daily audit pipeline appears scheduled (crontab) and log freshness."""
    log_dir = (os.getenv("HIBS_LOG_DIR") or "/var/log/hibs-bet").strip()
    default_log = (os.getenv("HIBS_PRED_LOG_SYNC_LOG") or f"{log_dir}/daily-audit-am.log").strip()
    legacy_log = f"{log_dir}/pred-log-sync.log"
    scheduled = False
    cron_user = ""
    try:
        import subprocess

        for user in ("www-data", "root"):
            proc = subprocess.run(
                ["crontab", "-u", user, "-l"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            body = proc.stdout or ""
            if proc.returncode == 0 and _crontab_has_audit_pipeline(body):
                scheduled = True
                cron_user = user
                break
    except Exception:
        pass

    log_path = default_log
    if not os.path.isfile(log_path) and os.path.isfile(legacy_log):
        log_path = legacy_log
    log_exists = os.path.isfile(log_path)
    log_age_hours: Optional[float] = None
    if log_exists:
        log_age_hours = round((datetime.now(timezone.utc).timestamp() - os.path.getmtime(log_path)) / 3600.0, 1)

    needs_reminder = prediction_log_enabled() and not scheduled
    message = ""
    if needs_reminder:
        message = (
            "Install daily audit pipeline: sudo bash /opt/hibs-bet/deploy/cron-hibs-calibration.sh --install"
        )
    elif scheduled and log_exists and log_age_hours is not None and log_age_hours > 48:
        message = f"Cron present but log stale ({log_age_hours}h) — check {log_path}"

    return {
        "scheduled": scheduled,
        "cron_user": cron_user or None,
        "log_path": log_path,
        "legacy_log_path": legacy_log,
        "log_exists": log_exists,
        "log_age_hours": log_age_hours,
        "needs_reminder": needs_reminder,
        "message": message,
    }


def _day_bounds_datetimes(day_offset: int = 0) -> Tuple[datetime, datetime, str, str]:
    """Start/end UTC for a display-TZ calendar day (0=today, -1=yesterday), plus local date and label."""
    from hibs_predictor.display_tz import display_timezone, display_tz_label, local_today

    target = local_today() + timedelta(days=int(day_offset))
    tz = display_timezone()
    start_local = datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=tz)
    end_local = datetime(target.year, target.month, target.day, 23, 59, 59, tzinfo=tz)
    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
        target.isoformat(),
        display_tz_label(),
    )


def _today_bounds_datetimes() -> Tuple[datetime, datetime, str, str]:
    """Start/end UTC for the display-TZ calendar day, plus local date and label."""
    return _day_bounds_datetimes(0)


def _yesterday_bounds_datetimes() -> Tuple[datetime, datetime, str, str]:
    """Start/end UTC for yesterday in the display timezone."""
    return _day_bounds_datetimes(-1)


def _today_bounds_utc() -> Tuple[str, str, str, str]:
    """Start/end UTC ISO for the display-TZ calendar day, plus local date and label."""
    start, end, date_local, tz_label = _today_bounds_datetimes()
    return start.isoformat(), end.isoformat(), date_local, tz_label


def _kickoff_in_bounds(kickoff_raw: str, start: datetime, end: datetime) -> bool:
    ko = _parse_kickoff_iso(kickoff_raw)
    if not ko:
        return False
    return start <= ko <= end


def _rows_kickoff_today(
    conn: sqlite3.Connection,
    *,
    start_dt: datetime,
    end_dt: datetime,
) -> List[sqlite3.Row]:
    """Latest snapshot per fixture whose kick-off falls in today's display-TZ window."""
    conn.row_factory = sqlite3.Row
    buf_lo = (start_dt - timedelta(hours=24)).isoformat()
    buf_hi = (end_dt + timedelta(hours=24)).isoformat()
    candidates = conn.execute(
        """
        SELECT id, fixture_id, league_code, captured_at, kickoff_iso,
               home_name, away_name, prediction_json, enrich_summary_json,
               result_home, result_away, result_outcome, result_status, result_recorded_at
        FROM prediction_snapshots
        WHERE kickoff_iso IS NOT NULL AND kickoff_iso != ''
          AND kickoff_iso >= ? AND kickoff_iso <= ?
        ORDER BY captured_at DESC
        """,
        (buf_lo, buf_hi),
    ).fetchall()
    seen: set[int] = set()
    out: List[sqlite3.Row] = []
    for r in candidates:
        fid = int(r["fixture_id"])
        if fid in seen:
            continue
        if not _kickoff_in_bounds(str(r["kickoff_iso"] or ""), start_dt, end_dt):
            continue
        seen.add(fid)
        out.append(r)
    return out


def _rows_scored_ft_today(
    conn: sqlite3.Connection,
    *,
    start_iso: str,
    end_iso: str,
) -> List[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(
        conn.execute(
            """
            SELECT id FROM prediction_snapshots
            WHERE result_recorded_at >= ? AND result_recorded_at <= ?
              AND result_outcome IS NOT NULL AND result_outcome != ''
            """,
            (start_iso, end_iso),
        ).fetchall()
    )


def _rows_result_recorded_in_day(
    conn: sqlite3.Connection,
    *,
    start_iso: str,
    end_iso: str,
) -> List[sqlite3.Row]:
    """Latest snapshot per fixture whose FT result was recorded in the display-TZ day window."""
    conn.row_factory = sqlite3.Row
    candidates = conn.execute(
        """
        SELECT id, fixture_id, league_code, captured_at, kickoff_iso,
               home_name, away_name, prediction_json, enrich_summary_json,
               result_home, result_away, result_outcome, result_status, result_recorded_at
        FROM prediction_snapshots
        WHERE result_recorded_at IS NOT NULL AND result_recorded_at != ''
          AND result_recorded_at >= ? AND result_recorded_at <= ?
          AND result_outcome IS NOT NULL AND result_outcome != ''
        ORDER BY result_recorded_at DESC
        """,
        (start_iso, end_iso),
    ).fetchall()
    seen: set[int] = set()
    out: List[sqlite3.Row] = []
    for r in candidates:
        fid = int(r["fixture_id"])
        if fid in seen:
            continue
        seen.add(fid)
        out.append(r)
    return out


def log_predictions_from_fixtures(
    fixtures: List[Dict[str, Any]],
    *,
    max_rows: Optional[int] = None,
    require_odds: Optional[bool] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Automated audit logging after each fixture bundle build.

    With HIBS_PREDICTION_LOG_ALWAYS=1 (default): log every row with a prediction, respecting
    HIBS_PREDICTION_LOG_MIN_INTERVAL_SEC per fixture (0 = new row on every bundle pass).
    Otherwise only backfill fixtures missing any snapshot row.

    When ``require_odds`` is true (default from HIBS_AUDIT_REQUIRE_ODDS), fixtures still
    missing 1X2 after ``ensure_snapshot_odds`` are skipped (daily cron uses this).
    """
    if not _enabled() or not fixtures:
        if stats is not None:
            stats.update({"logged": 0, "skipped_no_odds": 0, "odds_fetched_on_retry": 0, "considered": 0})
        return 0
    must_have_odds = audit_require_odds_for_batch() if require_odds is None else bool(require_odds)
    cap = max_rows if max_rows is not None else _auto_log_max_fixtures()
    if not _always_log():
        n = backfill_snapshots_from_fixtures(
            fixtures,
            max_rows=min(cap, 80),
            require_odds=must_have_odds,
            stats=stats,
        )
        return n

    logged = 0
    skipped_no_odds = 0
    odds_fetched = 0
    considered = 0
    force_each_pass = _min_interval_sec() <= 0
    for fixture in fixtures:
        if logged >= cap:
            break
        pred = fixture.get("prediction")
        if not isinstance(pred, dict) or pred.get("prediction_unavailable"):
            continue
        if not _fixture_id(fixture):
            continue
        considered += 1
        fx = dict(fixture)
        pr = _shallow_prediction_copy(pred)
        meta = ensure_snapshot_odds(fx, pr)
        if meta.get("source") == "fetch_bundle":
            odds_fetched += 1
        if must_have_odds and not prediction_has_stored_1x2(pr):
            skipped_no_odds += 1
            continue
        maybe_log_prediction_snapshot(fx, pr, skip_interval=force_each_pass, odds_meta=meta)
        logged += 1
    if stats is not None:
        stats.update(
            {
                "logged": logged,
                "skipped_no_odds": skipped_no_odds,
                "odds_fetched_on_retry": odds_fetched,
                "considered": considered,
                "require_odds": must_have_odds,
            }
        )
    return logged


def backfill_snapshots_from_fixtures(
    fixtures: List[Dict[str, Any]],
    *,
    max_rows: int = 80,
    require_odds: bool = False,
    stats: Optional[Dict[str, Any]] = None,
) -> int:
    """Log snapshots for fixtures that already have predictions but no audit row yet (best-effort)."""
    if not _enabled() or not fixtures:
        if stats is not None:
            stats.update({"logged": 0, "skipped_no_odds": 0, "odds_fetched_on_retry": 0, "considered": 0})
        return 0
    try:
        init_db()
        conn = sqlite3.connect(_db_path(), timeout=15)
        try:
            existing = {
                int(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT fixture_id FROM prediction_snapshots"
                ).fetchall()
            }
        finally:
            conn.close()
    except Exception:
        return 0
    logged = 0
    skipped_no_odds = 0
    odds_fetched = 0
    considered = 0
    for fixture in fixtures:
        if logged >= max_rows:
            break
        pred = fixture.get("prediction")
        if not isinstance(pred, dict) or pred.get("prediction_unavailable"):
            continue
        fid = _fixture_id(fixture)
        if not fid or fid in existing:
            continue
        considered += 1
        fx = dict(fixture)
        pr = _shallow_prediction_copy(pred)
        meta = ensure_snapshot_odds(fx, pr)
        if meta.get("source") == "fetch_bundle":
            odds_fetched += 1
        if require_odds and not prediction_has_stored_1x2(pr):
            skipped_no_odds += 1
            continue
        maybe_log_prediction_snapshot(fx, pr, skip_interval=True, odds_meta=meta)
        existing.add(fid)
        logged += 1
    if stats is not None:
        stats.update(
            {
                "logged": logged,
                "skipped_no_odds": skipped_no_odds,
                "odds_fetched_on_retry": odds_fetched,
                "considered": considered,
                "require_odds": require_odds,
            }
        )
    return logged


def audit_odds_capture_stats(*, days: int = 28, since_iso: str | None = None) -> Dict[str, Any]:
    """Share of recent audit snapshots with stored 1X2 odds (value replay / CLV readiness)."""
    empty: Dict[str, Any] = {
        "window_days": int(days),
        "since_iso": since_iso,
        "n_snapshots": 0,
        "n_with_1x2_odds": 0,
        "capture_rate_pct": None,
        "n_scored": 0,
        "n_scored_with_1x2": 0,
        "scored_capture_rate_pct": None,
    }
    if not _enabled() or not os.path.isfile(_db_path()):
        empty["message"] = "Prediction audit log disabled or missing."
        return empty
    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
    if since_iso and since_iso > cutoff:
        cutoff = since_iso
    conn = sqlite3.connect(_db_path(), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT prediction_json, result_outcome
            FROM prediction_snapshots
            WHERE captured_at >= ?
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        empty["message"] = f"No snapshots in last {days}d."
        return empty

    n_with = 0
    n_scored = 0
    n_scored_with = 0
    for r in rows:
        try:
            pred = json.loads(r["prediction_json"] or "{}")
        except Exception:
            pred = {}
        has = prediction_has_stored_1x2(pred) if isinstance(pred, dict) else False
        if has:
            n_with += 1
        if r["result_outcome"]:
            n_scored += 1
            if has:
                n_scored_with += 1
    n = len(rows)
    out = {
        "window_days": int(days),
        "since_iso": since_iso,
        "cutoff_iso": cutoff,
        "n_snapshots": n,
        "n_with_1x2_odds": n_with,
        "capture_rate_pct": round(100.0 * n_with / n, 2) if n else None,
        "n_scored": n_scored,
        "n_scored_with_1x2": n_scored_with,
        "scored_capture_rate_pct": round(100.0 * n_scored_with / n_scored, 2) if n_scored else None,
    }
    if n_with < max(3, int(0.5 * n)):
        out["message"] = (
            f"Only {n_with}/{n} recent snapshots have 1X2 odds — value replay and CLV gates need forward capture."
        )
    return out


def wilson_score_interval(
    successes: int, n: int, *, z: float = 1.96
) -> Tuple[Optional[float], Optional[float]]:
    """Wilson score 95% interval for a binomial proportion (percent scale)."""
    if n <= 0:
        return None, None
    p = float(successes) / float(n)
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = z * math.sqrt((p * (1.0 - p) / n) + (z2 / (4.0 * n * n))) / denom
    low = max(0.0, center - margin)
    high = min(1.0, center + margin)
    return round(low * 100.0, 2), round(high * 100.0, 2)


def _median_clv_pp(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid], 2)
    return round((ordered[mid - 1] + ordered[mid]) / 2.0, 2)


def clv_beat_close_summary(
    *,
    days: int = 28,
    since_iso: str | None = None,
    trial_leagues_only: bool = False,
    value_only: bool = False,
) -> Dict[str, Any]:
    """Rolling CLV beat-close headline for /api/health (settled rows, kickoff in window)."""
    out: Dict[str, Any] = {
        "enabled": _clv_enabled(),
        "window_days": int(days),
        "since_iso": since_iso,
        "value_only": bool(value_only),
        "n_clv_rows": 0,
        "beat_close_pct": None,
        "avg_clv_pp": None,
        "median_clv_pp": None,
        "beat_close_wilson_ci_95": None,
        "trial_leagues_only": bool(trial_leagues_only),
        "avg_edge_clv_pct": None,
        "mu_clv": None,
        "beat_close_fair_pct": None,
    }
    if not _clv_enabled() or not os.path.isfile(_db_path()):
        out["message"] = "CLV logging off or no audit DB."
        return out
    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
    if since_iso and since_iso > cutoff:
        cutoff = since_iso
    league_filter = ""
    params: list[Any] = [cutoff]
    if trial_leagues_only:
        from hibs_predictor.institutional_readiness import _TRIAL_VALUE_LEAGUES

        trial = sorted(_TRIAL_VALUE_LEAGUES - {"WORLD_CUP", "INTL_FRIENDLIES"})
        if trial:
            placeholders = ",".join("?" * len(trial))
            league_filter = f" AND league_code IN ({placeholders})"
            params.extend(trial)
    conn = sqlite3.connect(_db_path(), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            SELECT enrich_summary_json, prediction_json
            FROM prediction_snapshots
            WHERE result_outcome IS NOT NULL AND result_outcome != ''
              AND kickoff_iso >= ?
              {league_filter}
            """,
            tuple(params),
        ).fetchall()
    finally:
        conn.close()
    n = 0
    beat = 0
    beat_fair = 0
    pp_sum = 0.0
    pp_values: List[float] = []
    edge_sum = 0.0
    edge_n = 0
    mu_sum = 0.0
    mu_n = 0
    for r in rows:
        if value_only:
            try:
                pred = json.loads(r["prediction_json"] or "")
            except Exception:
                pred = {}
            if not isinstance(pred, dict) or not _value_pick_snapshot(pred):
                continue
        try:
            enrich = json.loads(r["enrich_summary_json"] or "")
        except Exception:
            continue
        clv = enrich.get("clv") if isinstance(enrich, dict) else None
        if not isinstance(clv, dict) or clv.get("clv_pp") is None:
            continue
        try:
            pp_f = float(clv["clv_pp"])
        except (TypeError, ValueError):
            continue
        n += 1
        pp_sum += pp_f
        pp_values.append(pp_f)
        if pp_f > 0:
            beat += 1
        try:
            edge_f = clv.get("edge_clv_pct")
            if edge_f is not None:
                edge_f = float(edge_f)
                edge_sum += edge_f
                edge_n += 1
                if edge_f > 0:
                    beat_fair += 1
        except (TypeError, ValueError):
            pass
        try:
            mu_log = clv.get("mu_clv_log")
            if mu_log is not None:
                mu_sum += float(mu_log)
                mu_n += 1
        except (TypeError, ValueError):
            pass
    out["n_clv_rows"] = n
    if n:
        out["beat_close_pct"] = round(100.0 * beat / n, 2)
        out["avg_clv_pp"] = round(pp_sum / n, 2)
        out["median_clv_pp"] = _median_clv_pp(pp_values)
        lo, hi = wilson_score_interval(beat, n)
        if lo is not None and hi is not None:
            out["beat_close_wilson_ci_95"] = {"low_pct": lo, "high_pct": hi}
        if edge_n:
            out["avg_edge_clv_pct"] = round(edge_sum / edge_n, 3)
            out["beat_close_fair_pct"] = round(100.0 * beat_fair / edge_n, 2)
        if mu_n:
            out["mu_clv"] = round(mu_sum / mu_n, 5)
    else:
        out["message"] = (
            f"No settled CLV rows in last {days}d — run daily sync after matches; "
            "ensure snapshots capture opening 1X2."
        )
    return out


# value_bets keys (engine) → acca market keys for FT settlement
_VALUE_BET_TO_MARKET_KEY: Dict[str, str] = {
    "home": "home_win",
    "away": "away_win",
    "draw": "draw",
    "btts_yes": "btts_yes",
    "btts_no": "btts_no",
    "over15": "over_15",
    "under15": "under_15",
    "over25": "over_25",
    "under25": "under_25",
    "over35": "over_35",
    "under35": "under_35",
}


def _snapshot_has_value(pred: Dict[str, Any]) -> bool:
    """True when the logged snapshot flagged at least one value bet."""
    if pred.get("has_any_value"):
        return True
    return bool(pred.get("value_bets")) or bool(pred.get("value_bets_alt"))


def _value_bet_row(pred: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    vb = pred.get("value_bets") or {}
    row = vb.get(key) if isinstance(vb, dict) else None
    if isinstance(row, dict):
        return row
    alt = pred.get("value_bets_alt") or {}
    row = alt.get(key) if isinstance(alt, dict) else None
    return row if isinstance(row, dict) else None


def _value_pick_snapshot(pred: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Top logged value leg (best_bet ROI) with display + settlement keys."""
    if not _snapshot_has_value(pred):
        return None
    key = pred.get("best_bet")
    if not key:
        merged: Dict[str, Any] = {}
        for src in (pred.get("value_bets"), pred.get("value_bets_alt")):
            if isinstance(src, dict):
                merged.update(src)
        if not merged:
            return None
        key = max(
            merged.keys(),
            key=lambda k: float((merged.get(k) or {}).get("roi_percent") or 0),
        )
    row = _value_bet_row(pred, str(key))
    if not row:
        return None
    market_key = str(key)
    settle_key = _VALUE_BET_TO_MARKET_KEY.get(market_key)
    label = row.get("market_label") or market_key.replace("_", " ").title()
    model_pct = row.get("model_probability_pct")
    if model_pct is None:
        try:
            model_pct = round(float(row.get("model_probability") or 0) * 100.0, 1)
        except (TypeError, ValueError):
            model_pct = None
    edge = row.get("edge_pct")
    if edge is None:
        edge = row.get("roi_percent")
    odds = row.get("odds")
    try:
        odds_f = round(float(odds), 2) if odds is not None else None
    except (TypeError, ValueError):
        odds_f = None
    try:
        edge_f = round(float(edge), 1) if edge is not None else None
    except (TypeError, ValueError):
        edge_f = None
    return {
        "market_key": market_key,
        "market_label": label,
        "settle_key": settle_key,
        "model_pct": model_pct,
        "odds": odds_f,
        "edge_pct": edge_f,
    }


def _row_result_packet(row: sqlite3.Row) -> Dict[str, Any]:
    status = str(row["result_status"] or "").upper()
    ft = status == "FT" or (
        row["result_home"] is not None
        and row["result_away"] is not None
        and bool(row["result_outcome"])
    )
    return {
        "fixture_status": "FT" if ft else "NS",
        "live_score_home": row["result_home"],
        "live_score_away": row["result_away"],
    }


def _value_pick_result_label(row: sqlite3.Row, pred: Dict[str, Any]) -> Optional[str]:
    """W / L / pending for the logged value leg; None when snapshot had no value flag."""
    snap = _value_pick_snapshot(pred)
    if not snap:
        return None
    settle_key = snap.get("settle_key")
    if not settle_key:
        return "pending"
    from hibs_predictor.acca_recommender import market_leg_result_label

    return market_leg_result_label(_row_result_packet(row), settle_key)


def _value_pick_tally(rows: List[sqlite3.Row]) -> Dict[str, Any]:
    wins = losses = pending = attempts = 0
    for r in rows:
        try:
            pred = json.loads(r["prediction_json"])
        except Exception:
            continue
        if not isinstance(pred, dict) or not _value_pick_snapshot(pred):
            continue
        attempts += 1
        label = _value_pick_result_label(r, pred)
        if label == "W":
            wins += 1
        elif label == "L":
            losses += 1
        else:
            pending += 1
    settled = wins + losses
    out: Dict[str, Any] = {
        "attempts": attempts,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "settled": settled,
    }
    if settled:
        out["hit_rate_pct"] = round(100.0 * wins / settled, 2)
    return out


def _best_pick_result_label(
    pred: Dict[str, Any],
    *,
    outcome: Optional[str],
    status: Optional[str],
) -> str:
    pick = (pred.get("predicted_outcome") or "").lower()
    if pick not in ("home", "draw", "away"):
        return "pending"
    oc = (outcome or "").lower()
    if not oc or (status and str(status).upper() != "FT"):
        return "pending"
    return "W" if pick == oc else "L"


def _model_pct_for_pick(pred: Dict[str, Any]) -> Optional[float]:
    pick = (pred.get("predicted_outcome") or "").lower()
    probs = pred.get("probabilities") or {}
    if pick not in ("home", "draw", "away"):
        return None
    return round(_safe_prob(probs.get(pick)) * 100.0, 1)


def _format_score(home: Any, away: Any) -> Optional[str]:
    if home is None or away is None:
        return None
    try:
        return f"{int(home)}-{int(away)}"
    except (TypeError, ValueError):
        return None


def _clv_pp_from_enrich(enrich_raw: Any) -> Optional[float]:
    try:
        enrich = json.loads(enrich_raw or "")
    except Exception:
        return None
    if not isinstance(enrich, dict):
        return None
    clv = enrich.get("clv")
    if not isinstance(clv, dict) or clv.get("clv_pp") is None:
        return None
    try:
        return round(float(clv["clv_pp"]), 2)
    except (TypeError, ValueError):
        return None


def _monitor_rows_to_table(rows: List[sqlite3.Row]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    wins = losses = pending = 0
    table_rows: List[Dict[str, Any]] = []
    for r in rows:
        fid = int(r["fixture_id"])
        try:
            pred = json.loads(r["prediction_json"])
        except Exception:
            pred = {}
        if not isinstance(pred, dict):
            pred = {}
        pick = (pred.get("predicted_outcome") or "").lower()
        label = _best_pick_result_label(
            pred,
            outcome=r["result_outcome"],
            status=r["result_status"],
        )
        if pick in ("home", "draw", "away"):
            if label == "W":
                wins += 1
            elif label == "L":
                losses += 1
            else:
                pending += 1
        value_snap = _value_pick_snapshot(pred)
        value_label = _value_pick_result_label(r, pred) if value_snap else None
        home_nm = str(r["home_name"] or pred.get("home") or "?")
        away_nm = str(r["away_name"] or pred.get("away") or "?")
        table_rows.append(
            {
                "fixture_id": fid,
                "match": f"{home_nm} v {away_nm}",
                "league": str(r["league_code"] or ""),
                "kickoff_iso": r["kickoff_iso"] or "",
                "pick": pick if pick in ("home", "draw", "away") else None,
                "model_pct": _model_pct_for_pick(pred),
                "result": label,
                "score": _format_score(r["result_home"], r["result_away"]),
                "clv_pp": _clv_pp_from_enrich(r["enrich_summary_json"]),
                "has_value": bool(value_snap),
                "value_market": (value_snap or {}).get("market_label"),
                "value_model_pct": (value_snap or {}).get("model_pct"),
                "value_odds": (value_snap or {}).get("odds"),
                "value_edge_pct": (value_snap or {}).get("edge_pct"),
                "value_result": value_label,
            }
        )
    table_rows.sort(
        key=lambda r: (
            0 if r.get("result") == "W" else (2 if r.get("result") == "L" else 1),
            (r.get("match") or "").lower(),
        )
    )
    meta = {
        "best_pick": {"wins": wins, "losses": losses, "pending": pending},
        "value_pick": _value_pick_tally(rows),
    }
    vp = meta["value_pick"]
    if vp.get("hit_rate_pct") is not None:
        meta["value_hit_rate_pct"] = vp["hit_rate_pct"]
    return meta, table_rows


def _monitor_day_dict(
    *,
    day_offset: int,
    empty_label: str,
    window_mode: str = "kickoff",
) -> Dict[str, Any]:
    """One calendar slice: kickoff window or FT results recorded that day (display TZ)."""
    start_dt, end_dt, date_local, tz_label = _day_bounds_datetimes(day_offset)
    start_iso, end_iso = start_dt.isoformat(), end_dt.isoformat()
    from hibs_predictor.display_tz import display_timezone

    tz_key = getattr(display_timezone(), "key", "UTC")
    mode = "scored" if window_mode == "scored" else "kickoff"
    out: Dict[str, Any] = {
        "ok": True,
        "enabled": prediction_log_enabled(),
        "section": mode,
        "date_local": date_local,
        "display_tz": tz_key,
        "display_tz_label": tz_label,
        "window_start_utc": start_iso[:19],
        "window_end_utc": end_iso[:19],
        "n_logged": 0,
        "n_scored_ft": 0,
        "best_pick": {"wins": 0, "losses": 0, "pending": 0},
        "rows": [],
    }
    if not prediction_log_enabled():
        out["message"] = (
            "Model monitor off — prediction log disabled. Set HIBS_PREDICTION_LOG_ENABLED=1."
        )
        return out
    if not os.path.isfile(_db_path()):
        out["message"] = (
            "Model monitor waiting for audit DB — set HIBS_PREDICTION_LOG_ENABLED=1 and use the dashboard."
        )
        return out

    init_db()
    conn = sqlite3.connect(_db_path(), timeout=20)
    try:
        if mode == "scored":
            day_rows = _rows_result_recorded_in_day(conn, start_iso=start_iso, end_iso=end_iso)
            scored_ft = day_rows
        else:
            day_rows = _rows_kickoff_today(conn, start_dt=start_dt, end_dt=end_dt)
            scored_ft = _rows_scored_ft_today(conn, start_iso=start_iso, end_iso=end_iso)
    finally:
        conn.close()

    out["n_logged"] = len(day_rows)
    out["n_scored_ft"] = len(scored_ft)
    table_meta, table_rows = _monitor_rows_to_table(day_rows)
    out["best_pick"] = table_meta.get("best_pick") or {"wins": 0, "losses": 0, "pending": 0}
    out["value_pick"] = table_meta.get("value_pick") or {
        "attempts": 0,
        "wins": 0,
        "losses": 0,
        "pending": 0,
        "settled": 0,
    }
    if table_meta.get("value_hit_rate_pct") is not None:
        out["value_hit_rate_pct"] = table_meta["value_hit_rate_pct"]
    out["rows"] = table_rows
    if not day_rows:
        if mode == "scored":
            out["message"] = (
                f"No FT results recorded {empty_label} ({date_local}) — run pred-log-sync after matches finish."
            )
        else:
            out["message"] = (
                f"No fixtures kicking off {empty_label} ({date_local}) — load the dashboard before kick-off to log predictions."
            )
    return out


def _monitor_combined_day(*, day_offset: int, empty_label: str) -> Dict[str, Any]:
    """Kickoff-day and results-recorded-day slices for templates/API."""
    kickoff = _monitor_day_dict(day_offset=day_offset, empty_label=empty_label, window_mode="kickoff")
    scored = _monitor_day_dict(day_offset=day_offset, empty_label=empty_label, window_mode="scored")
    return {
        "ok": kickoff.get("ok", True),
        "enabled": kickoff.get("enabled"),
        "date_local": kickoff.get("date_local"),
        "display_tz": kickoff.get("display_tz"),
        "display_tz_label": kickoff.get("display_tz_label"),
        "window_start_utc": kickoff.get("window_start_utc"),
        "window_end_utc": kickoff.get("window_end_utc"),
        "kickoff": kickoff,
        "scored": scored,
        "n_logged": kickoff.get("n_logged", 0),
        "n_scored_ft": kickoff.get("n_scored_ft", 0),
        "best_pick": kickoff.get("best_pick") or {"wins": 0, "losses": 0, "pending": 0},
        "value_pick": kickoff.get("value_pick")
        or {"attempts": 0, "wins": 0, "losses": 0, "pending": 0, "settled": 0},
        "value_hit_rate_pct": kickoff.get("value_hit_rate_pct"),
        "rows": kickoff.get("rows") or [],
        "message": kickoff.get("message"),
    }


def monitor_today_dict() -> Dict[str, Any]:
    """Today in display timezone: kickoff window + results recorded today."""
    return _monitor_combined_day(day_offset=0, empty_label="today")


def monitor_yesterday_dict() -> Dict[str, Any]:
    """Yesterday in display timezone: kickoff window + results recorded yesterday."""
    return _monitor_combined_day(day_offset=-1, empty_label="yesterday")


def _rows_in_monitor_window(
    conn: sqlite3.Connection,
    *,
    days: Optional[int] = None,
    scored_only: bool = False,
) -> List[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cutoff = _monitor_cutoff_iso(days=days)
    sql = """
        SELECT id, league_code, prediction_json, result_outcome, data_quality_pct,
               enrich_summary_json, captured_at
        FROM prediction_snapshots
        WHERE captured_at >= ?
    """
    if scored_only:
        sql += " AND result_outcome IS NOT NULL AND result_outcome != ''"
    sql += " ORDER BY captured_at"
    return list(conn.execute(sql, (cutoff,)).fetchall())


def monitor_summary_dict(*, days: Optional[int] = None) -> Dict[str, Any]:
    """Rolling-window prediction vs outcome metrics (default HIBS_MONITOR_DAYS=28)."""
    window_days = days if days is not None else _monitor_days()
    cutoff = _monitor_cutoff_iso(days=window_days)
    enabled = prediction_log_enabled()
    yesterday = monitor_yesterday_dict()
    today = monitor_today_dict()
    base: Dict[str, Any] = {
        "ok": True,
        "enabled": enabled,
        "window_days": window_days,
        "window_start_utc": cutoff[:10],
        "db_path": _db_path(),
        "prediction_log_enabled": enabled,
        "clv_log_enabled": _clv_enabled(),
        "pred_log_sync_cron": pred_log_sync_cron_status(),
        "yesterday": yesterday,
        "today": today,
    }
    if not enabled:
        base.update(
            {
                "ok": False,
                "n_logged": 0,
                "n_scored": 0,
                "message": (
                    "Model monitor off — set HIBS_PREDICTION_LOG_ENABLED=1 "
                    "(monitor follows the prediction log)."
                ),
                "by_league": [],
            }
        )
        return base
    if not os.path.isfile(_db_path()):
        base.update(
            {
                "ok": False,
                "error": "no_database",
                "n_logged": 0,
                "n_scored": 0,
                "message": "No audit database yet — enable HIBS_PREDICTION_LOG_ENABLED=1 and use the dashboard.",
            }
        )
        return base

    init_db()
    conn = sqlite3.connect(_db_path(), timeout=20)
    try:
        rows_all = _rows_in_monitor_window(conn, days=window_days, scored_only=False)
        rows_scored = _rows_in_monitor_window(conn, days=window_days, scored_only=True)
    finally:
        conn.close()

    n_logged = len(rows_all)
    n_scored = len(rows_scored)
    base["n_logged"] = n_logged
    base["n_scored"] = n_scored

    if n_scored == 0:
        base["message"] = (
            "No scored rows in window yet — run pred-log-sync after matches finish."
            if n_logged
            else "No snapshots in window — predictions accumulate when the dashboard runs."
        )
        base["by_league"] = []
        return base

    brier_sum = 0.0
    logloss_sum = 0.0
    pick_correct = 0
    pick_attempts = 0
    n_metrics = 0
    clv_n = 0
    clv_beat = 0
    clv_pp_sum = 0.0
    by_league: Dict[str, Dict[str, Any]] = {}

    for r in rows_scored:
        try:
            pred = json.loads(r["prediction_json"])
        except Exception:
            continue
        probs = pred.get("probabilities") or {}
        ph = _safe_prob(probs.get("home"))
        pd = _safe_prob(probs.get("draw"))
        pa = _safe_prob(probs.get("away"))
        out = (r["result_outcome"] or "").lower()
        if out not in ("home", "draw", "away"):
            continue
        yh, yd, ya = (1.0, 0.0, 0.0) if out == "home" else ((0.0, 1.0, 0.0) if out == "draw" else (0.0, 0.0, 1.0))
        brier = (ph - yh) ** 2 + (pd - yd) ** 2 + (pa - ya) ** 2
        p_correct = ph if out == "home" else (pd if out == "draw" else pa)
        brier_sum += brier
        logloss_sum += -math.log(p_correct)
        n_metrics += 1

        pick = (pred.get("predicted_outcome") or "").lower()
        if pick in ("home", "draw", "away"):
            pick_attempts += 1
            if pick == out:
                pick_correct += 1

        lg = str(r["league_code"] or "unknown")
        bucket = by_league.setdefault(
            lg,
            {"league": lg, "n_scored": 0, "brier_sum": 0.0, "pick_correct": 0, "pick_attempts": 0, "clv_n": 0, "clv_beat": 0},
        )
        bucket["n_scored"] += 1
        bucket["brier_sum"] += brier
        if pick in ("home", "draw", "away"):
            bucket["pick_attempts"] += 1
            if pick == out:
                bucket["pick_correct"] += 1

        try:
            enrich = json.loads(r["enrich_summary_json"] or "")
        except Exception:
            enrich = {}
        clv = enrich.get("clv") if isinstance(enrich, dict) else None
        if isinstance(clv, dict) and clv.get("clv_pp") is not None:
            try:
                pp_f = float(clv["clv_pp"])
            except (TypeError, ValueError):
                pp_f = None
            if pp_f is not None:
                clv_n += 1
                clv_pp_sum += pp_f
                bucket["clv_n"] += 1
                if pp_f > 0:
                    clv_beat += 1
                    bucket["clv_beat"] += 1

    if n_metrics == 0:
        base["message"] = "Scored rows in window lack parseable 1X2 results."
        base["by_league"] = []
        return base

    n_eff = max(1, n_metrics)
    base.update(
        {
            "n_used_metrics": n_metrics,
            "brier_score_1x2": round(brier_sum / n_eff, 5),
            "log_loss_1x2": round(logloss_sum / n_eff, 5),
            "best_pick_accuracy_pct": round(100.0 * pick_correct / pick_attempts, 2) if pick_attempts else None,
            "best_pick_n": pick_attempts,
            "best_pick_correct": pick_correct,
        }
    )
    if clv_n:
        base["clv_n"] = clv_n
        base["beat_close_pct"] = round(100.0 * clv_beat / clv_n, 2)
        base["avg_clv_pp"] = round(clv_pp_sum / clv_n, 2)

    league_rows: List[Dict[str, Any]] = []
    for lg, b in sorted(by_league.items(), key=lambda x: -x[1]["n_scored"]):
        ns = int(b["n_scored"])
        pa = int(b["pick_attempts"])
        cn = int(b["clv_n"])
        row: Dict[str, Any] = {
            "league": lg,
            "n_scored": ns,
            "brier": round(b["brier_sum"] / ns, 5) if ns else None,
            "best_pick_accuracy_pct": round(100.0 * b["pick_correct"] / pa, 2) if pa else None,
        }
        if cn:
            row["clv_n"] = cn
            row["beat_close_pct"] = round(100.0 * b["clv_beat"] / cn, 2)
        league_rows.append(row)
    base["by_league"] = league_rows
    return base


def _kickoff_window_cutoff_iso(*, days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()


def _dedupe_latest_pre_kickoff(rows: List[sqlite3.Row]) -> List[sqlite3.Row]:
    """One row per fixture: latest capture at or before kickoff (+15m grace)."""
    best: Dict[int, sqlite3.Row] = {}
    for r in rows:
        try:
            fid = int(r["fixture_id"])
        except (TypeError, ValueError):
            continue
        ko = _parse_kickoff_iso(str(r["kickoff_iso"] or ""))
        cap = _parse_kickoff_iso(str(r["captured_at"] or ""))
        if ko and cap and cap > ko + timedelta(minutes=15):
            continue
        prev = best.get(fid)
        if prev is None or str(r["captured_at"] or "") >= str(prev["captured_at"] or ""):
            best[fid] = r
    return list(best.values())


def _metrics_for_rows(rows: List[sqlite3.Row]) -> Dict[str, Any]:
    """Brier, log loss, pick accuracy, value legs on scored deduped rows."""
    brier_sum = 0.0
    logloss_sum = 0.0
    book_brier_sum = 0.0
    book_n = 0
    n = 0
    pick_correct = 0
    pick_attempts = 0
    value_wins = 0
    value_losses = 0
    by_league: Dict[str, Dict[str, Any]] = {}
    by_day: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        out = (r["result_outcome"] or "").lower()
        if out not in ("home", "draw", "away"):
            continue
        try:
            pred = json.loads(r["prediction_json"])
        except Exception:
            continue
        probs = pred.get("probabilities") or {}
        ph = _safe_prob(probs.get("home"))
        pd = _safe_prob(probs.get("draw"))
        pa = _safe_prob(probs.get("away"))
        yh, yd, ya = (1.0, 0.0, 0.0) if out == "home" else ((0.0, 1.0, 0.0) if out == "draw" else (0.0, 0.0, 1.0))
        brier = (ph - yh) ** 2 + (pd - yd) ** 2 + (pa - ya) ** 2
        p_correct = ph if out == "home" else (pd if out == "draw" else pa)
        brier_sum += brier
        logloss_sum += -math.log(p_correct)
        n += 1

        bo = pred.get("bookmaker_odds") or {}
        try:
            oh = _implied_from_decimal(bo.get("home"))
            od = _implied_from_decimal(bo.get("draw"))
            oa = _implied_from_decimal(bo.get("away"))
        except Exception:
            oh = od = oa = None
        if oh and od and oa:
            tot = oh + od + oa
            bh, bd, ba = oh / tot, od / tot, oa / tot
            book_brier_sum += (bh - yh) ** 2 + (bd - yd) ** 2 + (ba - ya) ** 2
            book_n += 1

        pick = (pred.get("predicted_outcome") or "").lower()
        if pick in ("home", "draw", "away"):
            pick_attempts += 1
            if pick == out:
                pick_correct += 1

        vr = _value_pick_result_label(r, pred)
        if vr == "W":
            value_wins += 1
        elif vr == "L":
            value_losses += 1

        lg = str(r["league_code"] or "unknown")
        bucket = by_league.setdefault(lg, {"n": 0, "brier_sum": 0.0, "pick_ok": 0, "pick_n": 0})
        bucket["n"] += 1
        bucket["brier_sum"] += brier
        if pick in ("home", "draw", "away"):
            bucket["pick_n"] += 1
            if pick == out:
                bucket["pick_ok"] += 1

        day = str(r["kickoff_iso"] or "")[:10] or "unknown"
        db = by_day.setdefault(day, {"n": 0, "brier_sum": 0.0})
        db["n"] += 1
        db["brier_sum"] += brier

    if n == 0:
        return {"n_scored": 0}

    baseline = _env_float("HIBS_CALIB_BASELINE_BRIER", 0.66)
    uniform_brier = round(2.0 / 3.0, 5)
    model_brier = round(brier_sum / n, 5)
    out_metrics: Dict[str, Any] = {
        "n_scored": n,
        "brier_score_1x2": model_brier,
        "log_loss_1x2": round(logloss_sum / n, 5),
        "best_pick_accuracy_pct": round(100.0 * pick_correct / pick_attempts, 2) if pick_attempts else None,
        "best_pick_n": pick_attempts,
        "best_pick_correct": pick_correct,
        "baseline_brier": baseline,
        "uniform_random_brier": uniform_brier,
        "beats_baseline": model_brier < baseline,
        "beats_uniform": model_brier < uniform_brier,
        "value_settled": value_wins + value_losses,
        "value_wins": value_wins,
        "value_losses": value_losses,
    }
    if value_wins + value_losses:
        out_metrics["value_hit_rate_pct"] = round(100.0 * value_wins / (value_wins + value_losses), 2)
    if book_n:
        out_metrics["book_implied_brier"] = round(book_brier_sum / book_n, 5)
        out_metrics["book_n"] = book_n
        out_metrics["model_vs_book_brier_delta"] = round(model_brier - book_brier_sum / book_n, 5)

    league_rows = []
    for lg, b in sorted(by_league.items(), key=lambda x: -x[1]["n"]):
        ns = int(b["n"])
        pn = int(b["pick_n"])
        league_rows.append(
            {
                "league": lg,
                "n": ns,
                "brier": round(b["brier_sum"] / ns, 5),
                "pick_accuracy_pct": round(100.0 * b["pick_ok"] / pn, 2) if pn else None,
            }
        )
    out_metrics["by_league"] = league_rows
    out_metrics["by_day"] = [
        {"day": d, "n": int(b["n"]), "brier": round(b["brier_sum"] / b["n"], 5)}
        for d, b in sorted(by_day.items())
    ]
    return out_metrics


def backtest_report_dict(*, days: int = 30) -> Dict[str, Any]:
    """
    Honest rolling backtest on engine audit log (pre-kickoff snapshots vs FT results).

    Uses kickoff time in the last ``days`` calendar days. Dedupes to one snapshot per fixture.
    Reports coverage limitations when settlement or history is incomplete.
    """
    window_days = max(1, int(days))
    baseline = _env_float("HIBS_CALIB_BASELINE_BRIER", 0.66)
    out: Dict[str, Any] = {
        "ok": True,
        "requested_window_days": window_days,
        "baseline_brier": baseline,
        "db_path": _db_path(),
        "honest_summary": "",
        "limitations": [],
    }
    if not prediction_log_enabled():
        out["ok"] = False
        out["message"] = "HIBS_PREDICTION_LOG_ENABLED=0 — enable audit logging first."
        return out
    if not os.path.isfile(_db_path()):
        out["ok"] = False
        out["message"] = "No prediction_audit.sqlite — load dashboard to seed snapshots."
        return out

    init_db()
    cutoff = _kickoff_window_cutoff_iso(days=window_days)
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(_db_path(), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        all_rows = list(
            conn.execute(
                """
                SELECT id, fixture_id, league_code, captured_at, kickoff_iso, home_name, away_name,
                       prediction_json, enrich_summary_json, result_outcome, result_recorded_at,
                       result_home, result_away, result_status, data_quality_pct
                FROM prediction_snapshots
                WHERE kickoff_iso IS NOT NULL AND kickoff_iso != ''
                  AND kickoff_iso >= ?
                ORDER BY kickoff_iso
                """,
                (cutoff,),
            ).fetchall()
        )
        span = conn.execute(
            "SELECT MIN(kickoff_iso), MAX(kickoff_iso), MIN(captured_at), MAX(captured_at), COUNT(*) FROM prediction_snapshots"
        ).fetchone()
    finally:
        conn.close()

    if span:
        out["data_span_all_time"] = {
            "first_kickoff": (span[0] or "")[:10],
            "last_kickoff": (span[1] or "")[:10],
            "first_capture": (span[2] or "")[:10],
            "last_capture": (span[3] or "")[:10],
            "snapshots_total": int(span[4] or 0),
        }

    deduped = _dedupe_latest_pre_kickoff(all_rows)
    future = 0
    pending = 0
    scored_rows: List[sqlite3.Row] = []
    for r in deduped:
        ko = _parse_kickoff_iso(str(r["kickoff_iso"] or ""))
        if ko and now < ko + timedelta(hours=2.5):
            future += 1
            continue
        if r["result_outcome"] and str(r["result_outcome"]).strip():
            scored_rows.append(r)
        else:
            pending += 1

    n_dedup = len(deduped)
    n_scored = len(scored_rows)
    out["coverage"] = {
        "snapshots_in_kickoff_window": len(all_rows),
        "fixtures_deduped_pre_kickoff": n_dedup,
        "scored_with_ft": n_scored,
        "pending_kickoff_passed_no_result": pending,
        "future_or_too_recent": future,
        "settlement_rate_pct": round(100.0 * n_scored / n_dedup, 2) if n_dedup else None,
        "window_start_kickoff": cutoff[:10],
    }

    if deduped:
        first_ko = str(deduped[0]["kickoff_iso"] or "")[:10]
        last_ko = str(deduped[-1]["kickoff_iso"] or "")[:10]
        out["actual_kickoff_span"] = {"first": first_ko, "last": last_ko}
        try:
            d0 = datetime.fromisoformat(first_ko)
            d1 = datetime.fromisoformat(last_ko)
            out["actual_calendar_days"] = (d1 - d0).days + 1
        except Exception:
            out["actual_calendar_days"] = None

    if out.get("actual_calendar_days") is not None and out["actual_calendar_days"] < window_days:
        out["limitations"].append(
            f"Audit history covers ~{out['actual_calendar_days']} calendar day(s) of kickoffs, "
            f"not a full {window_days}-day backtest — metrics are indicative only."
        )
    if n_dedup and n_scored < max(10, int(0.5 * n_dedup)):
        out["limitations"].append(
            f"Only {n_scored}/{n_dedup} deduped fixtures have FT results — run pred-log-sync; "
            "check API plan covers your leagues (Nordics/UCL may return empty)."
        )
    if len(all_rows) > n_dedup * 3:
        out["limitations"].append(
            "High snapshot duplication — bundle re-logging; backtest uses latest pre-kickoff row per fixture."
        )

    if n_scored == 0:
        out["ok"] = True
        out["metrics"] = None
        out["message"] = "No scored fixtures in window — settlement loop not closed yet."
        out["honest_summary"] = (
            f"0/{n_dedup} fixtures in the last {window_days}d kickoff window have FT results in the audit DB."
        )
        out["pred_log_sync_cron"] = pred_log_sync_cron_status()
        return out

    from hibs_predictor.league_profiles import (
        elite_pitch_league_codes,
        headline_pitch_league_codes,
        pipeline_excluded_league_codes,
    )

    pipeline_excluded = pipeline_excluded_league_codes()
    if pipeline_excluded:
        out["pipeline_excluded_leagues"] = sorted(pipeline_excluded)
        before = len(scored_rows)
        scored_rows = [
            r for r in scored_rows if str(r["league_code"] or "").upper() not in pipeline_excluded
        ]
        n_scored = len(scored_rows)
        if before != n_scored:
            out["limitations"].append(
                "Headline metrics exclude pipeline-excluded leagues from config/league_profiles.yaml: "
                + ", ".join(sorted(pipeline_excluded))
                + "."
            )
        out["coverage"]["scored_with_ft_production"] = n_scored
        out["coverage"]["scored_excluded_from_headline"] = before - n_scored

    if n_scored == 0:
        out["ok"] = True
        out["metrics"] = None
        out["message"] = "No scored fixtures remain after pipeline exclusions."
        out["honest_summary"] = (
            f"0 production-cohort fixtures scored in window (excluded: {', '.join(sorted(pipeline_excluded))})."
        )
        out["pred_log_sync_cron"] = pred_log_sync_cron_status()
        return out

    metrics = _metrics_for_rows(scored_rows)
    out["metrics"] = metrics
    mb = metrics.get("brier_score_1x2")
    pick_pct = metrics.get("best_pick_accuracy_pct")
    out["honest_summary"] = (
        f"On {n_scored} settled fixtures (deduped pre-kickoff): Brier {mb}, log loss {metrics.get('log_loss_1x2')}, "
        f"top pick {pick_pct}% correct"
        + (
            f", value hit {metrics.get('value_hit_rate_pct')}% ({metrics.get('value_settled')} legs)"
            if metrics.get("value_settled")
            else ""
        )
        + (
            f" — {'beats' if metrics.get('beats_baseline') else 'above'} baseline {baseline}"
        )
    )
    vr = out.get("top_europe_value_replay") or {}
    if vr.get("bets"):
        out["honest_summary"] += (
            f"; value replay {vr.get('realized_roi_pct')}% realized ROI on {vr.get('bets')} legs"
            f" (avg theoretical {vr.get('avg_theoretical_roi_pct')}%)"
        )
    if not metrics.get("beats_baseline"):
        out["limitations"].append(
            f"Model Brier {mb} is not below sale baseline {baseline} on this sample — do not market as calibrated yet."
        )
    odds_avail_n = 0
    clv_scored_n = 0
    for r in scored_rows:
        try:
            enrich = json.loads(r["enrich_summary_json"] or "")
        except Exception:
            enrich = {}
        if isinstance(enrich, dict) and enrich.get("odds_available"):
            odds_avail_n += 1
            clv = enrich.get("clv")
            if isinstance(clv, dict) and clv.get("clv_pp") is not None:
                clv_scored_n += 1
    if odds_avail_n >= 10 and clv_scored_n < max(3, int(0.1 * odds_avail_n)):
        out["limitations"].append(
            "CLV beat-close is thin on this sample — ensure HIBS_CLV_LOG_ENABLED=1, opening odds "
            "capture (bookmaker_odds / line_odds / fixture odds), and daily audit sync with closing 1X2."
        )
    if n_scored < _env_float("HIBS_SCALE_READY_MIN_N", 25):
        out["limitations"].append(
            f"Sample size {n_scored} < {_env_float('HIBS_SCALE_READY_MIN_N', 25)} — too small for buyer-grade proof."
        )
    out["pred_log_sync_cron"] = pred_log_sync_cron_status()
    out["scale_readiness"] = scale_readiness_dict()

    try:
        from hibs_predictor.value_replay import eval_replay_metrics, load_settled_fixtures

        top_europe = {
            "EPL", "SCOTLAND", "UCL", "EUROPA_LEAGUE", "UECL",
            "LA_LIGA", "SERIE_A", "BUNDESLIGA", "LIGUE_1", "EREDIVISIE", "PRIMEIRA",
        }
        replay_rows = load_settled_fixtures(window_days, leagues_only=top_europe)
        if replay_rows:
            out["top_europe_value_replay"] = eval_replay_metrics(replay_rows, use_stored_value_bets=False)
    except Exception as replay_exc:
        out["top_europe_value_replay_error"] = str(replay_exc)[:200]

    elite = elite_pitch_league_codes()
    if elite:
        out["elite_pitch_leagues"] = elite
    headline = headline_pitch_league_codes()
    if headline and n_scored:
        headline_rows = [
            r for r in scored_rows if str(r["league_code"] or "").upper() in set(headline)
        ]
        if headline_rows:
            hm = _metrics_for_rows(headline_rows)
            out["headline_pitch_leagues"] = headline
            out["headline_pitch_metrics"] = hm
            out["headline_pitch_summary"] = (
                f"Tier-1 domestic headline cohort ({len(headline_rows)} fixtures): "
                f"Brier {hm.get('brier_score_1x2')}, top pick {hm.get('best_pick_accuracy_pct')}% "
                "(excludes CHAMPIONSHIP — see full cohort metrics above)."
            )
    return out


def report_summary_dict() -> Dict[str, Any]:
    """Brier score (1X2), log loss, counts, optional value-hit — for API + CLI."""
    if not os.path.isfile(_db_path()):
        return {"ok": False, "error": "no_database", "path": _db_path()}
    init_db()
    conn = sqlite3.connect(_db_path(), timeout=20)
    try:
        rows = _rows_with_results(conn)
    finally:
        conn.close()
    n = len(rows)
    if n == 0:
        return {
            "ok": True,
            "n_scored_rows": 0,
            "n_used_metrics": 0,
            "message": "No rows with recorded results yet. Run pred-log-sync.",
            "brier_by_data_quality_bucket": brier_by_data_quality_bucket(),
            "clv_by_league": clv_beat_close_by_league(),
            "brier_by_league": brier_by_league(),
            "scale_readiness": scale_readiness_dict(),
        }

    brier_sum = 0.0
    logloss_sum = 0.0
    value_attempts = 0
    value_wins = 0
    value_losses = 0
    value_pending = 0
    n_used = 0

    for r in rows:
        try:
            pred = json.loads(r["prediction_json"])
        except Exception:
            continue
        probs = pred.get("probabilities") or {}
        ph = _safe_prob(probs.get("home"))
        pd = _safe_prob(probs.get("draw"))
        pa = _safe_prob(probs.get("away"))
        out = (r["result_outcome"] or "").lower()
        if out not in ("home", "draw", "away"):
            continue
        yh, yd, ya = (1.0, 0.0, 0.0) if out == "home" else ((0.0, 1.0, 0.0) if out == "draw" else (0.0, 0.0, 1.0))
        brier_sum += (ph - yh) ** 2 + (pd - yd) ** 2 + (pa - ya) ** 2
        p_correct = ph if out == "home" else (pd if out == "draw" else pa)
        logloss_sum += -math.log(p_correct)
        n_used += 1

        if _value_pick_snapshot(pred):
            value_attempts += 1
            vlabel = _value_pick_result_label(r, pred)
            if vlabel == "W":
                value_wins += 1
            elif vlabel == "L":
                value_losses += 1
            else:
                value_pending += 1

    if n_used == 0:
        return {
            "ok": True,
            "n_scored_rows": n,
            "n_used_metrics": 0,
            "message": "No rows with parseable 1X2 results yet. Run pred-log-sync after matches finish.",
            "brier_by_data_quality_bucket": brier_by_data_quality_bucket(),
            "clv_by_league": clv_beat_close_by_league(),
            "brier_by_league": brier_by_league(),
        }

    n_eff = max(1, n_used)
    out: Dict[str, Any] = {
        "ok": True,
        "n_scored_rows": n,
        "n_used_metrics": n_used,
        "brier_score_1x2": round(brier_sum / n_eff, 5),
        "log_loss_1x2": round(logloss_sum / n_eff, 5),
        "value_flags_count": value_attempts,
        "value_best_outcome_hits": value_wins,
        "value_losses": value_losses,
        "value_pending": value_pending,
        "brier_by_data_quality_bucket": brier_by_data_quality_bucket(),
    }
    value_settled = value_wins + value_losses
    out["value_settled"] = value_settled
    if value_settled:
        out["value_hit_rate"] = round(100.0 * value_wins / value_settled, 2)
    out["clv_by_league"] = clv_beat_close_by_league()
    out["brier_by_league"] = brier_by_league()
    out["scale_readiness"] = scale_readiness_dict()
    return out


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _league_cohort(league_code: str) -> str:
    """Split audit metrics: friendlies vs staking-scale (domestic / Nordics / WC — never pooled)."""
    lc = (league_code or "").strip().upper()
    if lc == "INTL_FRIENDLIES":
        return "friendlies"
    if lc in ("WORLD_CUP", "NORWAY_ELITESERIEN", "FINLAND_VEIKKAUSLIIGA", "DENMARK_SL"):
        return "scale"
    if lc and lc not in ("UNKNOWN", "SCOTTISH PREMIERSHIP"):
        return "scale"
    return "other"


def scale_readiness_dict() -> Dict[str, Any]:
    """
    Staking-scale gate: Brier vs baseline and CLV beat-close on value-flagged picks.

    Friendlies are reported separately and never mixed into ``scale`` aggregates.
    """
    baseline = _env_float("HIBS_CALIB_BASELINE_BRIER", 0.66)
    min_n = max(5, int(_env_float("HIBS_SCALE_READY_MIN_N", 25)))
    min_clv_pct = _env_float("HIBS_SCALE_READY_CLV_PCT", 60.0)
    empty: Dict[str, Any] = {
        "baseline_brier": baseline,
        "min_scored_n": min_n,
        "min_clv_beat_pct": min_clv_pct,
        "scale_ready": False,
        "cohorts": {},
        "message": "No scored rows yet — run pred-log-sync after matches finish.",
    }
    if not os.path.isfile(_db_path()):
        empty["message"] = "No audit database yet."
        return empty
    init_db()
    conn = sqlite3.connect(_db_path(), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT league_code, prediction_json, result_outcome, enrich_summary_json
            FROM prediction_snapshots
            WHERE result_outcome IS NOT NULL AND result_outcome != ''
            """
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return empty

    from hibs_predictor.league_profiles import pipeline_excluded_league_codes

    pipeline_excluded = pipeline_excluded_league_codes()
    cohorts: Dict[str, Dict[str, Any]] = {
        "scale": {"label": "Elite domestic + WC (staking scale)", "n_brier": 0, "brier_sum": 0.0, "clv_n": 0, "clv_beat": 0},
        "friendlies": {"label": "International friendlies (audit only)", "n_brier": 0, "brier_sum": 0.0, "clv_n": 0, "clv_beat": 0},
        "other": {"label": "Other / legacy rows", "n_brier": 0, "brier_sum": 0.0, "clv_n": 0, "clv_beat": 0},
    }
    by_league: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        lg = str(r["league_code"] or "unknown")
        if lg.upper() in pipeline_excluded:
            continue
        cohort = _league_cohort(lg)
        bucket = cohorts[cohort]
        lg_bucket = by_league.setdefault(
            lg,
            {"league": lg, "cohort": cohort, "n_brier": 0, "brier_sum": 0.0, "clv_n": 0, "clv_beat": 0},
        )
        try:
            pred = json.loads(r["prediction_json"])
        except Exception:
            continue
        probs = pred.get("probabilities") or {}
        ph = _safe_prob(probs.get("home"))
        pd = _safe_prob(probs.get("draw"))
        pa = _safe_prob(probs.get("away"))
        out = (r["result_outcome"] or "").lower()
        if out not in ("home", "draw", "away"):
            continue
        yh, yd, ya = (1.0, 0.0, 0.0) if out == "home" else ((0.0, 1.0, 0.0) if out == "draw" else (0.0, 0.0, 1.0))
        brier = (ph - yh) ** 2 + (pd - yd) ** 2 + (pa - ya) ** 2
        bucket["n_brier"] += 1
        bucket["brier_sum"] += brier
        lg_bucket["n_brier"] += 1
        lg_bucket["brier_sum"] += brier

        if not _value_pick_snapshot(pred):
            continue
        try:
            enrich = json.loads(r["enrich_summary_json"] or "")
        except Exception:
            enrich = {}
        clv = enrich.get("clv") if isinstance(enrich, dict) else None
        if not isinstance(clv, dict) or clv.get("clv_pp") is None:
            continue
        try:
            pp_f = float(clv["clv_pp"])
        except (TypeError, ValueError):
            continue
        bucket["clv_n"] += 1
        lg_bucket["clv_n"] += 1
        if pp_f > 0:
            bucket["clv_beat"] += 1
            lg_bucket["clv_beat"] += 1

    out_cohorts: Dict[str, Any] = {}
    for key, b in cohorts.items():
        nb = int(b["n_brier"])
        cn = int(b["clv_n"])
        row: Dict[str, Any] = {
            "label": b["label"],
            "n_scored": nb,
            "brier_score_1x2": round(b["brier_sum"] / nb, 5) if nb else None,
            "brier_vs_baseline": round((b["brier_sum"] / nb) - baseline, 5) if nb else None,
            "beats_baseline": bool(nb and (b["brier_sum"] / nb) < baseline),
        }
        if cn:
            row["value_clv_n"] = cn
            row["value_beat_close_pct"] = round(100.0 * b["clv_beat"] / cn, 2)
            row["clv_gate_ok"] = row["value_beat_close_pct"] >= min_clv_pct
        else:
            row["value_clv_n"] = 0
            row["value_beat_close_pct"] = None
            row["clv_gate_ok"] = False
        out_cohorts[key] = row

    league_rows: List[Dict[str, Any]] = []
    for lg, b in sorted(by_league.items(), key=lambda x: -x[1]["n_brier"]):
        nb = int(b["n_brier"])
        cn = int(b["clv_n"])
        league_rows.append(
            {
                "league": lg,
                "cohort": b["cohort"],
                "n_scored": nb,
                "brier": round(b["brier_sum"] / nb, 5) if nb else None,
                "value_clv_n": cn,
                "value_beat_close_pct": round(100.0 * b["clv_beat"] / cn, 2) if cn else None,
            }
        )

    scale = out_cohorts.get("scale") or {}
    n_scale = int(scale.get("n_scored") or 0)
    brier_ok = bool(scale.get("beats_baseline"))
    clv_ok = bool(scale.get("clv_gate_ok"))
    scale_ready = n_scale >= min_n and brier_ok and clv_ok
    parts: List[str] = []
    if n_scale < min_n:
        parts.append(f"Need {min_n - n_scale} more scored scale-cohort fixtures (have {n_scale}).")
    elif not brier_ok:
        parts.append(
            f"Brier {scale.get('brier_score_1x2')} not below baseline {baseline} on scale cohort."
        )
    elif not clv_ok:
        parts.append(
            f"Value CLV beat-close {scale.get('value_beat_close_pct')}% below {min_clv_pct}% target."
        )
    else:
        parts.append("Scale gates passed on domestic/Nordics/WC — friendlies excluded.")

    return {
        "baseline_brier": baseline,
        "min_scored_n": min_n,
        "min_clv_beat_pct": min_clv_pct,
        "scale_ready": scale_ready,
        "cohorts": out_cohorts,
        "by_league": league_rows,
        "message": " ".join(parts) if parts else "Insufficient data.",
    }


def brier_by_league() -> List[Dict[str, Any]]:
    """Mean 1X2 Brier per league_code for scored snapshots (calibration shrink input)."""
    if not os.path.isfile(_db_path()):
        return []
    init_db()
    conn = sqlite3.connect(_db_path(), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT league_code, prediction_json, result_outcome
            FROM prediction_snapshots
            WHERE result_outcome IS NOT NULL AND result_outcome != ''
            """
        ).fetchall()
    finally:
        conn.close()

    by_league: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        try:
            pred = json.loads(r["prediction_json"])
        except Exception:
            continue
        probs = pred.get("probabilities") or {}
        ph = _safe_prob(probs.get("home"))
        pd = _safe_prob(probs.get("draw"))
        pa = _safe_prob(probs.get("away"))
        oc = (r["result_outcome"] or "").lower()
        if oc not in ("home", "draw", "away"):
            continue
        yh, yd, ya = (1.0, 0.0, 0.0) if oc == "home" else ((0.0, 1.0, 0.0) if oc == "draw" else (0.0, 0.0, 1.0))
        brier = (ph - yh) ** 2 + (pd - yd) ** 2 + (pa - ya) ** 2
        lg = str(r["league_code"] or "unknown")
        bucket = by_league.setdefault(lg, {"n": 0, "brier_sum": 0.0})
        bucket["n"] += 1
        bucket["brier_sum"] += brier

    out: List[Dict[str, Any]] = []
    for lg, b in sorted(by_league.items(), key=lambda x: -x[1]["n"]):
        n = int(b["n"])
        out.append(
            {
                "league": lg,
                "n": n,
                "brier": round(b["brier_sum"] / n, 5) if n else None,
            }
        )
    return out


def clv_value_beat_close_summary(*, days: int = 28, since_iso: str | None = None) -> Dict[str, Any]:
    """CLV headline restricted to logged value picks (scale / staking cohort)."""
    return clv_beat_close_summary(days=days, since_iso=since_iso, value_only=True)


def clv_beat_close_by_league() -> Dict[str, Any]:
    """CLV beat-close rate and mean clv_pp grouped by league (requires HIBS_CLV_LOG_ENABLED snapshots)."""
    if not os.path.isfile(_db_path()):
        return {"enabled": False, "leagues": []}
    init_db()
    conn = sqlite3.connect(_db_path(), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT league_code, enrich_summary_json
            FROM prediction_snapshots
            WHERE enrich_summary_json IS NOT NULL AND enrich_summary_json != ''
            """
        ).fetchall()
    finally:
        conn.close()

    by_league: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        try:
            enrich = json.loads(r["enrich_summary_json"])
        except Exception:
            continue
        clv = enrich.get("clv") if isinstance(enrich, dict) else None
        if not isinstance(clv, dict):
            continue
        pp = clv.get("clv_pp")
        if pp is None:
            continue
        try:
            pp_f = float(pp)
        except (TypeError, ValueError):
            continue
        lg = str(r["league_code"] or "unknown")
        bucket = by_league.setdefault(lg, {"n": 0, "beat_close": 0, "clv_pp_sum": 0.0})
        bucket["n"] += 1
        bucket["clv_pp_sum"] += pp_f
        if pp_f > 0:
            bucket["beat_close"] += 1

    leagues_out: List[Dict[str, Any]] = []
    total_n = 0
    total_beat = 0
    for lg, b in sorted(by_league.items(), key=lambda x: -x[1]["n"]):
        n = int(b["n"])
        beat = int(b["beat_close"])
        total_n += n
        total_beat += beat
        leagues_out.append(
            {
                "league": lg,
                "n_clv": n,
                "beat_close_pct": round(100.0 * beat / n, 2) if n else None,
                "avg_clv_pp": round(b["clv_pp_sum"] / n, 2) if n else None,
            }
        )
    return {
        "enabled": _clv_enabled(),
        "n_clv_rows": total_n,
        "beat_close_pct": round(100.0 * total_beat / total_n, 2) if total_n else None,
        "leagues": leagues_out,
    }


def export_scored_csv(target_path: str) -> int:
    """Write one row per scored snapshot. Returns row count written."""
    init_db()
    conn = sqlite3.connect(_db_path(), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT captured_at, fixture_id, league_code, kickoff_iso, home_name, away_name,
                   data_quality_pct, one_x2_mode, xg_source, prediction_json,
                   result_home, result_away, result_outcome, result_recorded_at,
                   result_xg_home, result_xg_away
            FROM prediction_snapshots
            WHERE result_outcome IS NOT NULL AND result_outcome != ''
            ORDER BY kickoff_iso
            """
        ).fetchall()
    finally:
        conn.close()
    _ensure_dir(target_path)
    import csv

    n = 0
    with open(target_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "captured_at",
                "fixture_id",
                "league",
                "kickoff",
                "home",
                "away",
                "dq_pct",
                "mode",
                "xg_source",
                "p_home",
                "p_draw",
                "p_away",
                "pred_outcome",
                "res_home",
                "res_away",
                "res_outcome",
                "result_at",
                "res_xg_home",
                "res_xg_away",
            ]
        )
        for r in rows:
            try:
                pred = json.loads(r["prediction_json"])
            except Exception:
                pred = {}
            pr = pred.get("probabilities") or {}
            w.writerow(
                [
                    r["captured_at"],
                    r["fixture_id"],
                    r["league_code"],
                    r["kickoff_iso"],
                    r["home_name"],
                    r["away_name"],
                    r["data_quality_pct"],
                    r["one_x2_mode"],
                    r["xg_source"],
                    pr.get("home"),
                    pr.get("draw"),
                    pr.get("away"),
                    pred.get("predicted_outcome"),
                    r["result_home"],
                    r["result_away"],
                    r["result_outcome"],
                    r["result_recorded_at"],
                    r["result_xg_home"] if "result_xg_home" in r.keys() else None,
                    r["result_xg_away"] if "result_xg_away" in r.keys() else None,
                ]
            )
            n += 1
    return n


def brier_by_data_quality_bucket() -> List[Dict[str, Any]]:
    """Mean Brier for buckets of data_quality_pct (quartile-style bins)."""
    init_db()
    conn = sqlite3.connect(_db_path(), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        rows = _rows_with_results(conn)
    finally:
        conn.close()

    bins = [
        ("0-60", 0, 60),
        ("60-75", 60, 75),
        ("75-85", 75, 85),
        ("85-100", 85, 100.1),
    ]
    out: List[Dict[str, Any]] = []
    for label, lo, hi in bins:
        sub = [r for r in rows if lo <= float(r["data_quality_pct"] or 0) < hi]
        if not sub:
            out.append({"bucket": label, "n": 0, "brier": None})
            continue
        s = 0.0
        n_bin = 0
        for r in sub:
            try:
                pred = json.loads(r["prediction_json"])
            except Exception:
                continue
            probs = pred.get("probabilities") or {}
            ph, pd, pa = _safe_prob(probs.get("home")), _safe_prob(probs.get("draw")), _safe_prob(probs.get("away"))
            oc = (r["result_outcome"] or "").lower()
            if oc not in ("home", "draw", "away"):
                continue
            yh, yd, ya = (1, 0, 0) if oc == "home" else ((0, 1, 0) if oc == "draw" else (0, 0, 1))
            s += (ph - yh) ** 2 + (pd - yd) ** 2 + (pa - ya) ** 2
            n_bin += 1
        if n_bin == 0:
            out.append({"bucket": label, "n": 0, "brier": None})
        else:
            out.append({"bucket": label, "n": n_bin, "brier": round(s / n_bin, 5)})
    return out
