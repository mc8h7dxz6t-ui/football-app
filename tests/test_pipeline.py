import time

from pipeline.cache import LineCache
from pipeline.circuit_breaker import CircuitBreaker
from pipeline.tick import PriceTick


def test_tick_dedupe_key_stable():
    t1 = PriceTick("a v b", "Home", "Home", 2.1, "Bet365", "api-football")
    t2 = PriceTick("a v b", "Home", "Home", 2.1, "Bet365", "api-football")
    assert t1.dedupe_key() == t2.dedupe_key()


def test_memory_cache_roundtrip():
    cache = LineCache(redis_url="redis://127.0.0.1:59999/0", ttl_sec=30)
    ticks = [PriceTick("x v y", "Home", "Home", 2.0, "MB", "matchbook", category="exchange")]
    n = cache.merge_ticks("x v y", ticks, feed_name="test")
    assert n["snapshot_count"] == 1
    got = cache.get_ticks("x v y")
    assert len(got) == 1
    assert got[0].odds == 2.0


def test_circuit_breaker_opens():
    br = CircuitBreaker("test", failure_threshold=2, recovery_timeout_sec=0.01)
    br.record_failure("e1")
    br.record_failure("e2")
    assert br.state.value == "open"
    time.sleep(0.02)
    assert br.state.value == "half_open"
