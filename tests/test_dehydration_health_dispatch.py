from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from dehydrator import Dehydrator
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
    headers: dict[str, str] = {}
    query_params: dict[str, str] = {}
    path_params: dict[str, str] = {}


def response_json(response: Any) -> dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


def dehydrator_for(api_format: str) -> Dehydrator:
    dehydrator = object.__new__(Dehydrator)
    dehydrator.api_format = api_format
    dehydrator.api_key = "provider-secret"
    dehydrator.model = "probe-model"
    dehydrator.base_url = "https://provider.example"
    dehydrator.max_tokens = 1024
    dehydrator.temperature = 0.1
    dehydrator.timeout_seconds = 15
    dehydrator.thinking_mode = ""
    dehydrator.thinking_budget = 0
    dehydrator.client = None
    return dehydrator


async def call_health(monkeypatch: pytest.MonkeyPatch, dehydrator: Dehydrator):
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "dehydrator", dehydrator, raising=False)
    mcp = FakeMCP()
    config_api.register(mcp)
    return await mcp.routes[("POST", "/api/test/dehydration")](JsonRequest())


@pytest.mark.asyncio
async def test_dehydration_health_uses_openai_compatible_chat_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Completions:
        async def create(self, **kwargs: Any):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
            )

    dehydrator = dehydrator_for("openai_compat")
    client = SimpleNamespace(
        api_key=dehydrator.api_key,
        chat=SimpleNamespace(completions=Completions()),
    )
    setattr(dehydrator, "client", client)

    response = await call_health(monkeypatch, dehydrator)

    assert response.status_code == 200
    assert response_json(response)["api_format"] == "openai_compat"
    assert client.api_key == "provider-secret"
    assert captured["model"] == "probe-model"
    assert captured["messages"] == [
        {"role": "system", "content": "Connection health probe. Reply briefly."},
        {"role": "user", "content": "Reply with OK."},
    ]
    assert captured["max_tokens"] == 5
    assert captured["temperature"] == 0.0


@pytest.mark.asyncio
async def test_dehydration_health_uses_anthropic_messages_auth_and_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": "OK"}]}

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> Response:
            captured["url"] = url
            captured["request"] = kwargs
            return Response()

    monkeypatch.setattr(config_api.httpx, "AsyncClient", Client)
    response = await call_health(monkeypatch, dehydrator_for("anthropic"))

    assert response.status_code == 200
    assert response_json(response)["api_format"] == "anthropic"
    assert captured["url"] == "https://provider.example/v1/messages"
    assert captured["request"]["headers"] == {
        "x-api-key": "provider-secret",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = captured["request"]["json"]
    assert payload["model"] == "probe-model"
    assert payload["messages"] == [{"role": "user", "content": "Reply with OK."}]
    assert payload["system"] == "Connection health probe. Reply briefly."
    assert payload["max_tokens"] == 5


@pytest.mark.asyncio
async def test_dehydration_health_uses_gemini_native_auth_and_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "OK"}]}}
                ]
            }

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> Response:
            captured["url"] = url
            captured["request"] = kwargs
            return Response()

    monkeypatch.setattr(config_api.httpx, "AsyncClient", Client)
    response = await call_health(monkeypatch, dehydrator_for("gemini"))

    assert response.status_code == 200
    assert response_json(response)["api_format"] == "gemini"
    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "probe-model:generateContent"
    )
    assert captured["request"]["headers"] == {
        "x-goog-api-key": "provider-secret"
    }
    payload = captured["request"]["json"]
    assert payload["system_instruction"] == {
        "parts": [{"text": "Connection health probe. Reply briefly."}]
    }
    assert payload["contents"] == [
        {"role": "user", "parts": [{"text": "Reply with OK."}]}
    ]
    assert payload["generationConfig"]["maxOutputTokens"] == 5
    assert payload["generationConfig"]["thinkingConfig"] == {
        "thinkingBudget": 0
    }
