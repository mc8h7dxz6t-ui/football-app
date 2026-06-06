import xg_sources as xs


def test_normalize_team():
    assert xs.normalize_team("Manchester United FC") == "manchesterunited"
    assert xs.normalize_team("Atlético Madrid") == "atleticomadrid"
    assert xs.normalize_team("  Brighton & Hove Albion ") == "brightonhovealbion"
    assert xs.normalize_team("") == ""


def test_aggregate_team_xg_splits():
    matches = [
        {"home_team": "A", "away_team": "B", "home_xg": 2.0, "away_xg": 1.0},
        {"home_team": "B", "away_team": "A", "home_xg": 1.5, "away_xg": 0.5},
    ]
    agg = xs.aggregate_team_xg(matches)
    a = agg["a"]
    assert a["xg_played"] == 2
    assert abs(a["xg_for"] - 2.5) < 1e-9      # 2.0 home + 0.5 away
    assert abs(a["xg_against"] - 2.5) < 1e-9  # 1.0 + 1.5
    assert a["home_xg_played"] == 1 and abs(a["home_xg_for"] - 2.0) < 1e-9
    assert a["away_xg_played"] == 1 and abs(a["away_xg_for"] - 0.5) < 1e-9


def test_aggregate_skips_malformed():
    agg = xs.aggregate_team_xg([{"home_team": "A", "away_team": "B", "home_xg": "x", "away_xg": 1.0}])
    assert agg == {}


def test_attach_xg_matches_by_normalized_name():
    standings = {
        10: {"name": "Manchester United FC", "played": 5, "goals_for": 8, "goals_against": 6},
        20: {"name": "Some Unmatched FC", "played": 5, "goals_for": 4, "goals_against": 9},
    }
    xg = {"manchesterunited": {"xg_played": 5, "xg_for": 9.1, "xg_against": 5.2}}
    merged, matched = xs.attach_xg(standings, xg)
    assert matched == 1
    assert merged[10]["xg_for"] == 9.1
    assert "xg_for" not in merged[20]            # unmatched team untouched
    assert merged[10]["goals_for"] == 8          # original fields preserved


def test_fetch_unsupported_league_returns_empty():
    assert xs.fetch_understat_team_xg(179, 2025) == {}  # Scotland: not on Understat
