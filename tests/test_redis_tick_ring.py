import time

from pipeline.redis_tick_ring import MemoryTickRing
from pipeline.tick import PriceTick


def _tick(market: str, odds: float, ts: float) -> PriceTick:
    return PriceTick(
        "A v B",
        market,
        market,
        odds,
        "Matchbook",
        "matchbook",
        received_at=ts,
        category="exchange",
    )


def test_memory_ring_purges_outside_window():
    ring = MemoryTickRing("A v B", "Home", window_sec=0.05)
    ring.append_tick(_tick("Home", 2.0, time.time()))
    time.sleep(0.06)
    ring.append_tick(_tick("Home", 2.2, time.time()))
    now = time.time()
    assert ring.get_peak_odds("Home", now=now) == 2.2
    assert len(ring.ticks_in_window(now=now)) == 1


def test_memory_ring_peak_within_window():
    ring = MemoryTickRing("A v B", "Home", window_sec=5.0)
    now = time.time()
    ring.append_tick(_tick("Home", 2.0, now - 3))
    ring.append_tick(_tick("Home", 2.3, now - 2))
    ring.append_tick(_tick("Home", 2.1, now - 1))
    assert ring.get_peak_odds("Home", now=now) == 2.3
