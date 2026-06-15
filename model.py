"""Pure prediction model for the Football Value Engine (no Streamlit / no I/O).

Institutional default: bivariate Poisson + Dixon–Coles on a joint scoreline matrix;
1X2, Over 2.5 and BTTS are derivative sums (cannot contradict). Set
``FVE_PRICING_MODE=independent`` for the legacy independent-Poisson grid.

Importable + unit-testable without a running app.
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, Optional, Tuple

from pricing.score_matrix import PricingConfig, build_score_matrix, derive_market_probs, institutional_mode_enabled
from pricing.time_decay import blend_decay_with_aggregate

# League-average goals per team per game; only used as a gentle prior fallback.
DEFAULT_GOALS_PRIOR = 1.35
# Pseudo-games for venue shrinkage: with few venue games, lean on overall form.
VENUE_SHRINKAGE_GAMES = 4.0
MAX_GOALS_GRID = 10
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
        team_name = str((team.get("team") or {}).get("name") or "")

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
            "name": team_name,
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
    """Per-game goals `kind` ('for'|'against') at `venue` ('home'|'away'), shrunk to overall."""
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


def _time_decay_half_life() -> float:
    try:
        return max(7.0, float(os.environ.get("FVE_TIME_DECAY_HALF_LIFE_DAYS", "45")))
    except ValueError:
        return 45.0


def _time_decay_enabled() -> bool:
    return os.environ.get("FVE_TIME_DECAY", "1").strip().lower() not in ("0", "false", "no", "off")


def expected_goals(
    home: Dict[str, Any], away: Dict[str, Any], *, use_xg: bool = True, alpha: float = XG_BLEND_ALPHA
) -> Tuple[float, float]:
    """Expected goals (home, away) from venue-aware attack/defence blends + optional time decay."""
    rate_fn = (lambda t, v, k: blended_rate(t, v, k, alpha=alpha)) if use_xg else venue_rate
    hl = _time_decay_half_life()
    use_decay = _time_decay_enabled()

    def _rate(team: Dict[str, Any], venue: str, kind: str) -> float:
        base = rate_fn(team, venue, kind)
        if use_decay:
            return blend_decay_with_aggregate(team, venue, kind, base, half_life_days=hl)
        return base

    eh = (_rate(home, "home", "for") + _rate(away, "away", "against")) / 2.0
    ea = (_rate(away, "away", "for") + _rate(home, "home", "against")) / 2.0
    return (min(max(eh, 0.05), 6.0), min(max(ea, 0.05), 6.0))


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _score_grid_independent(eh: float, ea: float, max_goals: int):
    ph = [_poisson_pmf(i, eh) for i in range(max_goals + 1)]
    pa = [_poisson_pmf(j, ea) for j in range(max_goals + 1)]
    return ph, pa


def _market_probs_from_lambdas(
    eh: float,
    ea: float,
    *,
    max_goals: int = MAX_GOALS_GRID,
    use_xg: bool = True,
) -> Dict[str, float]:
    if institutional_mode_enabled():
        cfg = PricingConfig.from_env()
        matrix = build_score_matrix(eh, ea, config=cfg)
        return derive_market_probs(matrix)

    ph, pa = _score_grid_independent(eh, ea, max_goals)
    home_p = draw_p = away_p = 0.0
    over25 = 0.0
    btts = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = ph[i] * pa[j]
            if i > j:
                home_p += p
            elif i == j:
                draw_p += p
            else:
                away_p += p
            if i + j >= 3:
                over25 += p
            if i >= 1 and j >= 1:
                btts += p
    total = home_p + draw_p + away_p or 1.0
    return {
        "Home": home_p / total,
        "Draw": draw_p / total,
        "Away": away_p / total,
        "Over2.5": min(max(over25, 0.01), 0.99),
        "BTTS": min(max(btts, 0.01), 0.99),
    }


def match_model(
    home: Dict[str, Any], away: Dict[str, Any], *, max_goals: int = MAX_GOALS_GRID, use_xg: bool = True
) -> Dict[str, float]:
    """1X2 probabilities from joint score matrix (institutional) or independent Poisson."""
    eh, ea = expected_goals(home, away, use_xg=use_xg)
    probs = _market_probs_from_lambdas(eh, ea, max_goals=max_goals, use_xg=use_xg)
    return {"Home": probs["Home"], "Draw": probs["Draw"], "Away": probs["Away"]}


def goal_model(
    home: Dict[str, Any], away: Dict[str, Any], *, max_goals: int = MAX_GOALS_GRID, use_xg: bool = True
) -> Dict[str, float]:
    """Over 2.5 and BTTS from the same score matrix as 1X2."""
    eh, ea = expected_goals(home, away, use_xg=use_xg)
    probs = _market_probs_from_lambdas(eh, ea, max_goals=max_goals, use_xg=use_xg)
    return {"Over2.5": probs["Over2.5"], "BTTS": probs["BTTS"]}


def full_market_probs(
    home: Dict[str, Any], away: Dict[str, Any], *, use_xg: bool = True
) -> Dict[str, float]:
    """All derivative markets from one λ pair — for cross-market checks."""
    eh, ea = expected_goals(home, away, use_xg=use_xg)
    return _market_probs_from_lambdas(eh, ea, use_xg=use_xg)


def extract_best(odds_json: Dict[str, Any], *, event_label: str = "") -> Dict[str, Dict[str, Any]]:
    """Best decimal odds per market across bookmakers (line shopping)."""
    from odds_shopping import extract_best as _extract_best

    return _extract_best(odds_json, event_label=event_label)


def edge(prob: float, odds: float) -> float:
    """Expected value % of a unit stake at `odds` given model `prob`."""
    return (prob * odds - 1.0) * 100.0


def kelly(prob: float, odds: float) -> float:
    """Kelly fraction of bankroll (0 when no edge)."""
    if odds <= 1.0:
        return 0.0
    return max((prob * odds - 1.0) / (odds - 1.0), 0.0)
