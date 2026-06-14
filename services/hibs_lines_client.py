"""Read-only client for hibs-bet FVE lines proxy (no duplicate book API ingest)."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests

_DEFAULT_TIMEOUT = float(os.environ.get("HIBS_UPSTREAM_TIMEOUT_SEC", "15"))


class HibsLinesClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        token: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = (base_url or os.environ.get("HIBS_UPSTREAM_BASE_URL") or "").rstrip("/")
        self.token = (token or os.environ.get("HIBS_UPSTREAM_TOKEN") or "").strip()
        self.timeout = timeout

    def configured(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> Dict[str, str]:
        headers = {"User-Agent": "fve-hibs-upstream/1.0", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-FVE-Lines-Token"] = self.token
        return headers

    def fetch_fixture_lines(self, fixture_key: str) -> Dict[str, Any]:
        if not self.base_url:
            raise RuntimeError("HIBS_UPSTREAM_BASE_URL not set")
        url = f"{self.base_url}/api/fve/lines/{quote(fixture_key, safe='')}"
        resp = requests.get(url, headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("hibs upstream returned non-object JSON")
        return data
