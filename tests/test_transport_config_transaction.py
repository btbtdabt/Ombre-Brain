from __future__ import annotations

import json
import os
from typing import Any

import pytest

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

    async def json(self) -> object:
        return self._payload


def response_json(response) -> dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "persisted",
    [
        {"transport": "streamable-http", "unrelated": "keep"},
        {"unrelated": "keep"},
    ],
)
async def test_transport_env_failure_restores_yaml_and_leaves_runtime_untouched(
    monkeypatch: pytest.MonkeyPatch,
    persisted: dict[str, object],
) -> None:
    original_persisted = dict(persisted)
    yaml_states: list[dict[str, object]] = []
    timer_calls: list[tuple[object, ...]] = []

    def update_yaml(mutate) -> dict[str, object]:
        mutate(persisted)
        yaml_states.append(dict(persisted))
        return dict(persisted)

    def fail_env(_name: str, _value: str) -> None:
        raise OSError("managed env is read-only")

    class TimerProbe:
        def __init__(self, *args: object) -> None:
            timer_calls.append(args)

        def start(self) -> None:
            timer_calls.append(("started",))

    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(
        config_api.sh, "_read_json_object", lambda request: request.json()
    )
    monkeypatch.setattr(
        config_api.sh, "config", {"transport": "streamable-http"}
    )
    monkeypatch.setattr(config_api.sh, "_write_env_var", fail_env)
    monkeypatch.setattr(config_api, "atomic_update_config_yaml", update_yaml)
    monkeypatch.setattr("threading.Timer", TimerProbe)
    monkeypatch.setenv("OMBRE_TRANSPORT", "streamable-http")

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/transport")](
        JsonRequest({"transport": "sse"})
    )
    payload = response_json(response)

    assert response.status_code == 409
    assert payload["ok"] is False
    assert payload["restarting"] is False
    assert payload["rollback_failed"] is False
    assert persisted == original_persisted
    assert yaml_states[0]["transport"] == "sse"
    assert yaml_states[-1] == original_persisted
    assert config_api.sh.config["transport"] == "streamable-http"
    assert os.environ["OMBRE_TRANSPORT"] == "streamable-http"
    assert timer_calls == []


@pytest.mark.asyncio
async def test_transport_yaml_failure_does_not_touch_env_or_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_writes: list[tuple[str, str]] = []

    def fail_yaml(_mutate) -> dict[str, object]:
        raise OSError("config volume is read-only")

    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(
        config_api.sh, "_read_json_object", lambda request: request.json()
    )
    monkeypatch.setattr(
        config_api.sh, "config", {"transport": "streamable-http"}
    )
    monkeypatch.setattr(
        config_api.sh,
        "_write_env_var",
        lambda name, value: env_writes.append((name, value)),
    )
    monkeypatch.setattr(config_api, "atomic_update_config_yaml", fail_yaml)
    monkeypatch.setenv("OMBRE_TRANSPORT", "streamable-http")

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/transport")](
        JsonRequest({"transport": "sse"})
    )
    payload = response_json(response)

    assert response.status_code == 500
    assert payload["ok"] is False
    assert payload["restarting"] is False
    assert env_writes == []
    assert config_api.sh.config["transport"] == "streamable-http"
    assert os.environ["OMBRE_TRANSPORT"] == "streamable-http"


@pytest.mark.asyncio
async def test_transport_commits_both_files_before_publishing_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: dict[str, object] = {"transport": "streamable-http"}
    events: list[tuple[str, str]] = []

    def update_yaml(mutate) -> dict[str, object]:
        assert config_api.sh.config["transport"] == "streamable-http"
        assert os.environ["OMBRE_TRANSPORT"] == "streamable-http"
        mutate(persisted)
        events.append(("yaml", str(persisted["transport"])))
        return dict(persisted)

    def update_env(name: str, value: str) -> None:
        assert config_api.sh.config["transport"] == "streamable-http"
        assert os.environ["OMBRE_TRANSPORT"] == "streamable-http"
        events.append((name, value))

    class TimerProbe:
        def __init__(self, _delay: float, _callback) -> None:
            events.append(("timer", "created"))

        def start(self) -> None:
            events.append(("timer", "started"))

    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(
        config_api.sh, "_read_json_object", lambda request: request.json()
    )
    monkeypatch.setattr(
        config_api.sh, "config", {"transport": "streamable-http"}
    )
    monkeypatch.setattr(config_api.sh, "_write_env_var", update_env)
    monkeypatch.setattr(config_api, "atomic_update_config_yaml", update_yaml)
    monkeypatch.setattr("threading.Timer", TimerProbe)
    monkeypatch.setenv("OMBRE_TRANSPORT", "streamable-http")

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/transport")](
        JsonRequest({"transport": "sse"})
    )
    payload = response_json(response)

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["env_persisted"] is True
    assert persisted["transport"] == "sse"
    assert config_api.sh.config["transport"] == "sse"
    assert os.environ["OMBRE_TRANSPORT"] == "sse"
    assert events == [
        ("yaml", "sse"),
        ("OMBRE_TRANSPORT", "sse"),
        ("timer", "created"),
        ("timer", "started"),
    ]
