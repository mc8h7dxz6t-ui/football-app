"""Attack/defence ratings and MLE / portfolio extensions."""

import math

import model
from engine.portfolio_kelly import apply_portfolio_kelly_to_picks
from pricing.attack_defence import expected_goals_from_ratings
from pricing.league_calibration import league_rho_for
from pricing.mle_fit import fit_lambdas_mle
from pricing.ml_blend import blend_1x2_heads
from pricing.score_matrix import PricingConfig, derive_market_probs, build_score_matrix


def _team(gf, ga, played, h=None, a=None):
    h = h or (played // 2, gf // 2, ga // 2)
    a = a or (played - played // 2, gf - gf // 2, ga - ga // 2)
    return {
        "played": played,
        "goals_for": gf,
        "goals_against": ga,
        "home_played": h[0],
        "home_goals_for": h[1],
        "home_goals_against": h[2],
        "away_played": a[0],
        "away_goals_for": a[1],
        "away_goals_against": a[2],
    }


def test_league_rho_from_calibration():
    rho, dbg = league_rho_for("ENGLAND_PREMIER_LEAGUE")
    assert -0.25 <= rho <= 0.05
    assert dbg["source"] == "cache"


def test_attack_defence_lambda_ordering():
    strong = _team(40, 8, 20, h=(10, 25, 3), a=(10, 15, 5))
    weak = _team(8, 40, 20, h=(10, 3, 18), a=(10, 5, 22))
    lam_h, lam_a, meta = expected_goals_from_ratings(strong, weak, league_code="ENGLAND_PREMIER_LEAGUE")
    assert lam_h > lam_a
    assert meta["pipeline"] == "attack_defence"


def test_mle_fit_improves_vs_random():
    cfg = PricingConfig(max_goals=8, use_bivariate=True, use_dixon_coles=True, dixon_coles_rho=-0.1)
    targets = {"Home": 0.45, "Draw": 0.28, "Away": 0.27, "Over2.5": 0.52, "BTTS": 0.54}
    fit = fit_lambdas_mle(targets, config=cfg, lam_h0=1.0, lam_a0=1.0)
    assert fit["ok"] is True
    assert fit["method"] == "mle_nelder_mead"
    assert fit["neg_log_loss"] < 10.0


def test_ml_blend_keeps_goal_markets_from_matrix():
    cfg = PricingConfig(max_goals=8)
    matrix_probs = derive_market_probs(build_score_matrix(1.4, 1.1, config=cfg))
    ml = {"Home": 0.55, "Draw": 0.25, "Away": 0.20}
    blended, dbg = blend_1x2_heads(matrix_probs, ml, ml_weight=0.5)
    assert dbg["blended"] is True
    assert blended["Over2.5"] == matrix_probs["Over2.5"]
    assert blended["BTTS"] == matrix_probs["BTTS"]
    assert abs(sum(blended[k] for k in ("Home", "Draw", "Away")) - 1.0) < 1e-6


def test_portfolio_kelly_sqrt_legs():
    picks = [
        {"market": "Home", "stake": 80.0, "edge_pct": 5.0},
        {"market": "BTTS", "stake": 60.0, "edge_pct": 4.0},
    ]
    out = apply_portfolio_kelly_to_picks(picks, bankroll=1000.0, cap_pct=20.0)
    assert out[0]["portfolio_match_legs"] == 2
    assert out[0]["stake"] == round(80.0 / math.sqrt(2), 2)
    assert out[1]["stake"] == round(60.0 / math.sqrt(2), 2)


def test_model_expected_goals_use_attack_defence(monkeypatch):
    monkeypatch.setenv("FVE_ATTACK_DEFENCE", "1")
    monkeypatch.setenv("FVE_PRICING_MODE", "institutional")
    strong = _team(40, 8, 20)
    weak = _team(8, 40, 20)
    eh, ea = model.expected_goals(strong, weak, use_xg=False, league_code="ENGLAND_PREMIER_LEAGUE")
    assert eh > ea
