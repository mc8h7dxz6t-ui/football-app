"""Tests for FVE watchlist discovery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pipeline.watchlist import (
    discover_from_hibs_upstream,
    discover_from_scrape_lines_dir,
    discover_upcoming,
    parse_fixture_spec,
)


def test_parse_fixture_spec():
    keys, ctx = parse_fixture_spec("Arsenal v Chelsea:99:123")
    assert keys == ["Arsenal v Chelsea"]
    assert ctx["Arsenal v Chelsea"]["fixture_id"] == 99
    assert ctx["Arsenal v Chelsea"]["matchbook_event_id"] == 123
    assert ctx["Arsenal v Chelsea"]["home_team"] == "Arsenal"


def test_parse_fixture_spec_empty():
    keys, ctx = parse_fixture_spec("")
    assert keys == []
    assert ctx == {}


def test_discover_from_hibs_upstream(monkeypatch):
    monkeypatch.setenv("HIBS_UPSTREAM_BASE_URL", "http://hibs.test")
    payload = {
        "fixtures": [
            {"fixture_key": "Arsenal v Chelsea", "home_team": "Arsenal", "away_team": "Chelsea"},
            {"fixture_key": "Liverpool v Spurs", "home_team": "Liverpool", "away_team": "Spurs"},
        ]
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()
    with patch("pipeline.watchlist.requests.get", return_value=mock_resp):
        keys, ctx = discover_from_hibs_upstream()
    assert keys == ["Arsenal v Chelsea", "Liverpool v Spurs"]
    assert ctx["Arsenal v Chelsea"]["source"] == "hibs_upstream"


def test_discover_from_scrape_lines_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FVE_SCRAPE_LINES_DIR", str(tmp_path))
    (tmp_path / "Arsenal_v_Chelsea.json").write_text(
        '{"fixture_key":"Arsenal v Chelsea","home_team":"Arsenal","away_team":"Chelsea"}',
        encoding="utf-8",
    )
    keys, ctx = discover_from_scrape_lines_dir()
    assert keys == ["Arsenal v Chelsea"]
    assert ctx["Arsenal v Chelsea"]["source"] == "scrape_lines_dir"


def test_discover_upcoming_falls_back_to_hibs(monkeypatch):
    monkeypatch.setenv("FVE_FEED_MODE", "scrape")
    monkeypatch.setenv("HIBS_UPSTREAM_BASE_URL", "http://hibs.test")
    monkeypatch.delenv("API_SPORTS_KEY", raising=False)
    with patch("scrapers.fotmob_client.discover_fixtures", return_value=([], {})):
        with patch(
            "pipeline.watchlist.discover_from_hibs_upstream",
            return_value=(["A v B"], {"A v B": {"event_label": "A v B"}}),
        ):
            keys, _ = discover_upcoming()
    assert keys == ["A v B"]
