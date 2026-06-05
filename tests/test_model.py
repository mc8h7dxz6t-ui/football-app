import math

import model


def _team(gf, ga, played, h=None, a=None):
    """h/a = (played, gf, ga) venue splits; default to half of overall."""
    h = h or (played // 2, gf // 2, ga // 2)
    a = a or (played - played // 2, gf - gf // 2, ga - ga // 2)
    return {
        "played": played, "goals_for": gf, "goals_against": ga,
        "home_played": h[0], "home_goals_for": h[1], "home_goals_against": h[2],
        "away_played": a[0], "away_goals_for": a[1], "away_goals_against": a[2],
    }


def test_venue_rate_falls_back_to_overall_without_splits():
    bare = {"played": 10, "goals_for": 20, "goals_against": 10}
    assert model.venue_rate(bare, "home", "for") == 2.0  # no venue data -> overall
    empty = {"played": 0, "goals_for": 0, "goals_against": 0}
    assert model.venue_rate(empty, "home", "for") == model.DEFAULT_GOALS_PRIOR


def test_venue_rate_shrinks_toward_overall():
    # Big home-scoring split but only 2 home games -> shrunk below the raw 5.0.
    t = _team(30, 10, 10, h=(2, 10, 1), a=(8, 20, 9))
    r = model.venue_rate(t, "home", "for")
    assert 2.0 < r < 5.0


def test_expected_goals_orders_by_strength():
    strong = _team(40, 8, 10)
    weak = _team(8, 40, 10)
    eh, ea = model.expected_goals(strong, weak)
    assert eh > ea > 0


def test_match_model_is_a_distribution_and_favours_stronger_home():
    strong = _team(40, 8, 10)
    weak = _team(8, 40, 10)
    p = model.match_model(strong, weak)
    assert abs(sum(p.values()) - 1.0) < 1e-9
    assert all(0.0 < v < 1.0 for v in p.values())
    assert p["Home"] > p["Away"]


def test_goal_model_bounds_and_monotonicity():
    high = _team(40, 30, 10)   # lots of goals both ends
    low = _team(6, 6, 10)      # low-scoring
    g_high = model.goal_model(high, high)
    g_low = model.goal_model(low, low)
    for g in (g_high, g_low):
        assert 0.0 < g["Over2.5"] < 1.0 and 0.0 < g["BTTS"] < 1.0
    assert g_high["Over2.5"] > g_low["Over2.5"]


def test_extract_best_takes_max_across_books():
    odds = {
        "response": [
            {
                "bookmakers": [
                    {"bets": [{"name": "Match Winner", "values": [
                        {"value": "Home", "odd": "2.0"}, {"value": "Away", "odd": "3.5"}]}]},
                    {"bets": [{"name": "Match Winner", "values": [
                        {"value": "Home", "odd": "2.2"}]},
                              {"name": "Both Teams Score", "values": [{"value": "Yes", "odd": "1.8"}]}]},
                ]
            }
        ]
    }
    best = model.extract_best(odds)
    assert best["Home"]["odds"] == 2.2   # best of 2.0 / 2.2
    assert best["Away"]["odds"] == 3.5
    assert best["BTTS"]["odds"] == 1.8


def test_extract_best_empty_payload():
    assert model.extract_best({})["Home"]["odds"] == 0.0


def test_edge_and_kelly():
    assert math.isclose(model.edge(0.5, 2.2), 10.0)
    assert model.kelly(0.5, 1.0) == 0.0        # no payout edge
    assert model.kelly(0.4, 2.0) == 0.0        # negative EV -> 0
    assert model.kelly(0.6, 2.0) > 0.0
