import backtest as bt


def _rec(h, d, a, outcome):
    return {"probs": {"Home": h, "Draw": d, "Away": a}, "outcome": outcome}


def test_settle_1x2():
    assert bt.settle_1x2(2, 0) == "Home"
    assert bt.settle_1x2(1, 1) == "Draw"
    assert bt.settle_1x2(0, 3) == "Away"


def test_brier_perfect_and_uniform():
    perfect = [_rec(1.0, 0.0, 0.0, "Home"), _rec(0.0, 0.0, 1.0, "Away")]
    assert bt.brier_score_1x2(perfect) == 0.0
    uniform = [_rec(1 / 3, 1 / 3, 1 / 3, "Home")]
    assert abs(bt.brier_score_1x2(uniform) - 0.6667) < 1e-3
    assert bt.brier_score_1x2([]) is None


def test_log_loss_perfect_is_small_and_clip_safe():
    perfect = [_rec(1.0, 0.0, 0.0, "Home")]
    assert bt.log_loss_1x2(perfect) < 1e-6
    # Outcome assigned ~0 probability must not blow up (clipped).
    wrong = [_rec(1.0, 0.0, 0.0, "Away")]
    assert bt.log_loss_1x2(wrong) > 10.0


def test_top_pick_accuracy():
    recs = [_rec(0.6, 0.2, 0.2, "Home"), _rec(0.2, 0.2, 0.6, "Home")]
    assert bt.top_pick_accuracy(recs) == 50.0


def test_calibration_table_buckets():
    recs = [_rec(0.9, 0.05, 0.05, "Home")] * 4 + [_rec(0.9, 0.05, 0.05, "Away")]
    table = bt.calibration_table(recs, bins=10)
    row = next(r for r in table if r["n"] == 5)
    assert row["avg_predicted_pct"] == 90.0
    assert row["actual_pct"] == 80.0  # 4/5 correct


def test_implied_probs_devig():
    p = bt.implied_probs_1x2(2.0, 4.0, 4.0)
    assert p is not None
    assert abs(sum(p.values()) - 1.0) < 1e-9
    assert p["Home"] > p["Draw"]            # shortest price = highest prob
    assert bt.implied_probs_1x2(0, 4.0, 4.0) is None
    assert bt.implied_probs_1x2(1.0, 4.0, 4.0) is None


def test_evaluate_vs_market_verdict():
    recs = [
        {"probs": {"Home": 0.9, "Draw": 0.05, "Away": 0.05},
         "market_probs": {"Home": 0.5, "Draw": 0.25, "Away": 0.25}, "outcome": "Home"},
        {"probs": {"Home": 0.05, "Draw": 0.05, "Away": 0.9},
         "market_probs": {"Home": 0.25, "Draw": 0.25, "Away": 0.5}, "outcome": "Away"},
    ]
    out = bt.evaluate_vs_market(recs)
    assert out["n_paired"] == 2
    assert out["model"]["brier_score"] < out["market"]["brier_score"]
    assert out["verdict"] == "model beats market"
    assert out["brier_delta_vs_market"] < 0


def test_roi_backtest():
    bets = [
        {"won": True, "odds": 2.5, "stake": 1.0},   # +1.5
        {"won": False, "odds": 3.0, "stake": 1.0},  # -1.0
    ]
    r = bt.roi_backtest(bets)
    assert r["bets"] == 2 and r["wins"] == 1
    assert abs(r["pnl_units"] - 0.5) < 1e-9
    assert r["roi_pct"] == 25.0
    assert bt.roi_backtest([])["roi_pct"] is None


def test_evaluate_summary_and_empty():
    recs = [_rec(0.6, 0.2, 0.2, "Home"), _rec(0.2, 0.2, 0.6, "Away")]
    summ = bt.evaluate(recs)
    assert summ["n"] == 2
    assert summ["brier_score"] is not None
    assert summ["top_pick_accuracy_pct"] == 100.0
    empty = bt.evaluate([])
    assert empty["n"] == 0 and empty["brier_score"] is None
