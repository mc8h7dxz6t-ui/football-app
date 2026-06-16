"""Attack/defence ratings pipeline → Poisson λ (institutional strength model)."""

from __future__ import annotations

import os
from typing import Any, Dict, Tuple

from pricing.league_calibration import league_profile
from pricing.time_decay import blend_decay_with_aggregate

RATING_SHRINK_GAMES = 6.0


def _attack_defence_enabled() -> bool:
    return os.environ.get("FVE_ATTACK_DEFENCE", "1").strip().lower() not in ("0", "false", "no", "off")


def _venue_gf_ga(team: Dict[str, Any], venue: str) -> Tuple[float, float, int]:
    played = int(team.get(f"{venue}_played") or 0)
    gf = int(team.get(f"{venue}_goals_for") or 0)
    ga = int(team.get(f"{venue}_goals_against") or 0)
    if played <= 0:
        played = max(int(team.get("played") or 0), 0)
        gf = int(team.get("goals_for") or 0)
        ga = int(team.get("goals_against") or 0)
    return float(gf), float(ga), played


def _xg_gf_ga(team: Dict[str, Any], venue: str) -> Tuple[float | None, float | None, int]:
    vp = int(team.get(f"{venue}_xg_played") or team.get("xg_played") or 0)
    if vp <= 0:
        return None, None, 0
    xgf = float(team.get(f"{venue}_xg_for") or team.get("xg_for") or 0.0)
    xga = float(team.get(f"{venue}_xg_against") or team.get("xg_against") or 0.0)
    return xgf, xga, vp


def team_attack_rating(
    team: Dict[str, Any],
    venue: str,
    *,
    league_goals: float,
    use_xg: bool = True,
    xg_alpha: float = 0.6,
    half_life_days: float = 45.0,
    use_decay: bool = True,
) -> float:
    """Attack strength relative to league average (>1 = strong)."""
    gf, _, played = _venue_gf_ga(team, venue)
    rate = gf / played if played > 0 else league_goals
    if use_xg:
        xgf, _, xp = _xg_gf_ga(team, venue)
        if xp > 0 and xgf is not None:
            xg_rate = xgf / xp
            rate = xg_alpha * xg_rate + (1.0 - xg_alpha) * rate
    if use_decay:
        rate = blend_decay_with_aggregate(team, venue, "for", rate, half_life_days=half_life_days)
    raw = rate / league_goals if league_goals > 0 else 1.0
    weight = played / (played + RATING_SHRINK_GAMES) if played > 0 else 0.0
    return max(0.55, min(1.85, weight * raw + (1.0 - weight) * 1.0))


def team_defence_rating(
    team: Dict[str, Any],
    venue: str,
    *,
    league_goals: float,
    use_xg: bool = True,
    xg_alpha: float = 0.6,
    half_life_days: float = 45.0,
    use_decay: bool = True,
) -> float:
    """Defence strength as goals conceded rate relative to league (>1 = leaky)."""
    _, ga, played = _venue_gf_ga(team, venue)
    rate = ga / played if played > 0 else league_goals
    if use_xg:
        _, xga, xp = _xg_gf_ga(team, venue)
        if xp > 0 and xga is not None:
            xg_rate = xga / xp
            rate = xg_alpha * xg_rate + (1.0 - xg_alpha) * rate
    if use_decay:
        rate = blend_decay_with_aggregate(team, venue, "against", rate, half_life_days=half_life_days)
    raw = rate / league_goals if league_goals > 0 else 1.0
    weight = played / (played + RATING_SHRINK_GAMES) if played > 0 else 0.0
    return max(0.55, min(1.85, weight * raw + (1.0 - weight) * 1.0))


def expected_goals_from_ratings(
    home: Dict[str, Any],
    away: Dict[str, Any],
    *,
    league_code: str = "",
    use_xg: bool = True,
    xg_alpha: float = 0.6,
    half_life_days: float = 45.0,
    use_decay: bool = True,
) -> Tuple[float, float, Dict[str, Any]]:
    """
    λ_h = league_gpt × home_attack × away_defence × home_advantage
    λ_a = league_gpt × away_attack × home_defence
    """
    prof = league_profile(league_code)
    lg = prof["goals_per_team"]
    ha = prof["home_advantage"]

    h_att = team_attack_rating(home, "home", league_goals=lg, use_xg=use_xg, xg_alpha=xg_alpha, half_life_days=half_life_days, use_decay=use_decay)
    h_def = team_defence_rating(home, "home", league_goals=lg, use_xg=use_xg, xg_alpha=xg_alpha, half_life_days=half_life_days, use_decay=use_decay)
    a_att = team_attack_rating(away, "away", league_goals=lg, use_xg=use_xg, xg_alpha=xg_alpha, half_life_days=half_life_days, use_decay=use_decay)
    a_def = team_defence_rating(away, "away", league_goals=lg, use_xg=use_xg, xg_alpha=xg_alpha, half_life_days=half_life_days, use_decay=use_decay)

    lam_h = max(0.05, min(6.0, lg * h_att * a_def * ha))
    lam_a = max(0.05, min(6.0, lg * a_att * h_def))
    meta = {
        "pipeline": "attack_defence",
        "league": prof,
        "home_attack": round(h_att, 3),
        "home_defence": round(h_def, 3),
        "away_attack": round(a_att, 3),
        "away_defence": round(a_def, 3),
    }
    return lam_h, lam_a, meta


def attack_defence_enabled() -> bool:
    return _attack_defence_enabled()
