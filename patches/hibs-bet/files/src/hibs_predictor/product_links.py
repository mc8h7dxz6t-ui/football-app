"""Cross-product navigation URLs (football · racing · trading) for the product bar."""

from __future__ import annotations

import os
from typing import Any, Dict


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def production_mode() -> bool:
    return _env_truthy("HIBS_PRODUCTION")


def site_domain() -> str:
    return (os.getenv("HIBS_DOMAIN") or "hibs-bet.co.uk").strip().rstrip("/")


def football_production_url() -> str:
    return (os.getenv("HIBS_PRODUCTION_URL") or f"https://{site_domain()}").rstrip("/")


def football_home_url() -> str:
    raw = (os.getenv("HIBS_FOOTBALL_HOME_URL") or "/").strip()
    return raw or "/"


def racing_base_url() -> str:
    """Racing entry — relative /racing works on unified hibs-bet.co.uk nginx."""
    explicit = (os.getenv("HIBS_RACING_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    return "/racing"


def racing_cards_url() -> str:
    base = racing_base_url()
    if base.startswith("/"):
        return f"{base}/cards" if not base.endswith("/cards") else base
    return f"{base}/cards"


def trading_status_url() -> str:
    explicit = (os.getenv("HIBS_TRADING_STATUS_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    return "/harvested-execution"


def portfolio_api_url() -> str:
    explicit = (os.getenv("HIBS_PORTFOLIO_API_URL") or "").strip()
    if explicit:
        return explicit
    return "/api/racing/portfolio/summary"


def product_active_from_env(default: str = "football") -> str:
    raw = (os.getenv("HIBS_PRODUCT_ACTIVE") or default).strip().lower()
    return raw if raw in ("football", "racing", "trading") else default


def product_bar_context(*, active: str | None = None) -> Dict[str, Any]:
    """Template context for _product_switcher.html and portfolio bar."""
    racing = racing_base_url()
    active_product = active or product_active_from_env()
    return {
        "hibs_football_home_url": football_home_url(),
        "hibs_racing_base_url": racing,
        "hibs_racing_cards_url": racing_cards_url(),
        "hibs_trading_status_url": trading_status_url(),
        "hibs_product_active": active_product,
        "portfolio_api_url": portfolio_api_url(),
        "portfolio_racing_url": f"{racing}/portfolio" if not racing.endswith("/portfolio") else racing,
        "portfolio_football_url": "/tracker",
        "trading_metrics_url": (os.getenv("TRADING_METRICS_URL") or "http://127.0.0.1:9109").rstrip("/"),
        "hibs_stack_probe_enabled": production_mode() or _env_truthy("HIBS_HEALTH_STACK_PROBE"),
    }
