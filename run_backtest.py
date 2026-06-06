"""Headless backtest runner — score the engine vs the market on recent finished fixtures.

Usage:
    API_SPORTS_KEY=... python run_backtest.py --days 10 --leagues 39,140 --min-edge 2.5

No Streamlit; safe to run in CI / a terminal. Fetches finished fixtures + odds, runs
the Poisson model (optionally xG-blended), de-vigs the market, and reports Brier / log
loss / accuracy for model vs market plus a flat-stake value-bet ROI.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import requests

import backtest as bt
from model import edge, expected_goals, extract_best, goal_model, match_model, parse_standings
from xg_sources import attach_xg, fetch_understat_team_xg

BASE_URL = "https://v3.football.api-sports.io"
FINISHED = {"FT", "AET", "PEN"}
TIMEOUT = 15


def _key() -> str:
    return (os.environ.get("API_SPORTS_KEY") or os.environ.get("API_FOOTBALL_KEY") or "").strip()


def _get(path: str, params: Dict[str, Any], key: str) -> Dict[str, Any]:
    try:
        r = requests.get(f"{BASE_URL}/{path}", headers={"x-apisports-key": key}, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()
        return d if isinstance(d, dict) else {}
    except (requests.RequestException, ValueError):
        return {}


def current_season() -> int:
    t = datetime.now(timezone.utc)
    return t.year if t.month >= 7 else t.year - 1


def run(days: int, leagues: List[int], season: int, min_edge: float, use_xg: bool, with_odds: bool) -> Dict[str, Any]:
    key = _key()
    if not key:
        print("ERROR: set API_SPORTS_KEY", file=sys.stderr)
        sys.exit(2)

    records: List[Dict[str, Any]] = []
    bets: List[Dict[str, Any]] = []
    calls = 0

    xg_matched = 0
    for league_id in leagues:
        standings = parse_standings(_get("standings", {"league": league_id, "season": season}, key))
        calls += 1
        if use_xg:
            standings, m = attach_xg(standings, fetch_understat_team_xg(league_id, season))
            xg_matched += m
        for i in range(1, days + 1):
            date = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
            fixtures = _get("fixtures", {"league": league_id, "date": date}, key)
            calls += 1
            for f in fixtures.get("response", []):
                try:
                    if f["fixture"]["status"]["short"] not in FINISHED:
                        continue
                    fid = f["fixture"]["id"]
                    home_id, away_id = f["teams"]["home"]["id"], f["teams"]["away"]["id"]
                    hg, ag = int(f["goals"]["home"]), int(f["goals"]["away"])
                except (KeyError, TypeError, ValueError):
                    continue
                if home_id not in standings or away_id not in standings:
                    continue

                home, away = standings[home_id], standings[away_id]
                probs = match_model(home, away, use_xg=use_xg)
                outcome = bt.settle_1x2(hg, ag)
                rec: Dict[str, Any] = {"probs": probs, "outcome": outcome}

                if with_odds:
                    best = extract_best(_get("odds", {"fixture": fid}, key))
                    calls += 1
                    market = bt.implied_probs_1x2(
                        best["Home"]["odds"], best["Draw"]["odds"], best["Away"]["odds"]
                    )
                    if market:
                        rec["market_probs"] = market
                    for sel in ("Home", "Draw", "Away"):
                        odds = best[sel]["odds"]
                        if odds > 1.0 and edge(probs[sel], odds) >= min_edge:
                            bets.append({"won": sel == outcome, "odds": odds, "stake": 1.0})
                records.append(rec)

    out: Dict[str, Any] = {
        "params": {"days": days, "leagues": leagues, "season": season, "use_xg": use_xg,
                   "with_odds": with_odds, "min_edge": min_edge, "api_calls": calls,
                   "xg_teams_matched": xg_matched},
        "calibration": bt.evaluate(records),
    }
    if with_odds:
        out["vs_market"] = bt.evaluate_vs_market(records)
        out["value_roi"] = bt.roi_backtest(bets)
    return out


def _pois_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _grid_1x2(eh: float, ea: float, mg: int = 8) -> Dict[str, float]:
    h = d = a = 0.0
    for i in range(mg + 1):
        for j in range(mg + 1):
            p = _pois_pmf(i, eh) * _pois_pmf(j, ea)
            if i > j:
                h += p
            elif i == j:
                d += p
            else:
                a += p
    s = h + d + a or 1.0
    return {"Home": h / s, "Draw": d / s, "Away": a / s}


def _sample_pois(lam: float, rng: random.Random) -> int:
    L = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


def simulate(n: int, *, seed: int, min_edge: float, vig: float, market_noise: float, seen_games: int = 12) -> Dict[str, Any]:
    """Validate engine + backtest on synthetic ground truth.

    True per-game rates generate the actual score; the model only sees a NOISY
    season (``seen_games`` games) — realistic estimation error. The market is the
    de-vigged TRUE probabilities (+optional noise): noise=0 is a sharp book, higher
    noise is a soft book. Honest read of whether the model adds value.
    """
    rng = random.Random(seed)
    records: List[Dict[str, Any]] = []
    bets: List[Dict[str, Any]] = []

    def season_stats(att: float, dfn: float) -> Dict[str, Any]:
        gf = sum(_sample_pois(att, rng) for _ in range(seen_games))
        ga = sum(_sample_pois(dfn, rng) for _ in range(seen_games))
        return {
            "played": seen_games, "goals_for": gf, "goals_against": ga,
            "home_played": seen_games // 2, "home_goals_for": gf // 2, "home_goals_against": ga // 2,
            "away_played": seen_games - seen_games // 2,
            "away_goals_for": gf - gf // 2, "away_goals_against": ga - ga // 2,
        }

    for _ in range(n):
        h_att, h_def = rng.uniform(0.8, 2.3), rng.uniform(0.7, 1.8)
        a_att, a_def = rng.uniform(0.7, 2.0), rng.uniform(0.8, 1.9)
        eh, ea = (h_att + a_def) / 2.0, (a_att + h_def) / 2.0  # true expected goals
        home, away = season_stats(h_att, h_def), season_stats(a_att, a_def)

        probs = match_model(home, away, use_xg=False)
        true_p = _grid_1x2(eh, ea)
        hg, ag = _sample_pois(eh, rng), _sample_pois(ea, rng)
        outcome = bt.settle_1x2(hg, ag)

        mk = {k: max(true_p[k] + rng.uniform(-market_noise, market_noise), 0.01) for k in bt.OUTCOMES}
        s = sum(mk.values())
        market = {k: v / s for k, v in mk.items()}
        records.append({"probs": probs, "market_probs": market, "outcome": outcome})

        for sel in bt.OUTCOMES:
            # Punter sees the MARKET's odds (with vig). A soft/noisy book misprices some
            # legs, which the truth-closer model can exploit; a sharp book leaves only -vig.
            offered = (1.0 / market[sel]) / (1.0 + vig)
            if offered > 1.0 and edge(probs[sel], offered) >= min_edge:
                bets.append({"won": sel == outcome, "odds": offered, "stake": 1.0})

    return {
        "params": {"mode": "simulate", "n": n, "seed": seed, "min_edge": min_edge,
                   "vig": vig, "market_noise": market_noise, "seen_games": seen_games},
        "calibration": bt.evaluate(records),
        "vs_market": bt.evaluate_vs_market(records),
        "value_roi": bt.roi_backtest(bets),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--leagues", type=str, default="39,140,135,78,61")  # EPL, La Liga, Serie A, Bundesliga, Ligue 1
    ap.add_argument("--season", type=int, default=current_season())
    ap.add_argument("--min-edge", type=float, default=2.5)
    ap.add_argument("--use-xg", action="store_true")
    ap.add_argument("--with-odds", action="store_true", help="fetch odds (1 call/fixture) for market + ROI")
    ap.add_argument("--simulate", type=int, default=0, help="run N synthetic fixtures (no API) for engine validation")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--vig", type=float, default=0.05)
    ap.add_argument("--market-noise", type=float, default=0.0, help="0 = sharp book; higher = softer book")
    args = ap.parse_args()

    if args.simulate > 0:
        print(json.dumps(
            simulate(args.simulate, seed=args.seed, min_edge=args.min_edge,
                     vig=args.vig, market_noise=args.market_noise), indent=2))
        return

    leagues = [int(x) for x in args.leagues.split(",") if x.strip()]
    print(json.dumps(run(args.days, leagues, args.season, args.min_edge, args.use_xg, args.with_odds), indent=2))


if __name__ == "__main__":
    main()
