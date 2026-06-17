"""GPU / numpy Monte Carlo for in-play 1X2 (I2 sanity path)."""

from __future__ import annotations

import os
from typing import Dict


def simulate_1x2(home_lambda: float, away_lambda: float, *, paths: int = 100_000) -> Dict[str, float]:
    solver = (os.getenv("HIBS_INPLAY_SOLVER") or "auto").strip().lower()
    n = int(os.getenv("HIBS_INPLAY_MC_PATHS", str(paths)))
    try:
        import numpy as np

        hg = np.random.poisson(max(0.01, home_lambda), n)
        ag = np.random.poisson(max(0.01, away_lambda), n)
        home = float(np.mean(hg > ag))
        away = float(np.mean(hg < ag))
        draw = float(np.mean(hg == ag))
        return {"home": round(home, 5), "draw": round(draw, 5), "away": round(away, 5)}
    except Exception:
        # Minimal fallback
        total = max(0.01, home_lambda + away_lambda)
        h = home_lambda / total
        a = away_lambda / total
        d = max(0.0, 1.0 - h - a)
        s = h + d + a or 1.0
        return {"home": round(h / s, 5), "draw": round(d / s, 5), "away": round(a / s, 5)}
