from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from gateway import GatewayService
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
    def __init__(self, payload: object | None = None) -> None:
        self._payload = {} if payload is None else payload
        self.headers: dict[str, str] = {}
        self.query_params: dict[str, str] = {}
        self.path_params: dict[str, str] = {}
        self.method = "POST"

    async def json(self) -> object:
        return self._payload


def response_json(response: Any) -> dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


@pytest.mark.parametrize(
    "url",
    [
        "https://gateway.example/api/config",
        "http://ombre-gateway:8010/api/config",
        "http://localhost:8010/api/config",
        "http://127.0.0.1:8010/api/config",
        "http://10.20.30.40:8010/api/config",
        "http://[::1]:8010/api/config",
        "http://[fd00::10]:8010/api/config",
    ],
)
def test_gateway_admin_endpoint_allows_only_encrypted_or_explicit_local_sinks(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    monkeypatch.setenv("OMBRE_GATEWAY_ADMIN_URL", url)

    assert config_api._gateway_admin_endpoint() == (url, "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://gateway.example/api/config",
        "http://localhost.evil/api/config",
        "http://198.51.100.20/api/config",
    ],
)
async def test_gateway_admin_post_rejects_public_http_before_client_or_token_use(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    class ForbiddenClient:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("public HTTP must be rejected before client creation")

    monkeypatch.setenv("OMBRE_GATEWAY_ADMIN_URL", url)
    monkeypatch.setenv("OMBRE_GATEWAY_TOKEN", "must-not-be-sent")
    monkeypatch.setattr(config_api.httpx, "AsyncClient", ForbiddenClient)

    result = await config_api._post_gateway_live_config(
        {"dehydration": {"model": "new"}}
    )

    assert result == (False, "invalid_admin_url")


def _persist_fixture(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = {"buckets_dir": "vault", "dehydration": {"model": "old"}}
    persisted = deepcopy(runtime)

    def read_config() -> dict[str, Any]:
        return deepcopy(persisted)

    def persist_config(mutator):
        mutator(persisted)
        return deepcopy(persisted)

    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(config_api.sh, "dehydrator", None)
    monkeypatch.setattr(config_api, "read_config_yaml", read_config)
    monkeypatch.setattr(config_api, "atomic_update_config_yaml", persist_config)
    return runtime, persisted


@pytest.mark.asyncio
async def test_gateway_admin_post_uses_bearer_auth_bounded_timeout_and_validates_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class StubResponse:
        status_code = 200
        content = b'{"ok":true,"updated":["dehydration.model"]}'

        def json(self) -> dict[str, Any]:
            return {"ok": True, "updated": ["dehydration.model"]}

    class StubClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> StubResponse:
            captured["url"] = url
            captured["post_kwargs"] = kwargs
            return StubResponse()

    monkeypatch.setenv(
        "OMBRE_GATEWAY_ADMIN_URL", "http://ombre-gateway:8010/api/config"
    )
    monkeypatch.setenv("OMBRE_GATEWAY_TOKEN", "gateway-admin-secret")
    monkeypatch.setattr(config_api.httpx, "AsyncClient", StubClient)

    result = await config_api._post_gateway_live_config(
        {"dehydration": {"model": "new"}}
    )

    assert result == (True, "")
    assert captured["url"] == "http://ombre-gateway:8010/api/config"
    assert captured["post_kwargs"]["headers"] == {
        "Authorization": "Bearer gateway-admin-secret"
    }
    assert captured["post_kwargs"]["json"] == {
        "dehydration": {"model": "new"}
    }
    assert captured["client_kwargs"]["follow_redirects"] is False
    assert captured["client_kwargs"]["trust_env"] is False
    timeout = captured["client_kwargs"]["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    timeout_values = [timeout.connect, timeout.read, timeout.write, timeout.pool]
    assert all(value is not None for value in timeout_values)
    assert max(value for value in timeout_values if value is not None) <= 10
    assert "gateway-admin-secret" not in repr(result)


@pytest.mark.asyncio
async def test_gateway_admin_post_rejects_invalid_boundary_and_reports_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **_kwargs: Any):
            request = httpx.Request("POST", url)
            raise httpx.ReadTimeout("late", request=request)

    monkeypatch.setenv("OMBRE_GATEWAY_TOKEN", "gateway-admin-secret")
    monkeypatch.setenv("OMBRE_GATEWAY_ADMIN_URL", "file:///tmp/api/config")
    invalid = await config_api._post_gateway_live_config(
        {"dehydration": {"model": "new"}}
    )

    monkeypatch.setenv(
        "OMBRE_GATEWAY_ADMIN_URL", "http://ombre-gateway:8010/api/config"
    )
    monkeypatch.setattr(config_api.httpx, "AsyncClient", TimeoutClient)
    timed_out = await config_api._post_gateway_live_config(
        {"dehydration": {"model": "new"}}
    )

    assert invalid == (False, "invalid_admin_url")
    assert timed_out == (False, "timeout")


@pytest.mark.asyncio
async def test_gateway_admin_post_rejects_incomplete_success_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IncompleteResponse:
        status_code = 200
        content = b'{"ok":true,"updated":[]}'

        def json(self) -> dict[str, Any]:
            return {"ok": True, "updated": []}

    class IncompleteClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, _url: str, **_kwargs: Any) -> IncompleteResponse:
            return IncompleteResponse()

    monkeypatch.setenv(
        "OMBRE_GATEWAY_ADMIN_URL", "http://ombre-gateway:8010/api/config"
    )
    monkeypatch.setenv("OMBRE_GATEWAY_TOKEN", "gateway-admin-secret")
    monkeypatch.setattr(config_api.httpx, "AsyncClient", IncompleteClient)

    result = await config_api._post_gateway_live_config(
        {"dehydration": {"model": "new"}}
    )

    assert result == (False, "incomplete_response")


def test_gateway_live_apply_plan_never_resolves_upstream_env_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMBRE_GATEWAY_PROVIDER_API_KEY", "do-not-forward-this")
    body = {
        "persist": True,
        "persist_env": True,
        "gateway": {
            "cooldown_hours": 4,
            "upstreams": [
                {
                    "name": "provider",
                    "protocol": "openai",
                    "base_url": "https://models.example/v1",
                    "api_key_envs": ["OMBRE_GATEWAY_PROVIDER_API_KEY"],
                    "api_key_values": ["new-do-not-forward"],
                    "models": ["model"],
                }
            ]
        },
    }

    payload, restart_fields = config_api._build_gateway_live_apply_plan(
        body,
        {"OMBRE_GATEWAY_PROVIDER_API_KEY": "new-do-not-forward"},
    )

    serialized = json.dumps(payload)
    assert payload == {"gateway": {"cooldown_hours": 4}}
    assert "api_key_values" not in serialized
    assert "do-not-forward-this" not in serialized
    assert "new-do-not-forward" not in serialized
    assert restart_fields == ["gateway.upstreams.api_key_values"]


@pytest.mark.asyncio
async def test_config_post_clears_gateway_restart_after_complete_live_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, persisted = _persist_fixture(monkeypatch)
    calls: list[dict[str, Any]] = []

    async def live_apply(payload: dict[str, Any]) -> tuple[bool, str]:
        calls.append(deepcopy(payload))
        return True, ""

    monkeypatch.setattr(config_api, "_post_gateway_live_config", live_apply)
    mcp = FakeMCP()
    config_api.register(mcp)

    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest({"persist": True, "dehydration": {"model": "new"}})
    )
    payload = response_json(response)

    assert response.status_code == 200
    assert persisted["dehydration"]["model"] == "new"
    assert runtime["dehydration"]["model"] == "new"
    assert calls == [{"dehydration": {"model": "new"}}]
    assert payload["gateway_live_apply_attempted"] is True
    assert payload["gateway_live_apply_applied"] is True
    assert payload["gateway_live_apply_failed"] is False
    assert payload["gateway_restart_required"] is False
    assert payload["gateway_external_restart_required"] is False
    assert payload["restart_required"] is False


