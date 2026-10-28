"""Prepared for the final acceptance pass; no model/server calls."""
import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.core import request_control as C


def test_total_deadline_does_not_restart_per_delta():
    async def scenario():
        closed = []
        async def source():
            try:
                for _ in range(20):
                    await asyncio.sleep(.02)
                    yield "delta"
            finally:
                closed.append(True)
        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            async for _ in C.bounded_stream(source(), .05):
                pass
        assert closed == [True]
    asyncio.run(scenario())


def test_nested_stream_preserves_control_and_closes_cleanly():
    async def scenario():
        async def source():
            C.check_cancelled()
            yield 1
            C.check_cancelled()
            yield 2
        async def middle():
            async for value in C.bounded_stream(source(), 5):
                yield value
        assert [value async for value in C.bounded_stream(middle(), 1)] == [1, 2]
        assert not C.is_controlled()
    asyncio.run(scenario())


def test_cancelled_worker_cannot_deliver_late_result():
    async def scenario():
        import threading
        entered, release, checked = threading.Event(), threading.Event(), threading.Event()
        def worker():
            entered.set()
            release.wait(1)
            try:
                C.check_cancelled()
            except InterruptedError:
                checked.set()
                raise
        task = asyncio.create_task(C.run_sync(worker))
        while not entered.is_set():
            await asyncio.sleep(.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await asyncio.to_thread(checked.wait, 1)
        assert checked.is_set()
    asyncio.run(scenario())
