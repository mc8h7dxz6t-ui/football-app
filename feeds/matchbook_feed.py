"""Matchbook Edge REST feed — back/lay prices + runner ids for execution."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from bookmakers import bookmaker_url, classify_bookmaker
from feeds.base import FeedAdapter
from feeds.matchbook_client import get_matchbook_client
from pipeline.tick import PriceTick


def _map_runner_to_1x2(runner_name: str, home: str, away: str) -> Optional[str]:
    n = runner_name.strip().lower()
    if n in ("draw", "the draw"):
        return "Draw"
    if home and home.lower() in n:
        return "Home"
    if away and away.lower() in n:
        return "Away"
    return None


class MatchbookFeed(FeedAdapter):
    name = "matchbook"
    enabled_by_default = True
    tier = "exchange"

    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        client = get_matchbook_client()
        if not client:
            return []
        event_id = context.get("matchbook_event_id")
        if not event_id:
            return []

        home = str(context.get("home_team", ""))
        away = str(context.get("away_team", ""))
        markets = client.get_event_markets(int(event_id))
        ticks: List[PriceTick] = []

        for market in markets:
            mname = str(market.get("name") or "").lower()
            if "match odds" not in mname and market.get("market-type") not in ("one_x_two", "match_odds"):
                if "match" not in mname:
                    continue
            market_id = market.get("id")
            for runner in market.get("runners") or []:
                rname = str(runner.get("name") or "")
                leg = _map_runner_to_1x2(rname, home, away)
                if not leg:
                    continue
                prices = runner.get("prices") or []
                backs = [p for p in prices if str(p.get("side", "")).lower() == "back"]
                lays = [p for p in prices if str(p.get("side", "")).lower() == "lay"]
                best_back = max((float(p.get("odds", 0) or 0) for p in backs), default=0.0)
                best_lay = min((float(p.get("odds", 0) or 0) for p in lays), default=0.0) if lays else 0.0
                if best_back <= 1.0:
                    continue
                runner_id = runner.get("id")
                ticks.append(
                    PriceTick(
                        fixture_key=fixture_key,
                        market=leg,
                        selection=rname,
                        odds=best_back,
                        bookmaker="Matchbook",
                        source="matchbook",
                        category=classify_bookmaker("Matchbook"),
                        meta={
                            "bet_url": bookmaker_url("Matchbook", event_label=fixture_key),
                            "runner_id": runner_id,
                            "market_id": market_id,
                            "back_odds": best_back,
                            "lay_odds": best_lay if best_lay > 1.0 else None,
                        },
                    )
                )
        return ticks
