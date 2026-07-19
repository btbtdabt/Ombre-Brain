from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from web import config_api


def test_config_updates_serialize_across_two_event_loop_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = config_api._ConfigUpdateCoordinator()
    monkeypatch.setattr(config_api, "_CONFIG_UPDATE_COORDINATOR", coordinator)

    start = threading.Barrier(3)
    state_guard = threading.Lock()
    active = 0
    max_active = 0
    results: list[str] = []
    errors: list[BaseException] = []

    @config_api._serialize_config_updates
    async def update(name: str) -> Any:
        nonlocal active, max_active
        with state_guard:
            active += 1
            max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.05)
            return name
        finally:
            with state_guard:
                active -= 1

    def worker(name: str) -> None:
        try:
            start.wait(timeout=2)
            result = asyncio.run(update(name))
            with state_guard:
                results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with state_guard:
                errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(name,), daemon=True)
        for name in ("first", "second")
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=3)

    assert not [thread for thread in threads if thread.is_alive()]
    assert errors == []
    assert sorted(results) == ["first", "second"]
    assert max_active == 1


@pytest.mark.asyncio
async def test_cancelled_config_waiter_does_not_strand_the_next_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = config_api._ConfigUpdateCoordinator()
    monkeypatch.setattr(config_api, "_CONFIG_UPDATE_COORDINATOR", coordinator)

    holder_entered = asyncio.Event()
    release_holder = asyncio.Event()
    successor_entered = asyncio.Event()

    @config_api._serialize_config_updates
    async def update(name: str) -> Any:
        if name == "holder":
            holder_entered.set()
            await release_holder.wait()
        elif name == "successor":
            successor_entered.set()
        return name

    holder = asyncio.create_task(update("holder"))
    await asyncio.wait_for(holder_entered.wait(), timeout=1)

    cancelled_waiter = asyncio.create_task(update("cancelled"))
    await asyncio.sleep(0)
    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    successor = asyncio.create_task(update("successor"))
    await asyncio.sleep(0)
    assert not successor_entered.is_set()

    release_holder.set()
    assert await asyncio.wait_for(holder, timeout=1) == "holder"
    assert await asyncio.wait_for(successor, timeout=1) == "successor"
    assert successor_entered.is_set()