@pytest.mark.asyncio
async def test_config_post_keeps_restart_for_fields_gateway_cannot_live_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime, persisted = _persist_fixture(monkeypatch)
    calls: list[dict[str, Any]] = []

    async def live_apply(payload: dict[str, Any]) -> tuple[bool, str]:
        calls.append(deepcopy(payload))
        return True, ""

    monkeypatch.setattr(config_api, "_post_gateway_live_config", live_apply)
    monkeypatch.setattr(
        config_api,
        "_apply_current_runtime_sections",
        lambda *_args, **_kwargs: [],
    )
    mcp = FakeMCP()
    config_api.register(mcp)

    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist": True,
                "dehydration": {"model": "new", "timeout_seconds": 27},
            }
        )
    )
    payload = response_json(response)

    assert response.status_code == 200
    assert persisted["dehydration"]["timeout_seconds"] == 27
    assert calls == [{"dehydration": {"model": "new"}}]
    assert payload["gateway_live_apply_applied"] is True
    assert payload["gateway_live_apply_failed"] is False
    assert payload["gateway_restart_fields"] == ["dehydration.timeout_seconds"]
    assert payload["gateway_restart_required"] is True
    assert payload["gateway_external_restart_required"] is True


@pytest.mark.asyncio
async def test_config_post_keeps_durable_save_and_requires_external_restart_on_live_apply_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime, persisted = _persist_fixture(monkeypatch)

    async def live_apply(_payload: dict[str, Any]) -> tuple[bool, str]:
        return False, "timeout"

    monkeypatch.setattr(config_api, "_post_gateway_live_config", live_apply)
    mcp = FakeMCP()
    config_api.register(mcp)

    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest({"persist": True, "dehydration": {"model": "durable-new"}})
    )
    payload = response_json(response)

    assert response.status_code == 200
    assert persisted["dehydration"]["model"] == "durable-new"
    assert payload["ok"] is True
    assert payload["gateway_live_apply_attempted"] is True
    assert payload["gateway_live_apply_applied"] is False
    assert payload["gateway_live_apply_failed"] is True
    assert payload["gateway_live_apply_error"] == "timeout"
    assert payload["gateway_restart_required"] is True
    assert payload["gateway_external_restart_required"] is True
    assert payload["restart_required"] is True


