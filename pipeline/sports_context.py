"""Fixture sports context for model + value scan (standings, kickoff, optional xG)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from model import match_model, goal_model, parse_standings

BASE = "https://v3.football.api-sports.io"
TIMEOUT = 15
_SPORTS_TTL_SEC = int(os.environ.get("SPORTS_CACHE_TTL_SEC", "3600"))


def _api_key() -> str:
    return (os.environ.get("API_SPORTS_KEY") or os.environ.get("API_FOOTBALL_KEY") or "").strip()


def current_season() -> int:
    today = datetime.now(timezone.utc)
    return today.year if today.month >= 7 else today.year - 1


def _api_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    key = _api_key()
    if not key:
        raise RuntimeError("API_SPORTS_KEY not set")
    resp = requests.get(
        f"{BASE}/{path}",
        headers={"x-apisports-key": key},
        params=params,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else {}


def standings_row_to_stats(row: Dict[str, Any]) -> Dict[str, Any]:
    """Map API-Football standings row → model.py team dict."""

    def _split(block: Dict[str, Any]) -> Dict[str, int]:
        return {
            "played": int(block.get("played") or 0),
            "for": int((block.get("goals") or {}).get("for") or 0),
            "against": int((block.get("goals") or {}).get("against") or 0),
        }

    all_ = _split(row.get("all") or {})
    home = _split(row.get("home") or {})
    away = _split(row.get("away") or {})
    team = row.get("team") or {}
    return {
        "name": str(team.get("name") or ""),
        "played": all_["played"],
        "goals_for": all_["for"],
        "goals_against": all_["against"],
        "home_played": home["played"],
        "home_goals_for": home["for"],
        "home_goals_against": home["against"],
        "away_played": away["played"],
        "away_goals_for": away["for"],
        "away_goals_against": away["against"],
    }


def build_sports_payload(
    *,
    fixture: Dict[str, Any],
    standings_table: Dict[int, Dict[str, Any]],
    use_xg: bool = True,
) -> Dict[str, Any]:
    """Pure builder — testable without HTTP."""
    try:
        fx = fixture["fixture"]
        league = fixture["league"]
        teams = fixture["teams"]
        home_id = int(teams["home"]["id"])
        away_id = int(teams["away"]["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid fixture payload: {exc}") from exc

    home_stats = dict(standings_table.get(home_id) or {})
    away_stats = dict(standings_table.get(away_id) or {})
    if not home_stats.get("name"):
        home_stats["name"] = str((teams.get("home") or {}).get("name") or "")
    if not away_stats.get("name"):
        away_stats["name"] = str((teams.get("away") or {}).get("name") or "")

    league_id = int(league.get("id") or 0)
    season = int(league.get("season") or current_season())

    if use_xg and league_id:
        try:
            from xg_sources import attach_xg, fetch_understat_team_xg

            xg_map = fetch_understat_team_xg(league_id, season)
            if xg_map:
                wrapped = {home_id: home_stats, away_id: away_stats}
                merged, _ = attach_xg(wrapped, xg_map)
                home_stats = merged.get(home_id, home_stats)
                away_stats = merged.get(away_id, away_stats)
        except Exception:
            pass

    model_probs = {
        **match_model(home_stats, away_stats, use_xg=use_xg),
        **goal_model(home_stats, away_stats, use_xg=use_xg),
    }

    return {
        "fixture_id": int(fx.get("id") or 0),
        "league_id": league_id,
        "league_name": str(league.get("name") or ""),
        "season": season,
        "kickoff_iso": str(fx.get("date") or ""),
        "status": str((fx.get("status") or {}).get("short") or ""),
        "venue": str((fx.get("venue") or {}).get("name") or ""),
        "home_team": {
            "id": home_id,
            "name": home_stats.get("name") or str((teams.get("home") or {}).get("name") or ""),
        },
        "away_team": {
            "id": away_id,
            "name": away_stats.get("name") or str((teams.get("away") or {}).get("name") or ""),
        },
        "home_stats": home_stats,
        "away_stats": away_stats,
        "model_probs": model_probs,
        "data_quality": {
            "home_ok": bool(home_stats.get("played")),
            "away_ok": bool(away_stats.get("played")),
        },
        "sources": ["api-football"],
        "updated_at": time.time(),
        "ttl_sec": _SPORTS_TTL_SEC,
    }


def fetch_sports_context(context: Dict[str, Any], *, use_xg: Optional[bool] = None) -> Dict[str, Any]:
    """Pull fixture + standings from API-Football; return normalised sports bundle."""
    fixture_id = context.get("fixture_id")
    if not fixture_id:
        raise ValueError("fixture_id required in ingest context for sports data")

    if use_xg is None:
        use_xg = os.environ.get("FVE_USE_XG", "1").strip().lower() not in ("0", "false", "no")

    fx_data = _api_get("fixtures", {"id": int(fixture_id)})
    rows = fx_data.get("response") or []
    if not rows:
        raise ValueError(f"fixture {fixture_id} not found")
    fixture = rows[0]
    league_id = int((fixture.get("league") or {}).get("id") or 0)
    season = int((fixture.get("league") or {}).get("season") or current_season())

    standings_data = _api_get("standings", {"league": league_id, "season": season}) if league_id else {}
    table = parse_standings(standings_data)

    return build_sports_payload(fixture=fixture, standings_table=table, use_xg=bool(use_xg))


def sports_refresh_due(cached: Optional[Dict[str, Any]], *, now: Optional[float] = None) -> bool:
    if not cached:
        return True
    updated = float(cached.get("updated_at") or 0)
    ttl = float(cached.get("ttl_sec") or _SPORTS_TTL_SEC)
    ts = now if now is not None else time.time()
    return ts - updated >= ttl
