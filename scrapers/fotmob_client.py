"""FotMob public JSON — fixtures + league-table stats (no API-Football key)."""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

MATCHES_URL = "https://www.fotmob.com/api/data/matches"
LEAGUES_URL = "https://www.fotmob.com/api/data/leagues"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; fve-scrape/1.0)",
    "Accept": "application/json",
    "Referer": "https://www.fotmob.com/",
}
def _match_upcoming(match: Dict[str, Any]) -> bool:
    status = match.get("status") if isinstance(match.get("status"), dict) else {}
    if status.get("finished") or status.get("completed"):
        return False
    reason = status.get("reason") if isinstance(status.get("reason"), dict) else {}
    short = str(reason.get("short") or status.get("short") or "").upper()
    if short in ("FT", "AET", "PEN", "FULL_TIME", "FINISHED"):
        return False
    return True


def _cache_dir() -> Path:
    p = Path(os.environ.get("FVE_SCRAPE_CACHE_DIR", ".cache/fve-scrape"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cache_get(key: str, ttl_sec: int = 7200) -> Any:
    path = _cache_dir() / f"{key}.json"
    if not path.is_file():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(blob.get("_cached_at", 0)) > ttl_sec:
            return None
        return blob.get("data")
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _cache_set(key: str, data: Any) -> None:
    path = _cache_dir() / f"{key}.json"
    try:
        path.write_text(json.dumps({"_cached_at": time.time(), "data": data}), encoding="utf-8")
    except OSError:
        pass


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def fetch_matches_for_date(day: date) -> Dict[str, Any]:
    tz = (os.environ.get("FOTMOB_TIMEZONE") or "Europe/London").strip()
    key = f"matches_{day.isoformat()}_{tz}"
    cached = _cache_get(key)
    if isinstance(cached, dict):
        return cached
    resp = requests.get(
        MATCHES_URL,
        params={"date": day.strftime("%Y%m%d"), "timezone": tz},
        headers=_HEADERS,
        timeout=25,
    )
    resp.raise_for_status()
    payload = resp.json() if isinstance(resp.json(), dict) else {}
    _cache_set(key, payload)
    return payload


def fetch_league_table(league_id: int) -> Dict[str, Any]:
    key = f"league_{league_id}"
    cached = _cache_get(key, ttl_sec=43200)
    if isinstance(cached, dict):
        return cached
    resp = requests.get(LEAGUES_URL, params={"id": league_id}, headers=_HEADERS, timeout=25)
    resp.raise_for_status()
    payload = resp.json() if isinstance(resp.json(), dict) else {}
    _cache_set(key, payload)
    return payload


def _row_to_stats(row: Dict[str, Any]) -> Dict[str, Any]:
    def _int(v: Any) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    played = _int(row.get("played") or row.get("matches") or row.get("pts"))
    gf = _int(row.get("goals") or row.get("goalsfor") or row.get("goalsFor"))
    ga = _int(row.get("goalsConceded") or row.get("goalsagainst") or row.get("goalsAgainst"))
    return {
        "name": str(row.get("name") or row.get("teamName") or ""),
        "played": played,
        "goals_for": gf,
        "goals_against": ga,
        "home_played": played // 2,
        "home_goals_for": gf // 2,
        "home_goals_against": ga // 2,
        "away_played": played // 2,
        "away_goals_for": gf - gf // 2,
        "away_goals_against": ga - ga // 2,
    }


def team_stats_from_league(league_id: int, team_name: str) -> Dict[str, Any]:
    payload = fetch_league_table(league_id)
    target = _norm(team_name)
    for block in ("table", "all", "standings", "teams"):
        rows = payload.get(block)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("teamName") or (row.get("team") or {}).get("name") or "")
            if target and target not in _norm(name) and _norm(name) not in target:
                continue
            stats = _row_to_stats(row)
            if stats.get("played"):
                stats["source"] = "fotmob_table"
                return stats
    # FotMob nests table under data.table.all
    table = payload.get("table") or {}
    if isinstance(table, dict):
        for row in table.get("all") or table.get("data") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")
            if target and target not in _norm(name) and _norm(name) not in target:
                continue
            stats = _row_to_stats(row)
            if stats.get("played"):
                stats["source"] = "fotmob_table"
                return stats
    return {}


def discover_fixtures(
    league_ids: Dict[str, int],
    *,
    days_ahead: int = 3,
) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    keys: List[str] = []
    contexts: Dict[str, Dict[str, Any]] = {}
    today = date.today()
    wanted_ids = set(league_ids.values())
    id_to_name = {v: k for k, v in league_ids.items()}

    for offset in range(max(days_ahead, 1)):
        day = today + timedelta(days=offset)
        try:
            payload = fetch_matches_for_date(day)
        except requests.RequestException:
            continue
        for league in payload.get("leagues") or []:
            if not isinstance(league, dict):
                continue
            lid = league.get("primaryId") or league.get("id")
            try:
                lid_int = int(lid)
            except (TypeError, ValueError):
                continue
            if lid_int not in wanted_ids:
                continue
            league_name = id_to_name.get(lid_int, "")
            for match in league.get("matches") or []:
                if not isinstance(match, dict):
                    continue
                if not _match_upcoming(match):
                    continue
                home = str((match.get("home") or {}).get("name") if isinstance(match.get("home"), dict) else match.get("home") or "")
                away = str((match.get("away") or {}).get("name") if isinstance(match.get("away"), dict) else match.get("away") or "")
                if not home or not away:
                    continue
                fk = f"{home} v {away}"
                if fk in contexts:
                    continue
                contexts[fk] = {
                    "home_team": home,
                    "away_team": away,
                    "event_label": fk,
                    "league_name": league_name,
                    "fotmob_league_id": lid_int,
                    "fotmob_match_id": match.get("id"),
                }
                keys.append(fk)
    return keys, contexts


def sports_for_fixture(fixture_key: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    home = str(context.get("home_team") or "")
    away = str(context.get("away_team") or "")
    if not home or not away:
        parts = fixture_key.split(" v ", 1)
        if len(parts) == 2:
            home, away = parts[0].strip(), parts[1].strip()
    lid = context.get("fotmob_league_id")
    if not lid:
        from config.fotmob_leagues import fotmob_id_for_league

        lid = fotmob_id_for_league(str(context.get("league_name") or ""))
    if not lid:
        return None
    home_stats = team_stats_from_league(int(lid), home)
    away_stats = team_stats_from_league(int(lid), away)
    if not home_stats or not away_stats:
        return None
    return {
        "fixture_id": context.get("fixture_id") or context.get("fotmob_match_id"),
        "home_team": home,
        "away_team": away,
        "home_stats": home_stats,
        "away_stats": away_stats,
        "league": context.get("league_name"),
        "source": "fotmob-scrape",
        "data_quality": {
            "home_ok": bool(home_stats.get("played")),
            "away_ok": bool(away_stats.get("played")),
        },
    }
