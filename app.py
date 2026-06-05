import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pandas as pd
import requests
import streamlit as st

# ======================
# CONFIG
# ======================
BASE_URL = "https://v3.football.api-sports.io"
REQUEST_TIMEOUT_SEC = 15


def _get_api_key() -> str:
    """API key from env or Streamlit secrets — never hard-coded in source."""
    key = os.environ.get("API_SPORTS_KEY") or os.environ.get("API_FOOTBALL_KEY")
    if not key:
        try:
            key = st.secrets["API_SPORTS_KEY"]  # type: ignore[index]
        except Exception:
            key = ""
    return str(key or "").strip()


API_KEY = _get_api_key()

# ======================
# LEAGUES
# ======================
LEAGUES = {
    # Scotland
    "Scotland Premiership": 179,
    "Scotland Championship": 180,
    "Scotland League One": 181,
    "Scotland League Two": 182,
    # England
    "England Premier League": 39,
    "England Championship": 40,
    # Europe
    "Germany Bundesliga": 78,
    "Spain La Liga": 140,
    "Italy Serie A": 135,
    "France Ligue 1": 61,
    "Netherlands Eredivisie": 88,
    "Portugal Primeira Liga": 94,
    "Belgium First Division A": 144,
    # Extra
    "Denmark Superliga": 119,
    "Switzerland Super League": 207,
    "Sweden Allsvenskan": 113,
    "Czech First League": 345,
}


def current_season() -> int:
    """API-Football season = the season's starting year (Aug–May campaigns)."""
    today = datetime.now(timezone.utc)
    return today.year if today.month >= 7 else today.year - 1


