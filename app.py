import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pandas as pd
import requests
import streamlit as st

import backtest as bt
from model import (
    edge,
    extract_best,
    goal_model,
    kelly,
    match_model,
    parse_standings,
)

# ======================
# CONFIG
# ======================
BASE_URL = "https://v3.football.api-sports.io"
REQUEST_TIMEOUT_SEC = 15
FINISHED_STATUSES = {"FT", "AET", "PEN"}


def _get_api_key() -> str:
    key = os.environ.get("API_SPORTS_KEY") or os.environ.get("API_FOOTBALL_KEY")
    if not key:
        try:
            key = st.secrets["API_SPORTS_KEY"]  # type: ignore[index]
        except Exception:
            key = ""
    return str(key or "").strip()


API_KEY = _get_api_key()

LEAGUES = {
    "Scotland Premiership": 179,
    "Scotland Championship": 180,
    "Scotland League One": 181,
    "Scotland League Two": 182,
    "England Premier League": 39,
    "England Championship": 40,
    "Germany Bundesliga": 78,
    "Spain La Liga": 140,
    "Italy Serie A": 135,
    "France Ligue 1": 61,
    "Netherlands Eredivisie": 88,
    "Portugal Primeira Liga": 94,
    "Belgium First Division A": 144,
    "Denmark Superliga": 119,
    "Switzerland Super League": 207,
    "Sweden Allsvenskan": 113,
    "Czech First League": 345,
}


def current_season() -> int:
    today = datetime.now(timezone.utc)
    return today.year if today.month >= 7 else today.year - 1


# ======================
# API
# ======================
def _api_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
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
    return parse_standings(_api_get("standings", {"league": league_id, "season": season}))


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

with st.sidebar:
    st.header("Settings")
    bankroll = st.number_input("Bankroll (£)", min_value=0.0, value=1000.0, step=50.0)
    min_edge = st.slider("Minimum Edge %", 0.0, 10.0, 2.5)
    kelly_frac = st.slider("Kelly Fraction", 0.1, 1.0, 0.25)
    use_xg = st.checkbox("Blend xG (when available)", value=True)
    season = st.number_input("Season (start year)", min_value=2015, max_value=2100, value=current_season())
    selected_leagues = st.multiselect(
        "Leagues", options=list(LEAGUES.keys()), default=list(LEAGUES.keys())
    )

scan_tab, backtest_tab = st.tabs(["Value Scan", "Backtest"])

# ---------------------- Value Scan ----------------------
with scan_tab:
    days_ahead = st.slider("Days to scan", 1, 5, 2)
    st.caption(f"Scanning the next {days_ahead} day(s) across {len(selected_leagues)} league(s), season {season}.")

    if st.button("Run Scan", key="scan"):
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
                for f in get_fixtures(league_id, date).get("response", []):
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
                    home, away = standings[home_id], standings[away_id]
                    probs = {**match_model(home, away, use_xg=use_xg), **goal_model(home, away, use_xg=use_xg)}
                    for sel, prob in probs.items():
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

# ---------------------- Backtest ----------------------
with backtest_tab:
    st.caption(
        "Replays recent **finished** fixtures: predict 1X2, settle vs the real score, and "
        "score calibration (Brier / log loss / accuracy)."
    )
    st.warning(
        "Approximate: uses the *current* league table, so it is in-sample (mildly leaky). "
        "Treat as a sanity signal, not a forward backtest.",
        icon="⚠️",
    )
    lookback = st.slider("Look back (days)", 3, 30, 14)
    include_market = st.checkbox("Compare vs market + ROI (1 odds call per fixture)", value=False)

    if st.button("Run Backtest", key="bt"):
        if not selected_leagues:
            st.warning("Select at least one league.")
            st.stop()
        records = []
        bets = []
        progress = st.progress(0.0, text="Replaying…")
        for idx, league_name in enumerate(selected_leagues):
            league_id = LEAGUES[league_name]
            standings = get_standings(league_id, int(season))
            for i in range(1, int(lookback) + 1):
                date = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
                for f in get_fixtures(league_id, date).get("response", []):
                    try:
                        if f["fixture"]["status"]["short"] not in FINISHED_STATUSES:
                            continue
                        fid = f["fixture"]["id"]
                        home_id = f["teams"]["home"]["id"]
                        away_id = f["teams"]["away"]["id"]
                        hg = int(f["goals"]["home"])
                        ag = int(f["goals"]["away"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if home_id not in standings or away_id not in standings:
                        continue
                    probs = match_model(standings[home_id], standings[away_id], use_xg=use_xg)
                    outcome = bt.settle_1x2(hg, ag)
                    rec = {"probs": probs, "outcome": outcome}
                    if include_market:
                        best = extract_best(get_odds(fid))
                        market = bt.implied_probs_1x2(best["Home"]["odds"], best["Draw"]["odds"], best["Away"]["odds"])
                        if market:
                            rec["market_probs"] = market
                        for sel in ("Home", "Draw", "Away"):
                            odds = best[sel]["odds"]
                            if odds > 1.0 and edge(probs[sel], odds) >= min_edge:
                                bets.append({"won": sel == outcome, "odds": odds, "stake": 1.0})
                    records.append(rec)
            progress.progress((idx + 1) / len(selected_leagues), text=f"Replayed {league_name}")
        progress.empty()

        summary = bt.evaluate(records)
        if summary["n"] == 0:
            st.info("No finished fixtures with table coverage in the window.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Fixtures", summary["n"])
            c2.metric("Brier", summary["brier_score"], help=f"Uniform baseline {summary['uniform_baseline_brier']}")
            c3.metric("Log loss", summary["log_loss"])
            c4.metric("Top-pick acc", f"{summary['top_pick_accuracy_pct']}%")
            if summary["calibration"]:
                st.subheader("Calibration (top-pick)")
                st.dataframe(pd.DataFrame(summary["calibration"]), use_container_width=True)

            if include_market:
                vm = bt.evaluate_vs_market(records)
                roi = bt.roi_backtest(bets)
                st.subheader("Vs market (de-vigged)")
                if vm["n_paired"]:
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Model Brier", vm["model"]["brier_score"])
                    m2.metric("Market Brier", vm["market"]["brier_score"], help=vm["verdict"] or "")
                    m3.metric("Δ vs market", vm["brier_delta_vs_market"], help="negative = model beats market")
                else:
                    st.info("No fixtures with usable 1X2 odds for a market comparison.")
                st.subheader("Value-bet ROI (flat stake)")
                if roi["bets"]:
                    r1, r2, r3 = st.columns(3)
                    r1.metric("Bets", roi["bets"])
                    r2.metric("Hit rate", f"{roi['hit_rate_pct']}%")
                    r3.metric("ROI", f"{roi['roi_pct']}%", help=f"P&L {roi['pnl_units']} units")
                else:
                    st.info("No value bets cleared the edge threshold in the window.")

st.caption("Analytical/research tool only. Betting carries financial risk; validate before staking real money.")