def test_gateway_admin_upstream_contract_preserves_gemini_fields_without_resolving_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = cast(Any, object.__new__(GatewayService))
    service.gateway_cfg = {"upstreams": []}
    monkeypatch.setenv("OMBRE_GATEWAY_GEMINI_API_KEY", "must-stay-redacted")

    sanitized = service._sanitize_gateway_upstreams_config(
        [
            {
                "name": "gemini",
                "protocol": "openai",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "gemini_base_url": "https://generativelanguage.googleapis.com/v1beta",
                "gemini_auth": "google",
                "api_key_envs": ["OMBRE_GATEWAY_GEMINI_API_KEY"],
                "models": ["gemini-2.5-pro"],
            }
        ]
    )

    assert sanitized[0]["gemini_base_url"] == (
        "https://generativelanguage.googleapis.com/v1beta"
    )
    assert sanitized[0]["gemini_auth"] == "google"
    assert sanitized[0]["api_key_envs"] == ["OMBRE_GATEWAY_GEMINI_API_KEY"]
    assert "must-stay-redacted" not in json.dumps(sanitized)

    service.gateway_cfg = {"upstreams": sanitized}
    service.upstreams = [
        {
            **sanitized[0],
            "api_keys": [],
            "models": ["gemini-2.5-pro"],
            "model_map": {"gemini-2.5-pro": "gemini-2.5-pro"},
        }
    ]
    public = service._gateway_upstreams_config_payload()
    assert public[0]["gemini_base_url"] == sanitized[0]["gemini_base_url"]
    assert public[0]["gemini_auth"] == "google"
    assert "must-stay-redacted" not in json.dumps(public)

    with pytest.raises(ValueError, match="gemini_auth"):
        service._sanitize_gateway_upstreams_config(
            [
                {
                    "name": "bad",
                    "protocol": "openai",
                    "base_url": "https://example.test/v1",
                    "gemini_auth": "basic",
                }
            ]
        )


