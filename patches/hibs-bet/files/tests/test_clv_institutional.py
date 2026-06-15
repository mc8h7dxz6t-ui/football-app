"""Tests for institutional CLV (fair close, edge_clv_pct, mu_clv_log)."""

from __future__ import annotations

import math

from hibs_predictor.clv_institutional import (
    compute_edge_clv_pct,
    compute_mu_clv_log,
    enrich_clv_institutional_fields,
    fair_closing_1x2_odds,
)


def test_fair_closing_odds_devigs_margin():
    closing = {"home": 1.95, "draw": 3.5, "away": 4.0}
    fair = fair_closing_1x2_odds(closing)
    assert fair["home"] is not None
    assert fair["home"] > 1.95


def test_institutional_example_edge():
    # Taken 2.10, raw close home 1.95 — fair close = 1.95 * (1+M) > raw
    closing = {"home": 1.95, "draw": 3.5, "away": 4.0}
    fair = fair_closing_1x2_odds(closing)
    assert fair["home"] is not None
    assert fair["home"] > 1.95
    edge = compute_edge_clv_pct(2.10, fair["home"])
    assert edge is not None
    assert edge > 0
    mu = compute_mu_clv_log(2.10, fair["home"])
    assert mu is not None
    assert mu > 0
    assert abs(mu - math.log(2.10 / fair["home"])) < 1e-4


def test_enrich_clv_institutional_fields():
    clv = {"clv_pp": 3.5}
    closing = {"home": 2.0, "draw": 3.4, "away": 3.8}
    out = enrich_clv_institutional_fields(
        clv,
        closing,
        stake_outcome="home",
        odds_taken=2.1,
    )
    assert out["closing_odds_1x2_fair"]["home"] is not None
    assert out["edge_clv_pct"] is not None
    assert out["mu_clv_log"] is not None
