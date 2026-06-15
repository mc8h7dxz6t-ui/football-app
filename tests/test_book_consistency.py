"""Cross-market book consistency checks."""

from engine.book_consistency import book_marginals_from_shopped, cross_market_discrepancy


def _shopped_1x2_only():
    return {
        "Home": {"soft": {"odds": 2.1, "bookmaker": "A"}},
        "Draw": {"soft": {"odds": 3.4, "bookmaker": "A"}},
        "Away": {"soft": {"odds": 3.8, "bookmaker": "A"}},
    }


def test_book_marginals_from_1x2():
    book = book_marginals_from_shopped(_shopped_1x2_only())
    assert abs(sum(book.values()) - 1.0) < 0.01
    assert "Home" in book


def test_cross_market_detects_extra_markets():
    shopped = {
        **_shopped_1x2_only(),
        "Over2.5": {"soft": {"odds": 1.45, "bookmaker": "B"}},
        "BTTS": {"soft": {"odds": 1.55, "bookmaker": "B"}},
    }
    out = cross_market_discrepancy(shopped, model_lam_h=1.3, model_lam_a=1.1)
    assert out["ok"] is True
    assert "book_marginals" in out
    assert "gaps_book_vs_coherent_pct" in out


def test_cross_market_empty_shopped():
    out = cross_market_discrepancy({}, model_lam_h=1.2, model_lam_a=1.2)
    assert out["ok"] is False
