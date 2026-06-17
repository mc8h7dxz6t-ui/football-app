"""Tests for FVE backtest health slice."""

from __future__ import annotations

import json
from pathlib import Path


def test_backtest_health_slice_from_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps(
            [
                {
                    "probs": {"Home": 0.5, "Draw": 0.25, "Away": 0.25},
                    "outcome": "Home",
                },
                {
                    "probs": {"Home": 0.4, "Draw": 0.3, "Away": 0.3},
                    "outcome": "Away",
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FVE_BACKTEST_CACHE", str(cache))
    from pipeline.backtest_health import backtest_health_slice

    backtest_health_slice(force=True)
    out = backtest_health_slice(force=True)
    assert out["available"] is True
    assert out["n"] == 2
    assert out.get("brier_score") is not None
