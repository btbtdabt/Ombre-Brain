from __future__ import annotations

import asyncio
import json
import threading
from copy import deepcopy
from typing import Any

import pytest

from tools import _runtime as tools_runtime
from web import config_api


class FakeMCP:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Any] = {}

    def custom_route(self, path: str, methods: list[str]):
        def decorator(handler):
            for method in methods:
                self.routes[(method, path)] = handler
            return handler

        return decorator


class JsonRequest:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.headers: dict[str, str] = {}
        self.query_params: dict[str, str] = {}
        self.path_params: dict[str, str] = {}

    async def json(self) -> object:
        return self._payload


@pytest.mark.asyncio
async def test_secret_persist_failure_rolls_back_only_its_own_config_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = {
        "buckets_dir": "vault",
        "timezone": "Asia/Shanghai",
        "gateway": {"cooldown_hours": 6},
        "surfacing": {"sampling": {"top_k": 3}},
    }
    persisted = deepcopy(runtime)
    persisted_lock = threading.Lock()
    env_attempt_started = threading.Event()
    writer_done = threading.Event()
    writer_errors: list[BaseException] = []

    def read_persisted() -> dict[str, Any]:
        with persisted_lock:
            return deepcopy(persisted)

    def update_persisted(mutator) -> dict[str, Any]:
        with persisted_lock:
            mutator(persisted)
            return deepcopy(persisted)

    def fail_env_persistence(_updates: dict[str, str]) -> None:
        env_attempt_started.set()
        if not writer_done.wait(5):
            raise RuntimeError("concurrent writer did not finish")
        raise OSError("managed env is unavailable")

    async def concurrent_writer() -> None:
        try:
            if not env_attempt_started.wait(5):
                raise RuntimeError("config transaction never reached env persistence")
            runtime["surfacing"]["sampling"]["top_k"] = 99

            def mutate(config: dict[str, Any]) -> None:
                config.setdefault("surfacing", {}).setdefault("sampling", {})[
                    "top_k"
                ] = 99

            update_persisted(mutate)
        except BaseException as exc:  # pragma: no cover - asserted below
            writer_errors.append(exc)
        finally:
            writer_done.set()

    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "_env_persistence_issue", lambda: "")
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)
    for name in (
        "dehydrator",
        "embedding_engine",
        "persona_engine",
        "dream_engine",
        "reflection_engine",
        "portrait_engine",
    ):
        monkeypatch.setattr(config_api.sh, name, None, raising=False)
    monkeypatch.setattr(tools_runtime, "reranker_engine", None)
    monkeypatch.setattr(config_api, "read_config_yaml", read_persisted)
    monkeypatch.setattr(config_api, "atomic_update_config_yaml", update_persisted)
    monkeypatch.setattr(config_api, "_atomic_update_env_vars", fail_env_persistence)

    writer = threading.Thread(target=lambda: asyncio.run(concurrent_writer()))
    writer.daemon = True
    writer.start()

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist": True,
                "persist_env": True,
                "timezone": "America/New_York",
                "gateway": {
                    "cooldown_hours": 7,
                    "domain_sentinel_api_key": "new-secret",
                },
            }
        )
    )
    writer.join(timeout=5)

    assert response.status_code == 409, json.loads(response.body)
    assert not writer.is_alive()
    assert writer_errors == []
    assert runtime["gateway"] == {"cooldown_hours": 6}
    assert runtime["timezone"] == "Asia/Shanghai"
    assert runtime["surfacing"]["sampling"]["top_k"] == 99
    assert persisted["gateway"] == {"cooldown_hours": 6}
    assert persisted["timezone"] == "Asia/Shanghai"
    assert persisted["surfacing"]["sampling"]["top_k"] == 99
