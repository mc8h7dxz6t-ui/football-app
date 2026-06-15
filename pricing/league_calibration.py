"""Per-league Dixon–Coles ρ and league strength — mirrors hibs calibration cache shape."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Tuple


def _clamp_rho(rho: float) -> float:
    return max(-0.25, min(0.05, float(rho)))


def _calibration_path() -> Path:
    raw = (os.environ.get("FVE_CALIBRATION_PATH") or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[1] / "config" / "league_calibration.json"


@lru_cache(maxsize=1)
def load_calibration() -> Dict[str, Any]:
    path = _calibration_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _normalize_league_code(league_code: str) -> str:
    return (league_code or "").strip().upper().replace(" ", "_").replace("-", "_")


def league_profile(league_code: str) -> Dict[str, Any]:
    """Resolve ρ, goals prior, home advantage for a league."""
    cal = load_calibration()
    code = _normalize_league_code(league_code)
    leagues = cal.get("leagues") if isinstance(cal.get("leagues"), dict) else {}
    row = leagues.get(code) if code and isinstance(leagues, dict) else {}
    if not isinstance(row, dict):
        row = {}

    try:
        default_rho = float(cal.get("default_rho", os.environ.get("FVE_DIXON_COLES_RHO", "-0.10")))
    except (TypeError, ValueError):
        default_rho = -0.10
    try:
        default_gpt = float(cal.get("default_goals_per_team", 1.35))
    except (TypeError, ValueError):
        default_gpt = 1.35
    try:
        default_ha = float(cal.get("default_home_advantage", 1.08))
    except (TypeError, ValueError):
        default_ha = 1.08

    rho = _clamp_rho(float(row.get("rho", default_rho)))
    goals = float(row.get("goals_per_team", default_gpt))
    home_adv = float(row.get("home_advantage", default_ha))
    return {
        "league_code": code or "DEFAULT",
        "rho": rho,
        "goals_per_team": max(0.8, min(2.0, goals)),
        "home_advantage": max(1.0, min(1.25, home_adv)),
        "source": "cache" if code and code in leagues else "default",
    }


def league_rho_for(league_code: str) -> Tuple[float, Dict[str, Any]]:
    prof = league_profile(league_code)
    return prof["rho"], {"source": prof["source"], "rho": prof["rho"], "league": prof["league_code"]}
