"""Matchbook Edge REST feed — direct exchange prices (user API access).

Matchbook does not expose a public odds WebSocket; institutional setups poll
Edge REST at sub-second intervals and fan out via Redis (see pipeline/).
Docs: https://developers.matchbook.com/
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

from bookmakers import classify_bookmaker, bookmaker_url
from feeds.base import FeedAdapter
from pipeline.tick import PriceTick

BASE = os.environ.get("MATCHBOOK_API_BASE", "https://api.matchbook.com/edge/rest")
TIMEOUT = 15


class MatchbookClient:
    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.session_token: Optional[str] = None

    def login(self) -> str:
        resp = requests.post(
            f"{BASE}/security/session",
            json={"username": self.username, "password": self.password},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("session-token") or resp.cookies.get("session-token")
        if not token:
            raise RuntimeError("Matchbook login: no session-token")
        self.session_token = str(token)
        return self.session_token

    def _headers(self) -> Dict[str, str]:
        if not self.session_token:
            self.login()
        return {
            "Accept": "application/json",
            "session-token": self.session_token or "",
        }

    def get_event_markets(self, event_id: int) -> List[Dict[str, Any]]:
        resp = requests.get(
            f"{BASE}/events/{event_id}",
            headers=self._headers(),
            params={"include-prices": "true", "odds-type": "DECIMAL"},
            timeout=TIMEOUT,
        )
        if resp.status_code == 401:
            self.login()
            resp = requests.get(
                f"{BASE}/events/{event_id}",
                headers=self._headers(),
                params={"include-prices": "true", "odds-type": "DECIMAL"},
                timeout=TIMEOUT,
            )
        resp.raise_for_status()
        data = resp.json()
        return data.get("markets") or []


_client: Optional[MatchbookClient] = None


def _client_singleton() -> Optional[MatchbookClient]:
    global _client
    user = os.environ.get("MATCHBOOK_USERNAME", "").strip()
    pwd = os.environ.get("MATCHBOOK_PASSWORD", "").strip()
    if not user or not pwd:
        return None
    if _client is None:
        _client = MatchbookClient(user, pwd)
    return _client


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

    def fetch_ticks(self, fixture_key: str, context: Dict[str, Any]) -> List[PriceTick]:
        client = _client_singleton()
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
            for runner in market.get("runners") or []:
                rname = str(runner.get("name") or "")
                leg = _map_runner_to_1x2(rname, home, away)
                if not leg:
                    continue
                prices = runner.get("prices") or []
                back = [p for p in prices if str(p.get("side", "")).lower() == "back"]
                if not back:
                    continue
                best = max(float(p.get("odds", 0) or 0) for p in back)
                if best <= 1.0:
                    continue
                ticks.append(
                    PriceTick(
                        fixture_key=fixture_key,
                        market=leg,
                        selection=rname,
                        odds=best,
                        bookmaker="Matchbook",
                        source="matchbook",
                        category=classify_bookmaker("Matchbook"),
                        meta={
                            "bet_url": bookmaker_url("Matchbook", event_label=fixture_key),
                            "runner_id": runner.get("id"),
                            "market_id": market.get("id"),
                        },
                    )
                )
        return ticks
