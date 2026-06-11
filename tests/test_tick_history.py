import time

from pipeline.cache import LineCache
from pipeline.tick import PriceTick
from pipeline.tick_history import merge_snapshot, peak_ticks_in_window


def test_merge_snapshot_detects_odds_change():
    existing = [PriceTick("a v b", "Home", "Home", 2.0, "Matchbook", "matchbook")]
    incoming = [PriceTick("a v b", "Home", "Home", 2.1, "Matchbook", "matchbook")]
    snap, changed = merge_snapshot(existing, incoming)
    assert len(changed) == 1
    assert snap[0].odds == 2.1


def test_peak_ticks_captures_intra_window_spike():
    now = time.time()
    history = [
        PriceTick("a v b", "Home", "Home", 2.0, "MB", "matchbook", received_at=now - 4),
        PriceTick("a v b", "Home", "Home", 2.15, "MB", "matchbook", received_at=now - 2),
        PriceTick("a v b", "Home", "Home", 2.05, "MB", "matchbook", received_at=now - 1),
    ]
    peaks = peak_ticks_in_window(history, 5.0, now=now)
    assert len(peaks) == 1
    assert peaks[0].odds == 2.15


def test_cache_merge_appends_history():
    cache = LineCache(redis_url="redis://127.0.0.1:59999/0", ttl_sec=30)
    t1 = [PriceTick("x v y", "Home", "Home", 2.0, "MB", "matchbook")]
    t2 = [PriceTick("x v y", "Home", "Home", 2.1, "MB", "matchbook")]
    cache.merge_ticks("x v y", t1, feed_name="matchbook")
    stats = cache.merge_ticks("x v y", t2, feed_name="matchbook")
    assert stats["changed"] == 1
    hist = cache.get_tick_history("x v y", since=time.time() - 60)
    assert len(hist) >= 1
    peak = cache.get_peak_ticks("x v y", window_sec=60)
    assert peak[0].odds == 2.1
