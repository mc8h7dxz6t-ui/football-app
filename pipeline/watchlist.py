"""Auto-discover upcoming fixtures for hands-off ingest."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from config.leagues import leagues_for_watchlist
from config.fotmob_leagues import FOTMOB_LEAGUE_ID
from feeds.scrape_mode import scrape_watchlist_enabled

log = logging.getLogger(__name__)

API_BASE = "https://v3.football.api-sports.io"
TIMEOUT = 20
_NOT_STARTED = {"NS", "TBD", "PST"}


def _api_key() -> str:
    return (os.environ.get("API_SPORTS_KEY") or os.environ.get("API_FOOTBALL_KEY") or "").strip()


def _current_season() -> int:
    today = datetime.now(timezone.utc)
    return today.year if today.month >= 7 else today.year - 1


def _load_matchbook_overrides() -> Dict[str, int]:
    path = os.environ.get(
        "FVE_MATCHBOOK_MAP_FILE",
        str(Path(__file__).resolve().parents[1] / "config" / "matchbook_map.json"),
    )
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in raw.items() if v}
    except (OSError, ValueError, TypeError) as exc:
        log.warning("matchbook map %s unreadable: %s", p, exc)
        return {}


def _fetch_fixtures(league_id: int, date: str, key: str) -> List[Dict[str, Any]]:
    from pipeline.rate_limits import get_budget

    budget = get_budget()
    if not budget.allow("api-football"):
        log.warning("api-football budget exhausted — skipping watchlist fetch")
        return []
    try:
        resp = requests.get(
            f"{API_BASE}/fixtures",
            headers={"x-apisports-key": key},
            params={"league": league_id, "season": _current_season(), "date": date},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        budget.record("api-football")
        data = resp.json()
        return list(data.get("response") or [])
    except (requests.RequestException, ValueError) as exc:
        log.warning("fixtures fetch league=%s date=%s failed: %s", league_id, date, exc)
        return []


def _arb_only_mode() -> bool:
    return os.environ.get("FVE_ARB_ONLY", "").strip().lower() in ("1", "true", "yes", "on")


def discover_matchbook_only() -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    """Build watchlist from WATCHLIST_FIXTURES and/or matchbook_map — no API-Sports."""
    manual = os.environ.get("WATCHLIST_FIXTURES", "").strip()
    if manual:
        keys, contexts = parse_fixture_spec(manual)
        log.info("matchbook-only watchlist from WATCHLIST_FIXTURES (%d fixtures)", len(keys))
        return keys, contexts

    mb_map = _load_matchbook_overrides()
    if not mb_map:
        log.error(
            "FVE_ARB_ONLY: set WATCHLIST_FIXTURES or populate config/matchbook_map.json"
        )
        return [], {}

    keys: List[str] = []
    contexts: Dict[str, Dict[str, Any]] = {}
    for fk, event_id in mb_map.items():
        ctx: Dict[str, Any] = {"event_label": fk, "matchbook_event_id": int(event_id)}
        if " v " in fk:
            h, a = fk.split(" v ", 1)
            ctx["home_team"], ctx["away_team"] = h.strip(), a.strip()
        keys.append(fk)
        contexts[fk] = ctx
    log.info("matchbook-only watchlist from map (%d fixtures)", len(keys))
    return keys, contexts


def discover_upcoming(
    *,
    days_ahead: Optional[int] = None,
    leagues: Optional[Dict[str, int]] = None,
) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    """Return fixture keys + ingest contexts for upcoming matches."""
    days = days_ahead if days_ahead is not None else int(os.environ.get("FVE_WATCHLIST_DAYS", "3"))

    if scrape_watchlist_enabled():
        from scrapers.fotmob_client import discover_fixtures

        league_map = leagues or leagues_for_watchlist()
        fotmob_map = {
            name: FOTMOB_LEAGUE_ID[name]
            for name in league_map
            if name in FOTMOB_LEAGUE_ID
        }
        if not fotmob_map:
            fotmob_map = dict(FOTMOB_LEAGUE_ID)
        keys, contexts = discover_fixtures(fotmob_map, days_ahead=days)
        if keys:
            log.info("watchlist fotmob discovered %d fixtures", len(keys))
            return keys, contexts
        log.warning("fotmob watchlist empty — try WATCHLIST_FIXTURES")

    key = _api_key()
    if not key:
        log.error("API_SPORTS_KEY not set — cannot auto-discover watchlist (set FVE_FEED_MODE=scrape or WATCHLIST_FIXTURES)")
        return [], {}

    days = days_ahead if days_ahead is not None else int(os.environ.get("FVE_WATCHLIST_DAYS", "3"))
    league_map = leagues or leagues_for_watchlist()
    mb_map = _load_matchbook_overrides()
    keys: List[str] = []
    contexts: Dict[str, Dict[str, Any]] = {}
    now = datetime.now(timezone.utc)

    for league_name, league_id in league_map.items():
        for offset in range(max(days, 1)):
            date = (now + timedelta(days=offset)).strftime("%Y-%m-%d")
            for f in _fetch_fixtures(league_id, date, key):
                try:
                    status = str(f.get("fixture", {}).get("status", {}).get("short", ""))
                    if status not in _NOT_STARTED:
                        continue
                    fixture_id = int(f["fixture"]["id"])
                    home = str(f["teams"]["home"]["name"])
                    away = str(f["teams"]["away"]["name"])
                except (KeyError, TypeError, ValueError):
                    continue
                fk = f"{home} v {away}"
                if fk in contexts:
                    continue
                ctx: Dict[str, Any] = {
                    "fixture_id": fixture_id,
                    "home_team": home,
                    "away_team": away,
                    "event_label": fk,
                    "league_name": league_name if not league_name.startswith("league_") else "",
                }
                if fk in mb_map:
                    ctx["matchbook_event_id"] = mb_map[fk]
                keys.append(fk)
                contexts[fk] = ctx

    log.info("watchlist discovered %d fixtures (days=%d leagues=%d)", len(keys), days, len(league_map))
    return keys, contexts


def parse_fixture_spec(spec: str) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    """Parse fixture_key:fixture_id:matchbook_event_id comma list."""
    keys: List[str] = []
    contexts: Dict[str, Dict[str, Any]] = {}
    mb_map = _load_matchbook_overrides()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":")
        fk = bits[0]
        ctx: Dict[str, Any] = {"event_label": fk}
        if len(bits) > 1 and bits[1]:
            ctx["fixture_id"] = int(bits[1])
        if len(bits) > 2 and bits[2]:
            ctx["matchbook_event_id"] = int(bits[2])
        elif fk in mb_map:
            ctx["matchbook_event_id"] = mb_map[fk]
        if " v " in fk:
            h, a = fk.split(" v ", 1)
            ctx["home_team"] = h.strip()
            ctx["away_team"] = a.strip()
        keys.append(fk)
        contexts[fk] = ctx
    return keys, contexts


class WatchlistState:
    """Thread-safe fixture list refreshed on a schedule."""

    def __init__(self) -> None:
        self._keys: List[str] = []
        self._contexts: Dict[str, Dict[str, Any]] = {}
        self._updated_at: float = 0.0

    @property
    def updated_at(self) -> float:
        return self._updated_at

    def snapshot(self) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
        return list(self._keys), dict(self._contexts)

    def refresh(self) -> int:
        if _arb_only_mode():
            keys, contexts = discover_matchbook_only()
        else:
            keys, contexts = discover_upcoming()
            if not keys:
                manual = os.environ.get("WATCHLIST_FIXTURES", "").strip()
                if manual:
                    keys, contexts = parse_fixture_spec(manual)
                    log.info("watchlist using manual WATCHLIST_FIXTURES (%d fixtures)", len(keys))
        self._keys = keys
        self._contexts = contexts
        self._updated_at = time.time()
        return len(keys)

    def touch_heartbeat(self, path: str = "/tmp/fve_worker_heartbeat") -> None:
        try:
            Path(path).write_text(str(int(time.time())), encoding="utf-8")
        except OSError:
            pass
