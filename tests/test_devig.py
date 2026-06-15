import math

from engine.devig import devig_1x2, overround
from engine.fair_value import benchmark_vs_sharp


def test_overround_positive_for_vigged_book():
    o = overround({"Home": 2.0, "Draw": 3.5, "Away": 4.0})
    assert o > 0


def test_devig_methods_sum_to_one():
    h, d, a = 2.1, 3.4, 3.8
    for method in ("proportional", "power", "shin"):
        p = devig_1x2(h, d, a, method=method)
        assert p is not None
        assert math.isclose(sum(p.values()), 1.0, rel_tol=1e-6)


def test_benchmark_flags_hallucination():
    sharp = devig_1x2(2.0, 3.5, 4.0, method="shin")
    assert sharp is not None
    bench = benchmark_vs_sharp(
        selection="Home",
        soft_odds=1.95,
        sharp_line_probs=sharp,
        model_prob=0.55,
    )
    assert bench is not None
    assert bench.likely_hallucination is True
