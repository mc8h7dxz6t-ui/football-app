"""Thin client — Streamlit / frontends call FastAPI, never book APIs directly."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests

BASE = os.environ.get("FVE_API_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = 20


class FveApiClient:
    def __init__(self, base_url: str = BASE) -> None:
        self.base_url = base_url

    def available(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=2)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def health(self) -> Dict[str, Any]:
        r = requests.get(f"{self.base_url}/health", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def ingest(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = requests.post(f"{self.base_url}/ingest", json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def lines(self, fixture_key: str) -> Dict[str, Any]:
        r = requests.get(f"{self.base_url}/lines/{fixture_key}", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def value_scan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = requests.post(f"{self.base_url}/value-scan", json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
