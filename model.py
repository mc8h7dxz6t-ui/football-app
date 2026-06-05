"""Pure prediction model for the Football Value Engine (no Streamlit / no I/O).

Coherent independent-Poisson model: 1X2, Over 2.5 and BTTS are all derived from the
SAME pair of expected goals, so the markets cannot contradict each other. Expected
goals use **venue splits** (home team's home form, away team's away form) with
**shrinkage** toward overall form when the venue sample is thin.

Importable + unit-testable without a running app.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

# League-average goals per team per game; only used as a gentle prior fallback.
DEFAULT_GOALS_PRIOR = 1.35
# Pseudo-games for venue shrinkage: with few venue games, lean on overall form.
VENUE_SHRINKAGE_GAMES = 4.0
MAX_GOALS_GRID = 8
# Weight on the xG signal when blending with actual goals (xG is more stable/predictive).
XG_BLEND_ALPHA = 0.6


def parse_standings(res: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """Extract overall + home + away goal/played stats per team id from a standings payload."""
    table: Dict[int, Dict[str, Any]] = {}
    try:
        rows = res["response"][0]["league"]["standings"][0]
    except (KeyError, IndexError, TypeError):
        return table

    for team in rows:
        try:
            tid = team["team"]["id"]
        except (KeyError, TypeError):
            continue

        def _split(block: Dict[str, Any]) -> Dict[str, int]:
            return {
                "played": int(block.get("played") or 0),
                "for": int((block.get("goals") or {}).get("for") or 0),
                "against": int((block.get("goals") or {}).get("against") or 0),
            }

        all_ = _split(team.get("all") or {})
        home = _split(team.get("home") or {})
        away = _split(team.get("away") or {})
        table[tid] = {
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
    return table


def _overall_rate(team: Dict[str, Any], kind: str) -> float:
    played = max(int(team.get("played") or 0), 0)
    goals = int(team.get(f"goals_{kind}") or 0)
    if played <= 0:
        return DEFAULT_GOALS_PRIOR
    return goals / played


def venue_rate(team: Dict[str, Any], venue: str, kind: str) -> float:
    """Per-game goals `kind` ('for'|'against') at `venue` ('home'|'away'), shrunk to overall.

    Falls back gracefully to overall form (and a league prior) when venue splits are
    absent or the venue sample is small.
    """
    overall = _overall_rate(team, kind)
    vp = int(team.get(f"{venue}_played") or 0)
    if vp <= 0:
        return overall
    vg = int(team.get(f"{venue}_goals_{kind}") or 0)
    venue_value = vg / vp
    weight = vp / (vp + VENUE_SHRINKAGE_GAMES)
    return weight * venue_value + (1.0 - weight) * overall


def venue_rate_xg(team: Dict[str, Any], venue: str, kind: str) -> Optional[float]:
    """Per-game xG `kind` at `venue`, shrunk to overall xG. None when no xG data."""
    op = int(team.get("xg_played") or 0)
    if op <= 0:
        return None
    overall = float(team.get(f"xg_{kind}") or 0.0) / op
    vp = int(team.get(f"{venue}_xg_played") or 0)
    if vp <= 0:
        return overall
    venue_value = float(team.get(f"{venue}_xg_{kind}") or 0.0) / vp
    weight = vp / (vp + VENUE_SHRINKAGE_GAMES)
    return weight * venue_value + (1.0 - weight) * overall


def blended_rate(team: Dict[str, Any], venue: str, kind: str, *, alpha: float = XG_BLEND_ALPHA) -> float:
    """Blend the xG rate (if present) with the actual-goals rate; goals-only if no xG."""
    goals = venue_rate(team, venue, kind)
    xg = venue_rate_xg(team, venue, kind)
    if xg is None:
        return goals
    return alpha * xg + (1.0 - alpha) * goals


def expected_goals(
    home: Dict[str, Any], away: Dict[str, Any], *, use_xg: bool = True, alpha: float = XG_BLEND_ALPHA
) -> Tuple[float, float]:
    """Expected goals (home, away) from venue-aware attack/defence blends.

    When ``use_xg`` and xG fields are present, attack/defence rates blend xG with
    actual goals (weight ``alpha`` on xG); otherwise pure actual-goals form.
    """
    rate = (lambda t, v, k: blended_rate(t, v, k, alpha=alpha)) if use_xg else venue_rate
    eh = (rate(home, "home", "for") + rate(away, "away", "against")) / 2.0
    ea = (rate(away, "away", "for") + rate(home, "home", "against")) / 2.0
    # Keep strictly positive and bounded for a stable Poisson.
    return (min(max(eh, 0.05), 6.0), min(max(ea, 0.05), 6.0))


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _score_grid(eh: float, ea: float, max_goals: int = MAX_GOALS_GRID):
    ph = [_poisson_pmf(i, eh) for i in range(max_goals + 1)]
    pa = [_poisson_pmf(j, ea) for j in range(max_goals + 1)]
    return ph, pa


def match_model(
    home: Dict[str, Any], away: Dict[str, Any], *, max_goals: int = MAX_GOALS_GRID, use_xg: bool = True
) -> Dict[str, float]:
    """1X2 probabilities from an independent-Poisson score grid (normalised)."""
    eh, ea = expected_goals(home, away, use_xg=use_xg)
    ph, pa = _score_grid(eh, ea, max_goals)
    home_p = draw_p = away_p = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = ph[i] * pa[j]
            if i > j:
                home_p += p
            elif i == j:
                draw_p += p
            else:
                away_p += p
    total = home_p + draw_p + away_p or 1.0
    return {"Home": home_p / total, "Draw": draw_p / total, "Away": away_p / total}


def goal_model(
    home: Dict[str, Any], away: Dict[str, Any], *, max_goals: int = MAX_GOALS_GRID, use_xg: bool = True
) -> Dict[str, float]:
    """Over 2.5 and BTTS from the same expected-goals Poisson grid."""
    eh, ea = expected_goals(home, away, use_xg=use_xg)
    ph, pa = _score_grid(eh, ea, max_goals)
    over25 = 0.0
    btts = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = ph[i] * pa[j]
            if i + j >= 3:
                over25 += p
            if i >= 1 and j >= 1:
                btts += p
    return {
        "Over2.5": min(max(over25, 0.01), 0.99),
        "BTTS": min(max(btts, 0.01), 0.99),
    }


def extract_best(odds_json: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """Best decimal odds per market across bookmakers (line shopping)."""
    best = {
        "Home": {"odds": 0.0},
        "Draw": {"odds": 0.0},
        "Away": {"odds": 0.0},
        "Over2.5": {"odds": 0.0},
        "BTTS": {"odds": 0.0},
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
                if "over/under" in name and "2.5" in val and "over" in val:
                    best["Over2.5"]["odds"] = max(best["Over2.5"]["odds"], odd)
                if "both teams score" in name and val == "yes":
                    best["BTTS"]["odds"] = max(best["BTTS"]["odds"], odd)
    return best


def edge(prob: float, odds: float) -> float:
    """Expected value % of a unit stake at `odds` given model `prob`."""
    return (prob * odds - 1.0) * 100.0


def kelly(prob: float, odds: float) -> float:
    """Kelly fraction of bankroll (0 when no edge)."""
    if odds <= 1.0:
        return 0.0
    return max((prob * odds - 1.0) / (odds - 1.0), 0.0)
