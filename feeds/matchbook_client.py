"""Matchbook Edge REST client — prices and order execution."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

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
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "football-value-engine/2.0",
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("session-token") or resp.cookies.get("session-token")
        if not token:
            raise RuntimeError("Matchbook login: no session-token")
        self.session_token = str(token)
        return self.session_token

    def _headers(self, *, json_body: bool = False) -> Dict[str, str]:
        if not self.session_token:
            self.login()
        h = {
            "Accept": "application/json",
            "session-token": self.session_token or "",
            "User-Agent": "football-value-engine/2.0",
        }
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        resp = requests.request(method, f"{BASE}{path}", timeout=TIMEOUT, **kwargs)
        if resp.status_code == 401:
            self.login()
            kwargs["headers"] = self._headers(json_body=method in ("POST", "PUT"))
            resp = requests.request(method, f"{BASE}{path}", timeout=TIMEOUT, **kwargs)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}

    def get_event_markets(self, event_id: int) -> List[Dict[str, Any]]:
        data = self._request(
            "GET",
            f"/events/{event_id}",
            headers=self._headers(),
            params={"include-prices": "true", "odds-type": "DECIMAL"},
        )
        return data.get("markets") or []

    def submit_offers(self, offers: List[Dict[str, Any]]) -> Dict[str, Any]:
        """POST /v2/offers — back or lay on runner-id."""
        body = {
            "odds-type": "DECIMAL",
            "exchange-type": "back-lay",
            "offers": offers,
        }
        return self._request(
            "POST",
            "/v2/offers",
            headers=self._headers(json_body=True),
            json=body,
        )


_client: Optional[MatchbookClient] = None


def get_matchbook_client() -> Optional[MatchbookClient]:
    global _client
    user = os.environ.get("MATCHBOOK_USERNAME", "").strip()
    pwd = os.environ.get("MATCHBOOK_PASSWORD", "").strip()
    if not user or not pwd:
        return None
    if _client is None:
        _client = MatchbookClient(user, pwd)
    return _client
