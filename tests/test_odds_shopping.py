import bookmakers
import odds_shopping
from odds_sources import merge_offers


def _sample_api_football():
    return {
        "response": [
            {
                "bookmakers": [
                    {
                        "id": 8,
                        "name": "Bet365",
                        "bets": [
                            {
                                "name": "Match Winner",
                                "values": [
                                    {"value": "Home", "odd": "2.0"},
                                    {"value": "Away", "odd": "3.4"},
                                ],
                            }
                        ],
                    },
                    {
                        "id": 3,
                        "name": "Betfair",
                        "bets": [
                            {
                                "name": "Match Winner",
                                "values": [{"value": "Home", "odd": "2.15"}],
                            }
                        ],
                    },
                    {
                        "name": "Pinnacle",
                        "bets": [
                            {
                                "name": "Match Winner",
                                "values": [{"value": "Home", "odd": "2.05"}],
                            }
                        ],
                    },
                ]
            }
        ]
    }


def test_classify_bookmaker_channels():
    assert bookmakers.classify_bookmaker("Betfair Exchange", 3) == "exchange"
    assert bookmakers.classify_bookmaker("Bet365", 8) == "soft"
    assert bookmakers.classify_bookmaker("Pinnacle") == "sharp"


def test_shop_lines_splits_exchange_and_soft():
    offers = odds_shopping.parse_api_football_odds(_sample_api_football(), event_label="A v B")
    shopped = odds_shopping.shop_lines(offers)
    assert shopped["Home"]["all"]["odds"] == 2.15  # Betfair exchange best overall? No - Bet365 is 2.0, Betfair 2.15, Pinnacle 2.05 -> max is 2.15 Betfair
    assert shopped["Home"]["exchange"]["odds"] == 2.15
    assert shopped["Home"]["exchange"]["bookmaker"] == "Betfair"
    assert shopped["Home"]["soft"]["odds"] == 2.0
    assert shopped["Home"]["soft"]["bookmaker"] == "Bet365"
    assert shopped["Home"]["sharp"]["odds"] == 2.05


def test_extract_best_includes_metadata():
    best = odds_shopping.extract_best(_sample_api_football(), event_label="A v B")
    assert best["Home"]["odds"] == 2.15
    assert best["Home"]["bookmaker"] == "Betfair"
    assert best["Home"]["bet_url"].startswith("http")


def test_parse_odds_api_h2h():
    events = [
        {
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "bookmakers": [
                {
                    "title": "Matchbook",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Arsenal", "price": 2.1},
                                {"name": "Chelsea", "price": 3.2},
                                {"name": "Draw", "price": 3.5},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    offers = odds_shopping.parse_odds_api_h2h(events)
    assert any(o.market == "Home" and o.odds == 2.1 and o.category == "exchange" for o in offers)


def test_merge_offers_multi_source():
    af = odds_shopping.parse_api_football_odds(_sample_api_football())
    oa = odds_shopping.parse_odds_api_h2h(
        [
            {
                "home_team": "X",
                "away_team": "Y",
                "bookmakers": [
                    {
                        "title": "Sky Bet",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [{"name": "X", "price": 2.5}],
                            }
                        ],
                    }
                ],
            }
        ]
    )
    merged = merge_offers(af, oa)
    shopped = odds_shopping.shop_lines(merged)
    assert shopped["Home"]["all"]["odds"] == 2.5
    assert shopped["Home"]["all"]["source"] == "the-odds-api"
