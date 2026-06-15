"""Public read-only lines export for Football Value Engine (FVE upstream).

Wire in hibs-bet web.py (once):

    from hibs_predictor.fve_lines_proxy import register_fve_lines_routes
    register_fve_lines_routes(app)

Env (hibs-bet .env):
    FVE_LINES_TOKEN=optional-shared-secret
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request


def _token_ok() -> bool:
    expected = (os.environ.get("FVE_LINES_TOKEN") or "").strip()
    if not expected:
        return True
    supplied = (
        (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        or (request.headers.get("X-FVE-Lines-Token") or "").strip()
    )
    return supplied == expected


def _norm_label(home: str, away: str) -> str:
    return f"{home.strip()} v {away.strip()}"


def _fixture_packet(row: Dict[str, Any]) -> Dict[str, Any]:
    home = str(row.get("home_team") or row.get("home") or "").strip()
    away = str(row.get("away_team") or row.get("away") or "").strip()
    best = row.get("best_odds_1x2") or {}
    return {
        "fixture_key": _norm_label(home, away),
        "fixture_id": row.get("fixture_id") or row.get("id"),
        "home_team": home,
        "away_team": away,
        "kickoff_iso": row.get("kickoff_iso") or row.get("date"),
        "best_odds_1x2": best,
        "best_odds_source": row.get("best_odds_source") or {},
        "home_stats": row.get("home_stats") or {},
        "away_stats": row.get("away_stats") or {},
        "league": row.get("league") or row.get("league_code"),
    }


def _find_fixture(bundle: Dict[str, Any], fixture_key: str) -> Optional[Dict[str, Any]]:
    target = fixture_key.strip().casefold()
    for row in bundle.get("all") or []:
        if not isinstance(row, dict):
            continue
        home = str(row.get("home_team") or row.get("home") or "").strip()
        away = str(row.get("away_team") or row.get("away") or "").strip()
        label = _norm_label(home, away)
        if label.casefold() == target:
            return row
    return None


def build_lines_payload(bundle_loader, fixture_key: str) -> Dict[str, Any]:
    bundle = bundle_loader()
    row = _find_fixture(bundle, fixture_key)
    if not row:
        return {"ok": False, "error": "fixture_not_found", "fixture_key": fixture_key}
    pkt = _fixture_packet(row)
    pkt["ok"] = True
    return pkt


def register_fve_lines_routes(app: Flask, *, bundle_loader=None) -> None:
    """Register GET /api/fve/lines/<fixture_key> on the Flask app."""

    def _loader():
        if bundle_loader is not None:
            return bundle_loader()
        from hibs_predictor.web import _load_fixtures_for_http

        return _load_fixtures_for_http()

    @app.route("/api/fve/lines/<path:fixture_key>", methods=["GET"])
    def api_fve_lines(fixture_key: str):
        if not _token_ok():
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        payload = build_lines_payload(_loader, fixture_key)
        if not payload.get("ok"):
            return jsonify(payload), 404
        return jsonify(payload)
