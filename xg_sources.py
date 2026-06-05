"""Backup xG providers for the Football Value Engine.

Primary xG would come from API-Football's fixture statistics (quota-heavy: one call
per fixture). This module adds a **free backup**: season xG aggregated from
[Understat](https://understat.com) via the maintained `soccerdata` scraper, mapped
into the model's xG fields (`xg_for`/`xg_against`/`xg_played` + home/away splits) and
matched to standings rows by normalised team name.

Coverage: Understat = the big-5 leagues (EPL, La Liga, Bundesliga, Serie A, Ligue 1).
Other leagues degrade gracefully to goals-only (the model already handles missing xG).
For broader coverage (Championship, Eredivisie, Scottish Prem, …) `soccerdata.FBref`
(StatsBomb xG) is the next step but needs a browser/anti-scrape layer — see README.

The aggregation/normalisation/merge helpers are pure and unit-tested; only
`fetch_understat_team_xg` touches the network (and lazily imports soccerdata).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, Tuple

# API-Football league id -> soccerdata Understat league name.
UNDERSTAT_LEAGUE_BY_ID: Dict[int, str] = {
    39: "ENG-Premier League",
    140: "ESP-La Liga",
    78: "GER-Bundesliga",
    135: "ITA-Serie A",
    61: "FRA-Ligue 1",
}

_SUFFIX_TOKENS = {"fc", "afc", "cf", "sc", "ac", "club", "calcio", "ssd", "us", "as"}


def normalize_team(name: str) -> str:
    """Loose key for cross-source name matching (strip accents, punctuation, suffixes)."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    tokens = [t for t in s.split() if t and t not in _SUFFIX_TOKENS]
    return "".join(tokens)


def _blank() -> Dict[str, float]:
    return {
        "xg_played": 0, "xg_for": 0.0, "xg_against": 0.0,
        "home_xg_played": 0, "home_xg_for": 0.0, "home_xg_against": 0.0,
        "away_xg_played": 0, "away_xg_for": 0.0, "away_xg_against": 0.0,
    }


def aggregate_team_xg(matches: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Aggregate per-match xG into season totals keyed by normalised team name.

    Each match dict needs: home_team, away_team, home_xg, away_xg.
    """
    out: Dict[str, Dict[str, float]] = {}
    for m in matches:
        try:
            home, away = str(m["home_team"]), str(m["away_team"])
            hxg, axg = float(m["home_xg"]), float(m["away_xg"])
        except (KeyError, TypeError, ValueError):
            continue
        hk, ak = normalize_team(home), normalize_team(away)
        if not hk or not ak:
            continue
        h = out.setdefault(hk, _blank())
        a = out.setdefault(ak, _blank())
        # home team
        h["xg_played"] += 1; h["xg_for"] += hxg; h["xg_against"] += axg
        h["home_xg_played"] += 1; h["home_xg_for"] += hxg; h["home_xg_against"] += axg
        # away team
        a["xg_played"] += 1; a["xg_for"] += axg; a["xg_against"] += hxg
        a["away_xg_played"] += 1; a["away_xg_for"] += axg; a["away_xg_against"] += hxg
    return out


def attach_xg(
    standings: Dict[int, Dict[str, Any]], xg_by_name: Dict[str, Dict[str, float]]
) -> Tuple[Dict[int, Dict[str, Any]], int]:
    """Merge xG stats into standings rows by normalised name. Returns (new_standings, n_matched)."""
    merged: Dict[int, Dict[str, Any]] = {}
    matched = 0
    for tid, row in standings.items():
        new_row = dict(row)
        xg = xg_by_name.get(normalize_team(row.get("name", "")))
        if xg:
            new_row.update(xg)
            matched += 1
        merged[tid] = new_row
    return merged, matched


def fetch_understat_team_xg(api_league_id: int, season: int) -> Dict[str, Dict[str, float]]:
    """Season xG per team from Understat (via soccerdata). {} if unsupported/unavailable."""
    league = UNDERSTAT_LEAGUE_BY_ID.get(int(api_league_id))
    if not league:
        return {}
    try:
        import soccerdata as sd  # lazy: heavy optional dep
    except ImportError:
        return {}
    try:
        us = sd.Understat(leagues=league, seasons=str(season))
        df = us.read_team_match_stats().reset_index()
        cols = {"home_team", "away_team", "home_xg", "away_xg"}
        if not cols.issubset(df.columns):
            return {}
        matches = df[["home_team", "away_team", "home_xg", "away_xg"]].to_dict("records")
        return aggregate_team_xg(matches)
    except Exception:
        return {}
