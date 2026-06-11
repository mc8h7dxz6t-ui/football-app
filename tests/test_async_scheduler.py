import asyncio
import time

from pipeline.async_scheduler import AsyncSchedulerGuard


def test_guard_skips_duplicate_inflight():
    async def run():
        guard = AsyncSchedulerGuard()
        state = {"count": 0}

        def job():
            state["count"] += 1
            time.sleep(0.1)

        t1 = asyncio.create_task(guard.execute_safely("feed:matchbook:fx1", job))
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(guard.execute_safely("feed:matchbook:fx1", job))
        r1 = await t1
        r2 = await t2
        return r1, r2, state["count"]

    r1, r2, count = asyncio.run(run())
    assert r1 is True
    assert r2 is False
    assert count == 1
