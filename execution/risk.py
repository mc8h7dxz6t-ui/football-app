"""Execution risk rails — small stakes, kill switch, live-trade gates."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List

from engine.arb import ArbLeg, ArbOpportunity


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "")
    if not v:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class RiskConfig:
    max_stake_gbp: float = float(os.environ.get("MATCHBOOK_MAX_STAKE", "2.0"))
    max_total_outlay_gbp: float = float(os.environ.get("MATCHBOOK_MAX_OUTLAY", "6.0"))
    min_profit_pct: float = float(os.environ.get("ARB_MIN_PROFIT_PCT", "0.5"))
    max_daily_trades: int = int(os.environ.get("MATCHBOOK_MAX_DAILY_TRADES", "20"))
    max_daily_outlay_gbp: float = float(os.environ.get("MATCHBOOK_MAX_DAILY_OUTLAY", "50"))
    kill_switch: bool = _env_bool("MATCHBOOK_KILL_SWITCH", False)
    auto_trade: bool = _env_bool("MATCHBOOK_AUTO_TRADE", False)
    live_confirmed: bool = os.environ.get("MATCHBOOK_CONFIRM_LIVE", "").strip().upper() == "YES"
    allow_partial_dutch: bool = _env_bool("MATCHBOOK_ALLOW_PARTIAL_DUTCH", False)

    _daily_trades: int = field(default=0, init=False, repr=False)
    _daily_outlay: float = field(default=0.0, init=False, repr=False)
    _day: str = field(default_factory=lambda: time.strftime("%Y-%m-%d"))

    def live_enabled(self) -> bool:
        return self.auto_trade and self.live_confirmed and not self.kill_switch

    def _roll_day(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today
            self._daily_trades = 0
            self._daily_outlay = 0.0

    def validate_opportunity(self, opp: ArbOpportunity, legs: List[ArbLeg]) -> None:
        self._roll_day()
        if self.kill_switch:
            raise RiskError("KILL_SWITCH active — no orders")
        if opp.profit_pct < self.min_profit_pct:
            raise RiskError(f"profit {opp.profit_pct:.2f}% below min {self.min_profit_pct}%")
        if opp.kind == "dutch_1x2" and len(opp.matchbook_legs) < 3 and not self.allow_partial_dutch:
            raise RiskError("partial dutch disabled — need all 3 legs on Matchbook or enable ALLOW_PARTIAL")
        if self._daily_trades >= self.max_daily_trades:
            raise RiskError("daily trade count limit reached")
        outlay = sum(leg.stake for leg in legs)
        if outlay > self.max_total_outlay_gbp + 0.05:
            raise RiskError(f"outlay £{outlay:.2f} exceeds max £{self.max_total_outlay_gbp:.2f}")
        if self._daily_outlay + outlay > self.max_daily_outlay_gbp:
            raise RiskError("daily outlay limit reached")
        for leg in legs:
            if leg.stake > self.max_stake_gbp:
                raise RiskError(f"stake £{leg.stake:.2f} exceeds per-leg max £{self.max_stake_gbp:.2f}")
            if not leg.runner_id:
                raise RiskError(f"missing runner_id for {leg.market}")

    def record_execution(self, outlay: float) -> None:
        self._roll_day()
        self._daily_trades += 1
        self._daily_outlay += outlay

    def status(self) -> Dict[str, object]:
        self._roll_day()
        exec_disabled = os.environ.get("EXECUTION_DISABLED", "1").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        return {
            "live_enabled": self.live_enabled(),
            "auto_trade": self.auto_trade,
            "kill_switch": self.kill_switch,
            "max_stake_gbp": self.max_stake_gbp,
            "max_total_outlay_gbp": self.max_total_outlay_gbp,
            "min_profit_pct": self.min_profit_pct,
            "daily_trades": self._daily_trades,
            "daily_outlay_gbp": self._daily_outlay,
            "mode": "analytics",
            "execution_disabled": exec_disabled,
            "sub_100ms_exchange": False,
            "co_location": False,
            "institutional_note": (
                "Sub-100ms exchange execution not in analytics license (EXECUTION_DISABLED)."
            ),
        }


class RiskError(Exception):
    pass
