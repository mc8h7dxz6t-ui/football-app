"""FastAPI + WebSocket integration tests — no Redis, Matchbook, or live API keys."""

from __future__ import annotations

import pytest

from tests.conftest import seed_fixture_cache


def test_health_ok(api_client):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["cache_backend"] == "memory-zset"
    assert body["line_bus"] == "local"
    assert body.get("wire_codec") in ("orjson", "json", "msgpack")
    assert "ws_max_pending_sends" in body
    assert "api_budgets" in body


def test_lines_404_without_cache(api_client):
    resp = api_client.get("/lines/Unknown%20Fixture")
    assert resp.status_code == 404


def test_lines_and_fixture_after_seed(api_client, memory_cache, fixture_key):
    seed_fixture_cache(memory_cache, fixture_key)
    lines = api_client.get(f"/lines/{fixture_key}")
    assert lines.status_code == 200
    data = lines.json()
    assert data["tick_count"] >= 3
    assert "shopped" in data
    assert data["shopped"]["Home"]["soft"]["odds"] >= 2.0

    bundle = api_client.get(f"/fixture/{fixture_key}")
    assert bundle.status_code == 200
    assert bundle.json()["ready"]["lines"] is True
    assert bundle.json()["ready"]["sports"] is True


def test_value_scan_with_cached_lines(api_client, memory_cache, fixture_key):
    seed_fixture_cache(memory_cache, fixture_key)
    resp = api_client.post(
        "/value-scan",
        json={
            "fixture_key": fixture_key,
            "min_edge_pct": -50.0,
            "bankroll": 1000.0,
            "kelly_fraction": 0.25,
            "use_xg": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fixture_key"] == fixture_key
    assert isinstance(body["picks"], list)
    assert body["picks"]  # strong home vs weak away should produce at least one pick


def test_devig_demo(api_client):
    resp = api_client.get("/devig/demo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["overround_pct"] > 0
    assert sum(body["shin"].values()) == pytest.approx(1.0, abs=0.01)


def test_ws_snapshot_after_seed(api_client, memory_cache, fixture_key):
    seed_fixture_cache(memory_cache, fixture_key)
    with api_client.websocket_connect(f"/ws/lines/{fixture_key}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "snapshot"
        assert msg["fixture_key"] == fixture_key
        assert msg["ready"]["lines"] is True


def test_ws_waiting_without_cache(api_client, fixture_key):
    with api_client.websocket_connect(f"/ws/lines/{fixture_key}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "waiting"
        assert msg["fixture_key"] == fixture_key


def test_ws_ping_and_snapshot_commands(api_client, memory_cache, fixture_key):
    seed_fixture_cache(memory_cache, fixture_key)
    with api_client.websocket_connect(f"/ws/fixture/{fixture_key}") as ws:
        ws.receive_json()
        ws.send_text("ping")
        pong = ws.receive_json()
        assert pong["type"] == "pong"
        ws.send_text("snapshot")
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["lines"]["tick_count"] >= 3