# ======================
# HELPERS
# ======================
def _api_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Single guarded API call: timeout, status check, JSON — never raises."""
    try:
        resp = requests.get(
            f"{BASE_URL}/{path}",
            headers={"x-apisports-key": API_KEY},
            params=params,
            timeout=REQUEST_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except (requests.RequestException, ValueError):
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def get_fixtures(league_id: int, date: str) -> Dict[str, Any]:
    return _api_get("fixtures", {"league": league_id, "date": date})


@st.cache_data(ttl=900, show_spinner=False)
def get_odds(fixture_id: int) -> Dict[str, Any]:
    return _api_get("odds", {"fixture": fixture_id})


@st.cache_data(ttl=21600, show_spinner=False)
def get_standings(league_id: int, season: int) -> Dict[int, Dict[str, Any]]:
    res = _api_get("standings", {"league": league_id, "season": season})
    table: Dict[int, Dict[str, Any]] = {}
    try:
        for team in res["response"][0]["league"]["standings"][0]:
            table[team["team"]["id"]] = {
                "played": team["all"]["played"],
                "goals_for": team["all"]["goals"]["for"],
                "goals_against": team["all"]["goals"]["against"],
            }
    except (KeyError, IndexError, TypeError):
        pass
    return table


# ======================
# MODELS
# ======================
def match_model(home, away):
    h = home["goals_for"] - home["goals_against"]
    a = away["goals_for"] - away["goals_against"]

    total = abs(h) + abs(a) + 1

    home_prob = 0.45 + (h / total) * 0.25
    away_prob = 0.45 + (a / total) * 0.25
    draw_prob = 1 - (home_prob + away_prob)

    return {
        "Home": max(0.05, min(home_prob, 0.85)),
        "Draw": max(0.05, min(draw_prob, 0.3)),
        "Away": max(0.05, min(away_prob, 0.85)),
    }


def goal_model(home, away):
    ph = max(home["played"], 1)
    pa = max(away["played"], 1)

    hgf = home["goals_for"] / ph
    hga = home["goals_against"] / ph
    agf = away["goals_for"] / pa
    aga = away["goals_against"] / pa

    eh = (hgf + aga) / 2
    ea = (agf + hga) / 2

    total = eh + ea

    over25 = 1 / (1 + math.exp(-(total - 2.5)))
    btts = (eh * ea) / ((eh * ea) + 1)

    return {
        "Over2.5": max(0.05, min(over25, 0.95)),
        "BTTS": max(0.05, min(btts, 0.95)),
    }


# ======================
# ODDS
# ======================
def extract_best(odds_json):
    best = {
        "Home": {"odds": 0},
        "Draw": {"odds": 0},
        "Away": {"odds": 0},
        "Over2.5": {"odds": 0},
        "BTTS": {"odds": 0},
    }

    try:
        bookmakers = odds_json["response"][0]["bookmakers"]
    except (KeyError, IndexError, TypeError):
        return best

    for b in bookmakers:
        for market in b.get("bets", []):
            name = str(market.get("name", "")).lower()

            for outcome in market.get("values", []):
                val = str(outcome.get("value", "")).lower()
                try:
                    odd = float(outcome.get("odd"))
                except (TypeError, ValueError):
                    continue

                if "match winner" in name:
                    if val == "home":
                        best["Home"]["odds"] = max(best["Home"]["odds"], odd)
                    elif val == "draw":
                        best["Draw"]["odds"] = max(best["Draw"]["odds"], odd)
                    elif val == "away":
                        best["Away"]["odds"] = max(best["Away"]["odds"], odd)

                if "over/under" in name and "2.5" in val:
                    if "over" in val:
                        best["Over2.5"]["odds"] = max(best["Over2.5"]["odds"], odd)

                if "both teams score" in name and val == "yes":
                    best["BTTS"]["odds"] = max(best["BTTS"]["odds"], odd)

    return best


# ======================
# EDGE + KELLY
# ======================
def edge(prob, odds):
    return (prob * odds - 1) * 100


def kelly(prob, odds):
    return max((prob * odds - 1) / (odds - 1), 0)


# ======================
# APP
# ======================
st.set_page_config(page_title="Football Value Engine", page_icon="⚽")
st.title("Football Value Engine")

if not API_KEY:
    st.error(
        "No API key found. Set `API_SPORTS_KEY` as an environment variable or in "
        "Streamlit secrets (`.streamlit/secrets.toml`). Get a free key at api-football.com."
    )
    st.stop()

bankroll = st.number_input("Bankroll (£)", min_value=0.0, value=1000.0, step=50.0)
min_edge = st.slider("Minimum Edge %", 0.0, 10.0, 2.5)
kelly_frac = st.slider("Kelly Fraction", 0.1, 1.0, 0.25)

with st.expander("Scan settings"):
    season = st.number_input("Season (start year)", min_value=2015, max_value=2100, value=current_season())
    days_ahead = st.slider("Days to scan", 1, 5, 2)
    selected_leagues = st.multiselect(
        "Leagues", options=list(LEAGUES.keys()), default=list(LEAGUES.keys())
    )

st.caption(f"Scanning the next {days_ahead} day(s) across {len(selected_leagues)} league(s), season {season}.")

if st.button("Run Scan"):
    if not selected_leagues:
        st.warning("Select at least one league.")
        st.stop()

    results = []
    progress = st.progress(0.0, text="Scanning…")

    for idx, league_name in enumerate(selected_leagues):
        league_id = LEAGUES[league_name]
        standings = get_standings(league_id, int(season))

        for i in range(int(days_ahead)):
            date = (datetime.now(timezone.utc) + timedelta(days=i)).strftime("%Y-%m-%d")
            fixtures = get_fixtures(league_id, date)

            for f in fixtures.get("response", []):
                try:
                    fixture_id = f["fixture"]["id"]
                    home_id = f["teams"]["home"]["id"]
                    away_id = f["teams"]["away"]["id"]
                    kickoff = f["fixture"]["date"].replace("T", " ").replace("Z", "")
                except (KeyError, TypeError):
                    continue

                if home_id not in standings or away_id not in standings:
                    continue

                best = extract_best(get_odds(fixture_id))
                home = standings[home_id]
                away = standings[away_id]

                match_probs = match_model(home, away)
                goal_probs = goal_model(home, away)

                for sel, prob in (
                    *((s, match_probs[s]) for s in ("Home", "Draw", "Away")),
                    *((s, goal_probs[s]) for s in ("Over2.5", "BTTS")),
                ):
                    odds = best[sel]["odds"]
                    if odds <= 1.0:
                        continue
                    e = edge(prob, odds)
                    if e < min_edge:
                        continue
                    stake = bankroll * kelly(prob, odds) * kelly_frac
                    results.append([league_name, sel, odds, prob, e, stake, kickoff])

        progress.progress((idx + 1) / len(selected_leagues), text=f"Scanned {league_name}")

    progress.empty()

    if not results:
        st.info("No value bets found for the current settings.")
    else:
        df = pd.DataFrame(
            results,
            columns=["League", "Market", "Odds", "Model Prob", "Edge %", "Stake £", "Kickoff"],
        )
        st.dataframe(df.sort_values(by="Edge %", ascending=False), use_container_width=True)

st.caption("Analytical/research tool only. Betting carries financial risk; validate before staking real money.")
