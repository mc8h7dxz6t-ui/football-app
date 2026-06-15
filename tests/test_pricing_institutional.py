"""Institutional pricing — bivariate Poisson, Dixon–Coles, coherent derivatives."""

import model
from pricing.score_matrix import PricingConfig, build_score_matrix, derive_market_probs
from pricing.time_decay import decay_weight, rate_from_recent_matches


def test_dixon_coles_inflates_low_draws_vs_independent():
    cfg_dc = PricingConfig(max_goals=8, use_bivariate=True, use_dixon_coles=True, dixon_coles_rho=-0.13)
    cfg_plain = PricingConfig(max_goals=8, use_bivariate=False, use_dixon_coles=False)
    lam_h, lam_a = 1.1, 1.0
    p_dc = derive_market_probs(build_score_matrix(lam_h, lam_a, config=cfg_dc))
    p_in = derive_market_probs(build_score_matrix(lam_h, lam_a, config=cfg_plain))
    assert p_dc["Draw"] > p_in["Draw"]


def test_derivatives_are_coherent():
    cfg = PricingConfig(max_goals=10, use_bivariate=True, use_dixon_coles=True)
    matrix = build_score_matrix(1.4, 1.2, config=cfg)
    probs = derive_market_probs(matrix)
    assert abs(probs["Home"] + probs["Draw"] + probs["Away"] - 1.0) < 1e-6
    assert 0.0 < probs["Over2.5"] < 1.0
    assert 0.0 < probs["BTTS"] < 1.0


def test_bivariate_changes_btts_vs_independent():
    cfg_biv = PricingConfig(use_bivariate=True, use_dixon_coles=False, shared_frac=0.25)
    cfg_ind = PricingConfig(use_bivariate=False, use_dixon_coles=False)
    lam_h, lam_a = 1.3, 1.1
    btts_biv = derive_market_probs(build_score_matrix(lam_h, lam_a, config=cfg_biv))["BTTS"]
    btts_ind = derive_market_probs(build_score_matrix(lam_h, lam_a, config=cfg_ind))["BTTS"]
    assert btts_biv != btts_ind


def test_time_decay_weights_recent_matches_more():
    matches = [
        {"goals_for": 3, "goals_against": 0, "days_ago": 3},
        {"goals_for": 0, "goals_against": 2, "days_ago": 120},
    ]
    r = rate_from_recent_matches(matches, kind="for", half_life_days=30.0)
    assert r is not None and r > 1.0


def test_decay_weight_monotonic():
    assert decay_weight(0, half_life_days=45) > decay_weight(90, half_life_days=45)


def test_full_market_probs_match_split_models():
    home = {
        "played": 20,
        "goals_for": 30,
        "goals_against": 15,
        "home_played": 10,
        "home_goals_for": 18,
        "home_goals_against": 5,
        "away_played": 10,
        "away_goals_for": 12,
        "away_goals_against": 10,
    }
    away = {
        "played": 20,
        "goals_for": 20,
        "goals_against": 25,
        "home_played": 10,
        "home_goals_for": 12,
        "home_goals_against": 14,
        "away_played": 10,
        "away_goals_for": 8,
        "away_goals_against": 11,
    }
    full = model.full_market_probs(home, away, use_xg=False)
    m = model.match_model(home, away, use_xg=False)
    g = model.goal_model(home, away, use_xg=False)
    assert abs(full["Home"] - m["Home"]) < 1e-9
    assert abs(full["Over2.5"] - g["Over2.5"]) < 1e-9
