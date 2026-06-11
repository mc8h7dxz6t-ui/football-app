"""Matchbook order execution with dry-run default."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.arb import ArbLeg, ArbOpportunity, optimal_dutch_stakes
from execution.risk import RiskConfig, RiskError
from feeds.matchbook_client import get_matchbook_client

log = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    opportunity: ArbOpportunity
    dry_run: bool
    legs: List[ArbLeg]
    offers_sent: List[Dict[str, Any]] = field(default_factory=list)
    api_response: Optional[Dict[str, Any]] = None
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "opportunity": self.opportunity.to_dict(),
            "legs": [l.__dict__ for l in self.legs],
            "offers_sent": self.offers_sent,
            "api_response": self.api_response,
            "error": self.error,
        }


def _scale_stakes_to_risk(legs: List[ArbLeg], risk: RiskConfig) -> List[ArbLeg]:
    """Scale stakes so per-leg and total outlay respect risk caps."""
    if not legs:
        return legs
    outlay = sum(l.stake for l in legs)
    if outlay <= 0:
        return legs
    factor = 1.0
    max_leg = max(l.stake for l in legs)
    if max_leg > risk.max_stake_gbp:
        factor = min(factor, risk.max_stake_gbp / max_leg)
    if outlay > risk.max_total_outlay_gbp:
        factor = min(factor, risk.max_total_outlay_gbp / outlay)
    if factor >= 1.0:
        return legs
    return [
        ArbLeg(
            market=l.market,
            odds=l.odds,
            bookmaker=l.bookmaker,
            source=l.source,
            runner_id=l.runner_id,
            market_id=l.market_id,
            side=l.side,
            stake=round(l.stake * factor, 2),
        )
        for l in legs
    ]


def _build_offers(legs: List[ArbLeg]) -> List[Dict[str, Any]]:
    offers: List[Dict[str, Any]] = []
    for leg in legs:
        if leg.runner_id is None:
            continue
        offers.append(
            {
                "runner-id": int(leg.runner_id),
                "side": leg.side,
                "odds": round(float(leg.odds), 2),
                "stake": round(float(leg.stake), 2),
                "keep-in-play": False,
            }
        )
    return offers


def execute_matchbook_arb(
    opp: ArbOpportunity,
    *,
    risk: Optional[RiskConfig] = None,
    total_outlay: Optional[float] = None,
) -> ExecutionResult:
    """Place Matchbook legs for an arb opportunity (dry-run unless live enabled)."""
    risk = risk or RiskConfig()
    legs_to_place = list(opp.matchbook_legs)
    if not legs_to_place:
        return ExecutionResult(opp, dry_run=True, legs=[], error="no Matchbook legs")

    outlay = total_outlay if total_outlay is not None else risk.max_total_outlay_gbp
    legs_to_place = optimal_dutch_stakes(legs_to_place, outlay)
    legs_to_place = _scale_stakes_to_risk(legs_to_place, risk)

    try:
        risk.validate_opportunity(opp, legs_to_place)
    except RiskError as exc:
        return ExecutionResult(opp, dry_run=not risk.live_enabled(), legs=legs_to_place, error=str(exc))

    offers = _build_offers(legs_to_place)
    if not offers:
        return ExecutionResult(opp, dry_run=True, legs=legs_to_place, error="no valid offers")

    dry_run = not risk.live_enabled()
    if dry_run:
        log.info("DRY-RUN Matchbook offers: %s", offers)
        return ExecutionResult(opp, dry_run=True, legs=legs_to_place, offers_sent=offers)

    client = get_matchbook_client()
    if not client:
        return ExecutionResult(opp, dry_run=True, legs=legs_to_place, error="Matchbook credentials missing")

    try:
        resp = client.submit_offers(offers)
        risk.record_execution(sum(l.stake for l in legs_to_place))
        return ExecutionResult(
            opp,
            dry_run=False,
            legs=legs_to_place,
            offers_sent=offers,
            api_response=resp,
        )
    except Exception as exc:
        log.exception("Matchbook submit failed")
        return ExecutionResult(opp, dry_run=False, legs=legs_to_place, offers_sent=offers, error=str(exc))
