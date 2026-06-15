"""Worker heartbeat status."""

from __future__ import annotations

from pipeline.worker_status import touch_worker_heartbeat, worker_status


def test_worker_heartbeat(tmp_path, monkeypatch):
    path = tmp_path / "hb"
    monkeypatch.setenv("FVE_WORKER_HEARTBEAT", str(path))
    touch_worker_heartbeat()
    st = worker_status()
    assert st["alive"] is True
    assert st["last_seen_sec_ago"] is not None
    assert st["last_seen_sec_ago"] < 5
