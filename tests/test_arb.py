from engine.arb import dutch_1x2_arb, optimal_dutch_stakes, scan_arbitrage, ArbLeg
from execution.matchbook_executor import execute_matchbook_arb
from execution.risk import RiskConfig
from pipeline.tick import PriceTick


def _tick(market, odds, source, book="X", runner_id=100):
    return PriceTick(
        f"A v B",
        market,
        market,
        odds,
        book,
        source,
        category="exchange" if source == "matchbook" else "soft",
        meta={"runner_id": runner_id + hash(market) % 10},
    )


def test_dutch_1x2_detects_arb():
    ticks = [
        _tick("Home", 2.2, "matchbook", "Matchbook", 1),
        _tick("Draw", 4.0, "api-football", "Bet365", 2),
        _tick("Away", 5.0, "matchbook", "Matchbook", 3),
    ]
    opp = dutch_1x2_arb(ticks, fixture_key="A v B", min_profit_pct=0.0)
    assert opp is not None
    assert opp.profit_pct > 0
    assert len(opp.matchbook_legs) == 2


def test_optimal_dutch_stakes_sum_near_outlay():
    legs = [
        ArbLeg("Home", 2.0, "MB", "matchbook", 1, side="back"),
        ArbLeg("Draw", 4.0, "MB", "matchbook", 2, side="back"),
        ArbLeg("Away", 5.0, "MB", "matchbook", 3, side="back"),
    ]
    staked = optimal_dutch_stakes(legs, 6.0)
    assert sum(l.stake for l in staked) > 0
    assert all(l.stake > 0 for l in staked)


def test_execute_dry_run_by_default():
    ticks = [
        _tick("Home", 2.2, "matchbook", "Matchbook", 1),
        _tick("Draw", 4.0, "matchbook", "Matchbook", 2),
        _tick("Away", 5.0, "matchbook", "Matchbook", 3),
    ]
    opps = scan_arbitrage(ticks, fixture_key="A v B", min_profit_pct=0.0)
    assert opps
    risk = RiskConfig()
    risk.auto_trade = False
    result = execute_matchbook_arb(opps[0], risk=risk, total_outlay=6.0)
    assert result.dry_run is True
    assert result.offers_sent
