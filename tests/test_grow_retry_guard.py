"""Regression coverage for grow retries after an MCP/client timeout."""

import asyncio
import threading

import pytest

from tools.grow.retry_guard import (
    request_fingerprint,
    reset_for_tests,
    run_once,
)


@pytest.fixture(autouse=True)
def clear_retry_guard():
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.mark.asyncio
async def test_completed_retry_reuses_result_without_running_twice():
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return "2条|新2合0"

    first = await run_once("same-request", operation)
    second = await run_once("same-request", operation)

    assert first == "2条|新2合0"
    assert "未重复写入" in second
    assert "2条|新2合0" in second
    assert calls == 1


@pytest.mark.asyncio
async def test_inflight_retry_returns_immediately_and_original_survives_cancellation():
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        completed.set()
        return "后台写入完成"

    original_waiter = asyncio.create_task(run_once("slow-request", operation))
    await started.wait()

    duplicate = await asyncio.wait_for(
        run_once("slow-request", operation), timeout=0.1
    )
    assert "仍在后台处理中" in duplicate

    original_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await original_waiter

    release.set()
    await asyncio.wait_for(completed.wait(), timeout=1)
    await asyncio.sleep(0)

    retried = await run_once("slow-request", operation)
    assert "未重复写入" in retried
    assert "后台写入完成" in retried
    assert calls == 1


@pytest.mark.asyncio
async def test_failed_operation_is_not_cached():
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary provider failure")
        return "retry succeeded"

    with pytest.raises(RuntimeError, match="temporary provider failure"):
        await run_once("failed-request", operation)

    assert await run_once("failed-request", operation) == "retry succeeded"
    assert calls == 2


def test_request_fingerprint_is_stable_but_includes_payload_mode():
    content_a = request_fingerprint(
        content="  diary\r\nentry  ", items=None, test_data=False
    )
    content_b = request_fingerprint(
        content="diary\nentry", items=None, test_data=False
    )
    items = request_fingerprint(
        content="diary\nentry", items=["final memory"], test_data=False
    )

    assert content_a == content_b
    assert items != content_a


def test_inflight_retry_is_shared_across_event_loops_and_threads():
    started = threading.Event()
    release = threading.Event()
    original_result: dict[str, str] = {}
    calls = 0
    calls_lock = threading.Lock()

    async def operation() -> str:
        nonlocal calls
        with calls_lock:
            calls += 1
        started.set()
        released = await asyncio.to_thread(release.wait, 2.0)
        assert released
        return "cross-loop write complete"

    def run_original() -> None:
        original_result["value"] = asyncio.run(
            run_once("cross-loop-request", operation)
        )

    worker = threading.Thread(target=run_original)
    worker.start()
    try:
        assert started.wait(timeout=1.0)
        duplicate = asyncio.run(run_once("cross-loop-request", operation))
        assert "仍在后台处理中" in duplicate
    finally:
        release.set()
        worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert original_result["value"] == "cross-loop write complete"
    replayed = asyncio.run(run_once("cross-loop-request", operation))
    assert "未重复写入" in replayed
    assert "cross-loop write complete" in replayed
    assert calls == 1
