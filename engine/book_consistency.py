"""Cross-market book consistency — implied 1X2 vs O2.5 vs BTTS from one score matrix."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from engine.devig import devig_multiway
from pricing.score_matrix import PricingConfig, build_score_matrix, derive_market_probs, fit_lambdas_to_book_marginals


_MARKET_KEYS = {
    "1x2": ("Home", "Draw", "Away"),
    "over25": ("Over2.5",),
    "btts": ("BTTS",),
}


def _best_odds(shopped: Dict[str, Dict[str, Dict[str, Any]]], market: str) -> Optional[float]:
    leg = shopped.get(market) or {}
    for ch in ("soft", "all", "sharp", "exchange"):
        o = float((leg.get(ch) or {}).get("odds") or 0)
        if o > 1.0:
            return o
    for ch_data in leg.values():
        if isinstance(ch_data, dict):
            o = float(ch_data.get("odds") or 0)
            if o > 1.0:
                return o
    return None


def _de_vig_binary(over_odds: float, under_odds: Optional[float] = None) -> Dict[str, float]:
    if under_odds and under_odds > 1.0:
        return devig_multiway({"Over": over_odds, "Under": under_odds}, method="shin")
    # Complement when only one side quoted
    p_over = 1.0 / over_odds
    p_over = min(max(p_over, 0.02), 0.98)
    return {"Over": p_over, "Under": 1.0 - p_over}


def book_marginals_from_shopped(
    shopped: Dict[str, Dict[str, Dict[str, Any]]],
    *,
    method: str = "shin",
) -> Dict[str, float]:
    """De-vigged fair probs for Home/Draw/Away/Over2.5/BTTS from shopped lines."""
    out: Dict[str, float] = {}
    o_h = _best_odds(shopped, "Home")
    o_d = _best_odds(shopped, "Draw")
    o_a = _best_odds(shopped, "Away")
    if o_h and o_d and o_a:
        fair = devig_multiway({"Home": o_h, "Draw": o_d, "Away": o_a}, method=method)  # type: ignore[arg-type]
        out.update(fair)

    o_over = _best_odds(shopped, "Over2.5")
    if o_over:
        o_under = None
        under_leg = shopped.get("Under2.5") or shopped.get("Under 2.5") or {}
        for ch_data in under_leg.values():
            if isinstance(ch_data, dict):
                u = float(ch_data.get("odds") or 0)
                if u > 1.0:
                    o_under = u
                    break
        fair_o = _de_vig_binary(o_over, o_under)
        out["Over2.5"] = fair_o.get("Over", 1.0 / o_over)

    o_btts = _best_odds(shopped, "BTTS")
    if o_btts:
        o_no = _best_odds(shopped, "BTTS No") or _best_odds(shopped, "BTTS_No")
        if o_no:
            fair_b = devig_multiway({"Yes": o_btts, "No": o_no}, method=method)  # type: ignore[arg-type]
            out["BTTS"] = fair_b.get("Yes", 1.0 / o_btts)
        else:
            p = min(max(1.0 / o_btts, 0.02), 0.98)
            out["BTTS"] = p

    return out


def cross_market_discrepancy(
    shopped: Dict[str, Dict[str, Dict[str, Any]]],
    *,
    model_lam_h: float,
    model_lam_a: float,
    min_gap_pct: float = 3.0,
    config: Optional[PricingConfig] = None,
) -> Dict[str, Any]:
    """
    Compare book de-vigged marginals to a single coherent score matrix.

    Returns per-market gaps and synthetic arb hints (book rich vs matrix).
    """
    cfg = config or PricingConfig.from_env()
    book = book_marginals_from_shopped(shopped)
    if not book:
        return {"ok": False, "error": "insufficient book lines", "book_marginals": {}}

    fit = fit_lambdas_to_book_marginals(book, config=cfg, lam_h0=model_lam_h, lam_a0=model_lam_a)
    implied_lam_h = float(fit.get("lam_h") or model_lam_h)
    implied_lam_a = float(fit.get("lam_a") or model_lam_a)
    book_matrix = build_score_matrix(implied_lam_h, implied_lam_a, config=cfg)
    book_coherent = derive_market_probs(book_matrix)

    model_matrix = build_score_matrix(model_lam_h, model_lam_a, config=cfg)
    model_probs = derive_market_probs(model_matrix)

    gaps: Dict[str, float] = {}
    for k, book_p in book.items():
        coherent_p = book_coherent.get(k, 0.0)
        gaps[k] = round((book_p - coherent_p) * 100.0, 3)

    hints: List[Dict[str, Any]] = []
    for market, book_p in book.items():
        coherent_p = book_coherent.get(market, 0.0)
        model_p = model_probs.get(market, 0.0)
        book_rich_pct = (book_p - coherent_p) * 100.0
        model_edge_pct = (model_p - book_p) * 100.0
        if book_rich_pct >= min_gap_pct and model_edge_pct >= min_gap_pct:
            hints.append(
                {
                    "market": market,
                    "book_rich_vs_coherent_pct": round(book_rich_pct, 2),
                    "model_edge_vs_book_pct": round(model_edge_pct, 2),
                    "note": "Book overprices vs joint matrix; model also favours this side.",
                }
            )
        elif book_rich_pct >= min_gap_pct:
            hints.append(
                {
                    "market": market,
                    "book_rich_vs_coherent_pct": round(book_rich_pct, 2),
                    "model_edge_vs_book_pct": round(model_edge_pct, 2),
                    "note": "Cross-market inconsistency — book rich vs fitted score matrix.",
                }
            )

    max_gap = max((abs(v) for v in gaps.values()), default=0.0)
    return {
        "ok": True,
        "book_marginals": {k: round(v, 4) for k, v in book.items()},
        "book_coherent_marginals": {k: round(v, 4) for k, v in book_coherent.items()},
        "model_marginals": {k: round(v, 4) for k, v in model_probs.items()},
        "gaps_book_vs_coherent_pct": gaps,
        "implied_lambdas": {"lam_h": implied_lam_h, "lam_a": implied_lam_a},
        "fit_rmse": fit.get("rmse"),
        "max_abs_gap_pct": round(max_gap, 3),
        "incoherent": max_gap >= min_gap_pct,
        "synthetic_hints": hints,
    }
