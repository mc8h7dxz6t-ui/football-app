"""Arbitrage detection from cached multi-book ticks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from pipeline.tick import PriceTick

Leg = str  # Home | Draw | Away


@dataclass
class ArbLeg:
    market: Leg
    odds: float
    bookmaker: str
    source: str
    runner_id: Optional[int] = None
    market_id: Optional[int] = None
    side: str = "back"  # back | lay
    stake: float = 0.0


@dataclass
class ArbOpportunity:
    kind: str  # dutch_1x2 | matchbook_back_lay
    fixture_key: str
    profit_pct: float
    legs: List[ArbLeg]
    matchbook_legs: List[ArbLeg] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "kind": self.kind,
            "fixture_key": self.fixture_key,
            "profit_pct": round(self.profit_pct, 4),
            "legs": [leg.__dict__ for leg in self.legs],
            "matchbook_legs": [leg.__dict__ for leg in self.matchbook_legs],
            "notes": self.notes,
        }


def _best_back_per_leg(ticks: List[PriceTick]) -> Dict[Leg, PriceTick]:
    legs: Dict[Leg, PriceTick] = {}
    for t in ticks:
        if t.market not in ("Home", "Draw", "Away"):
            continue
        cur = legs.get(t.market)
        if cur is None or t.odds > cur.odds:
            legs[t.market] = t
    return legs


def dutch_1x2_arb(
    ticks: List[PriceTick],
    *,
    fixture_key: str,
    min_profit_pct: float = 0.0,
) -> Optional[ArbOpportunity]:
    """Cross-book 1X2 dutch: back all outcomes at best price; arb if sum(1/odds) < 1."""
    best = _best_back_per_leg(ticks)
    if len(best) < 3:
        return None
    inv = sum(1.0 / t.odds for t in best.values())
    if inv >= 1.0:
        return None
    profit_pct = (1.0 / inv - 1.0) * 100.0
    if profit_pct < min_profit_pct:
        return None

    legs: List[ArbLeg] = []
    mb_legs: List[ArbLeg] = []
    for leg, tick in best.items():
        al = ArbLeg(
            market=leg,
            odds=tick.odds,
            bookmaker=tick.bookmaker,
            source=tick.source,
            runner_id=tick.meta.get("runner_id"),
            market_id=tick.meta.get("market_id"),
            side="back",
        )
        legs.append(al)
        if tick.source == "matchbook" or tick.bookmaker.lower() == "matchbook":
            mb_legs.append(al)

    notes = ""
    if len(mb_legs) < 3:
        notes = (
            f"Partial auto-exec: {len(mb_legs)}/3 legs on Matchbook — "
            "place remaining legs manually at other books for locked arb."
        )

    return ArbOpportunity(
        kind="dutch_1x2",
        fixture_key=fixture_key,
        profit_pct=profit_pct,
        legs=legs,
        matchbook_legs=mb_legs,
        notes=notes,
    )


def matchbook_back_lay_arb(
    ticks: List[PriceTick],
    *,
    fixture_key: str,
    min_profit_pct: float = 0.0,
) -> List[ArbOpportunity]:
    """Same runner: back at best back, lay at best lay on Matchbook."""
    by_market: Dict[Leg, Dict[str, float]] = {}
    meta: Dict[Leg, Dict[str, object]] = {}
    for t in ticks:
        if t.source != "matchbook" and t.bookmaker.lower() != "matchbook":
            continue
        if t.market not in ("Home", "Draw", "Away"):
            continue
        row = by_market.setdefault(t.market, {})
        back = float(t.meta.get("back_odds") or t.odds)
        lay = float(t.meta.get("lay_odds") or 0)
        if back > 1.0:
            row["back"] = max(row.get("back", 0), back)
        if lay > 1.0:
            row["lay"] = min(row["lay"], lay) if row.get("lay") else lay
        if t.meta.get("runner_id"):
            meta[t.market] = t.meta

    out: List[ArbOpportunity] = []
    for leg, prices in by_market.items():
        back = prices.get("back", 0)
        lay = prices.get("lay", 0)
        if back <= 1.0 or lay <= 1.0 or back >= lay:
            continue
        # Simplified scalp: back high lay low — profit if lay_implied < back
        lay_imp = 1.0 / lay
        back_imp = 1.0 / back
        if back_imp + lay_imp >= 1.0:
            continue
        profit_pct = (1.0 / (back_imp + lay_imp) - 1.0) * 100.0
        if profit_pct < min_profit_pct:
            continue
        m = meta.get(leg, {})
        out.append(
            ArbOpportunity(
                kind="matchbook_back_lay",
                fixture_key=fixture_key,
                profit_pct=profit_pct,
                legs=[
                    ArbLeg(leg, back, "Matchbook", "matchbook", m.get("runner_id"), m.get("market_id"), "back"),
                    ArbLeg(leg, lay, "Matchbook", "matchbook", m.get("runner_id"), m.get("market_id"), "lay"),
                ],
                matchbook_legs=[
                    ArbLeg(leg, back, "Matchbook", "matchbook", m.get("runner_id"), m.get("market_id"), "back"),
                    ArbLeg(leg, lay, "Matchbook", "matchbook", m.get("runner_id"), m.get("market_id"), "lay"),
                ],
                notes="Intra-Matchbook back/lay scalp — requires both offers to fill.",
            )
        )
    return out


def optimal_dutch_stakes(legs: List[ArbLeg], total_outlay: float) -> List[ArbLeg]:
    """Equal-profit dutching stakes for a set of back legs."""
    if not legs or total_outlay <= 0:
        return legs
    inv = sum(1.0 / leg.odds for leg in legs)
    if inv <= 0:
        return legs
    payout = total_outlay / inv
    out: List[ArbLeg] = []
    for leg in legs:
        stake = payout / leg.odds
        out.append(
            ArbLeg(
                market=leg.market,
                odds=leg.odds,
                bookmaker=leg.bookmaker,
                source=leg.source,
                runner_id=leg.runner_id,
                market_id=leg.market_id,
                side=leg.side,
                stake=round(stake, 2),
            )
        )
    return out


def scan_arbitrage(
    ticks: List[PriceTick],
    *,
    fixture_key: str,
    min_profit_pct: float = 0.3,
) -> List[ArbOpportunity]:
    opps: List[ArbOpportunity] = []
    d = dutch_1x2_arb(ticks, fixture_key=fixture_key, min_profit_pct=min_profit_pct)
    if d:
        opps.append(d)
    opps.extend(matchbook_back_lay_arb(ticks, fixture_key=fixture_key, min_profit_pct=min_profit_pct))
    opps.sort(key=lambda o: -o.profit_pct)
    return opps
