"""Optional Celery worker for distributed ingest (Redis broker)."""

from __future__ import annotations

import os

from celery import Celery

broker = os.environ.get("CELERY_BROKER_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/1"))
app = Celery("fve", broker=broker, backend=broker)
app.conf.task_serializer = "json"
app.conf.result_serializer = "json"
app.conf.accept_content = ["json"]
app.conf.beat_schedule = {
    "ingest-watchlist": {
        "task": "tasks.celery_app.ingest_watchlist",
        "schedule": float(os.environ.get("INGEST_INTERVAL_SEC", "5")),
    },
}


@app.task
def ingest_watchlist() -> dict:
    from pipeline.ingest import ingest_fixture
    from feeds.registry import build_default_registry

    spec = os.environ.get("WATCHLIST_FIXTURES", "")
    registry = build_default_registry()
    results = []
    for part in spec.split(","):
        if not part.strip():
            continue
        bits = part.strip().split(":")
        fk = bits[0]
        ctx = {"event_label": fk}
        if len(bits) > 1 and bits[1]:
            ctx["fixture_id"] = int(bits[1])
        if len(bits) > 2 and bits[2]:
            ctx["matchbook_event_id"] = int(bits[2])
        results.append(ingest_fixture(registry, fk, context=ctx))
    return {"ingested": len(results)}