@pytest.mark.asyncio
async def test_gateway_admin_config_rolls_back_all_earlier_sections_when_later_apply_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = cast(Any, object.__new__(GatewayService))
    service.config = {
        "dehydration": {"model": "old-model"},
        "gateway": {"cooldown_hours": 6},
        "self_anchor": {"entry_bucket_id": "anchor"},
        "embedding": {"model": "embed"},
        "dream": {"enabled": True},
    }
    service.gateway_cfg = service.config["gateway"]
    service.self_anchor_cfg = service.config["self_anchor"]
    service.embedding_cfg = service.config["embedding"]
    service.dream_cfg = service.config["dream"]
    service.dehydrator = SimpleNamespace(model="old-model", client=object())
    service.upstream_key_cooldowns = {"provider": 42.0}
    service._bucket_list_cache = {"all": {"expires_at": 99.0}}
    service.runtime_marker = "old"
    service._authorize = lambda _header: None
    monkeypatch.setenv("OMBRE_PERSONA_MODEL", "old-persona")
    original_config = deepcopy(service.config)
    original_client = service.dehydrator.client

    def apply_dehydration(_payload: dict[str, Any]) -> list[str]:
        service.config["dehydration"]["model"] = "partially-applied"
        service.dehydrator.model = "partially-applied"
        service.dehydrator.client = object()
        service.upstream_key_cooldowns.clear()
        service._bucket_list_cache.clear()
        service.runtime_marker = "partially-applied"
        service.new_partial_attribute = True
        monkeypatch.setenv("OMBRE_PERSONA_MODEL", "partially-applied")
        return ["dehydration.model"]

    def fail_gateway(_payload: dict[str, Any]) -> list[str]:
        service.gateway_cfg["cooldown_hours"] = 1
        raise ValueError("injected later-section failure")

    service._apply_dehydration_config = apply_dehydration
    service._apply_gateway_memory_config = fail_gateway

    response = await service.handle_config(
        JsonRequest(
            {
                "dehydration": {"model": "new-model"},
                "gateway": {"cooldown_hours": 1},
            }
        )
    )

    assert response.status_code == 400
    assert service.config == original_config
    assert service.gateway_cfg is service.config["gateway"]
    assert service.dehydrator.model == "old-model"
    assert service.dehydrator.client is original_client
    assert service.upstream_key_cooldowns == {"provider": 42.0}
    assert service._bucket_list_cache == {"all": {"expires_at": 99.0}}
    assert service.runtime_marker == "old"
    assert not hasattr(service, "new_partial_attribute")
    assert config_api.os.environ["OMBRE_PERSONA_MODEL"] == "old-persona"


def test_models_data_explains_external_gateway_live_apply_outcomes() -> None:
    asset = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "dashboard-assets"
        / "models-data.js"
    ).read_text(encoding="utf-8")

    assert "gateway_live_apply_failed" in asset
    assert "external ombre-gateway service" in asset
    assert "loadAndPopulate(panelId, null, true, true)" in asset
    assert "app.ui.setRestartRequired" in asset
    assert "outcome.brainRestart" in asset
    assert "Gateway restart required for all processes" not in asset
    assert "'/api/restart'" not in asset

    unified_shell = (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "dashboard-assets"
        / "unified-shell.js"
    ).read_text(encoding="utf-8")
    assert "setRestartRequired: function setRestartRequired" in unified_shell
    assert "global.setRestartRequired(Boolean(required)" in unified_shell
