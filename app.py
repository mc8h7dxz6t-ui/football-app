import streamlit as st
import requests
import pandas as pd
import math
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

API_KEY = "1fd110142ef284957b9f852d0290b080"

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

BASE_URL = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_KEY
}

# ======================
# HELPERS
# ======================
def get_fixtures(league_id: int, date: str):
    url = f"{BASE_URL}/fixtures"
    params = {"league": league_id, "date": date}
    return requests.get(url, headers=HEADERS, params=params).json()

def get_odds(fixture_id: int):
    url = f"{BASE_URL}/odds"
    params = {"fixture": fixture_id}
    return requests.get(url, headers=HEADERS, params=params).json()

def get_standings(league_id: int):
    url = f"{BASE_URL}/standings"
    params = {"league": league_id, "season": 2024}
    res = requests.get(url, headers=HEADERS, params=params).json()

    table = {}
    try:
        for team in res["response"][0]["league"]["standings"][0]:
            table[team["team"]["id"]] = {
                "played": team["all"]["played"],
                "goals_for": team["all"]["goals"]["for"],
                "goals_against": team["all"]["goals"]["against"],
            }
    except:
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
    except:
        return best

    for b in bookmakers:
        for market in b["bets"]:
            name = market["name"].lower()

            for outcome in market["values"]:
                val = outcome["value"].lower()
                odd = float(outcome["odd"])

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
st.title("Football Value Engine")

bankroll = st.number_input("Bankroll (£)", value=1000)
min_edge = st.slider("Minimum Edge %", 0.0, 10.0, 2.5)
kelly_frac = st.slider("Kelly Fraction", 0.1, 1.0, 0.25)

st.caption("Scanning next 2 days across all selected leagues")

if st.button("Run Scan"):

    results = []

    for league_name, league_id in LEAGUES.items():
        standings = get_standings(league_id)

        for i in range(2):
            date = (datetime.today() + timedelta(days=i)).strftime("%Y-%m-%d")
            fixtures = get_fixtures(league_id, date)

            for f in fixtures.get("response", []):
                fixture_id = f["fixture"]["id"]

                home_id = f["teams"]["home"]["id"]
                away_id = f["teams"]["away"]["id"]

                if home_id not in standings or away_id not in standings:
                    continue

                odds_json = get_odds(fixture_id)
                best = extract_best(odds_json)

                home = standings[home_id]
                away = standings[away_id]

                match_probs = match_model(home, away)
                goal_probs = goal_model(home, away)

                kickoff = f["fixture"]["date"].replace("T", " ").replace("Z", "")

                # 1X2
                for sel in ["Home", "Draw", "Away"]:
                    odds = best[sel]["odds"]
                    if odds == 0:
                        continue

                    prob = match_probs[sel]
                    e = edge(prob, odds)

                    if e < min_edge:
                        continue

                    stake = bankroll * kelly(prob, odds) * kelly_frac

                    results.append([league_name, sel, odds, prob, e, stake, kickoff])

                # GOALS
                for sel in ["Over2.5", "BTTS"]:
                    odds = best[sel]["odds"]
                    if odds == 0:
                        continue

                    prob = goal_probs[sel]
                    e = edge(prob, odds)

                    if e < min_edge:
                        continue

                    stake = bankroll * kelly(prob, odds) * kelly_frac

                    results.append([league_name, sel, odds, prob, e, stake, kickoff])

    df = pd.DataFrame(results, columns=[
        "League", "Market", "Odds", "Model Prob", "Edge %", "Stake £", "Kickoff"
    ])

    st.dataframe(df.sort_values(by="Edge %", ascending=False))
