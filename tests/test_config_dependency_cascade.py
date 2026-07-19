from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

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


def response_json(response) -> dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


def install_config_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[dict[str, Any], dict[str, SimpleNamespace], list[dict[str, Any]]]:
    vault = tmp_path / "vault"
    runtime: dict[str, Any] = {
        "buckets_dir": str(vault),
        "state_dir": str(tmp_path / "state"),
        "dehydration": {
            "model": "dehy-old",
            "base_url": "https://dehy-old.example/v1",
            "api_key": "dehy-old-key",
        },
        "embedding": {
            "enabled": True,
            "model": "embed-old",
            "base_url": "https://embed-old.example/v1",
            "api_key": "embed-old-key",
        },
        "reranker": {},
        "persona": {},
        "reflection": {},
        "dream": {},
    }
    engines = {
        "reranker": SimpleNamespace(marker="reranker-old"),
        "persona": SimpleNamespace(marker="persona-old"),
        "reflection": SimpleNamespace(marker="reflection-old"),
        "dream": SimpleNamespace(
            marker="dream-old",
            enabled=True,
            model="dream-old",
            base_url="https://dream-old.example/v1",
            api_key="dream-key",
            client=object(),
        ),
    }
    persisted = deepcopy(runtime)
    persistence_calls: list[dict[str, Any]] = []

    def atomic_update(mutator):
        mutator(persisted)
        persistence_calls.append(deepcopy(persisted))
        return deepcopy(persisted)

    def replace_embedding(engine) -> None:
        config_api.sh.embedding_engine = engine
        tools_runtime.embedding_engine = engine

    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(config_api.sh, "dehydrator", None)
    monkeypatch.setattr(config_api.sh, "embedding_engine", None)
    monkeypatch.setattr(config_api.sh, "persona_engine", engines["persona"], raising=False)
    monkeypatch.setattr(config_api.sh, "reflection_engine", engines["reflection"], raising=False)
    monkeypatch.setattr(config_api.sh, "dream_engine", engines["dream"], raising=False)
    monkeypatch.setattr(config_api.sh, "portrait_engine", None, raising=False)
    monkeypatch.setattr(config_api.sh, "replace_embedding_engine", replace_embedding)
    monkeypatch.setattr(tools_runtime, "reranker_engine", engines["reranker"])
    monkeypatch.setattr(config_api, "read_config_yaml", lambda: deepcopy(persisted))
    monkeypatch.setattr(config_api, "atomic_update_config_yaml", atomic_update)
    monkeypatch.setattr(config_api, "_rebuild_embedding_runtime", lambda: None)
    monkeypatch.delenv("OMBRE_GATEWAY_ADMIN_URL", raising=False)

    return runtime, engines, persistence_calls


@pytest.mark.parametrize(
    ("patch", "expected_sections"),
    [
        (
            {"embedding": {"base_url": "https://embed-new.example/v1"}},
            {"reranker", "reflection"},
        ),
        (
            {"dehydration": {"base_url": "https://dehy-new.example/v1"}},
            {"reranker", "persona", "reflection"},
        ),
        (
            {"persona": {"base_url": "https://persona-new.example/v1"}},
            {"persona", "reflection"},
        ),
    ],
    ids=("embedding", "dehydration", "persona"),
)
@pytest.mark.asyncio
async def test_config_post_refreshes_all_runtime_dependents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    patch: dict[str, Any],
    expected_sections: set[str],
) -> None:
    _runtime, engines, _persistence_calls = install_config_runtime(
        monkeypatch, tmp_path
    )
    built: list[str] = []

    def build(
        section: str,
        _config: dict[str, Any],
        _pending_env: dict[str, str] | None = None,
    ) -> SimpleNamespace:
        built.append(section)
        return SimpleNamespace(marker=f"{section}-new")

    monkeypatch.setattr(config_api, "_build_dependency_runtime_engine", build)

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest({"persist": True, **patch})
    )

    assert response.status_code == 200
    assert set(built) == expected_sections
    for section in expected_sections:
        assert engines[section].marker == f"{section}-new"


def test_dependency_engine_builders_resolve_inherited_provider_tuples(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OMBRE_PERSONA_API_KEY", raising=False)
    monkeypatch.delenv("OMBRE_PERSONA_BASE_URL", raising=False)
    monkeypatch.delenv("OMBRE_PERSONA_MODEL", raising=False)
    monkeypatch.delenv("OMBRE_REFLECTION_API_KEY", raising=False)
    monkeypatch.delenv("OMBRE_EMBEDDING_API_KEY", raising=False)
    config = {
        "buckets_dir": str(tmp_path / "vault"),
        "state_dir": str(tmp_path / "state"),
        "dehydration": {
            "model": "dehy-model",
            "base_url": "https://dehy.example/v1",
            "api_key": "dehy-key",
        },
        "embedding": {
            "base_url": "https://embed.example/v1",
            "api_key": "embed-key",
        },
        "reranker": {},
        "persona": {},
        "reflection": {},
    }

    reranker = cast(
        Any, config_api._build_dependency_runtime_engine("reranker", config)
    )
    reflection = cast(
        Any, config_api._build_dependency_runtime_engine("reflection", config)
    )
    persona = cast(
        Any, config_api._build_dependency_runtime_engine("persona", config)
    )

    assert (reranker.base_url, reranker.api_key) == (
        "https://embed.example/v1",
        "embed-key",
    )
    assert (reflection.base_url, reflection.model, reflection.api_key) == (
        "https://embed.example/v1",
        "dehy-model",
        "embed-key",
    )
    assert persona.api_key == "dehy-key"


@pytest.mark.asyncio
async def test_config_post_rolls_back_every_touched_dependency_when_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime, engines, persistence_calls = install_config_runtime(monkeypatch, tmp_path)
    runtime_before = deepcopy(runtime)
    engine_states_before = {
        section: dict(vars(engine)) for section, engine in engines.items()
    }

    monkeypatch.setattr(
        config_api,
        "_build_dependency_runtime_engine",
        lambda section, _config, _pending_env=None: SimpleNamespace(
            marker=f"{section}-new"
        ),
    )
    real_commit = config_api._commit_dependency_runtime_engine

    def fail_after_partial_commit(
        section: str,
        target: object,
        staged: object,
    ) -> None:
        real_commit(section, target, staged)
        if section == "reflection":
            raise RuntimeError("injected dependency failure: private-provider-key")

    monkeypatch.setattr(
        config_api,
        "_commit_dependency_runtime_engine",
        fail_after_partial_commit,
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist": True,
                "dehydration": {"base_url": "https://dehy-new.example/v1"},
            }
        )
    )

    assert response.status_code == 500
    assert response_json(response)["error"] == "runtime reload failed"
    assert "private-provider-key" not in caplog.text
    assert runtime == runtime_before
    assert persistence_calls == []
    for section, engine in engines.items():
        assert vars(engine) == engine_states_before[section]


@pytest.mark.asyncio
async def test_config_post_rolls_back_when_direct_client_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime, engines, persistence_calls = install_config_runtime(monkeypatch, tmp_path)
    runtime["dream"] = {
        "enabled": True,
        "model": "dream-old",
        "base_url": "https://dream-old.example/v1",
        "api_key": "dream-key",
    }
    runtime_before = deepcopy(runtime)
    dream_before = dict(vars(engines["dream"]))

    import openai

    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("client failed")),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist": True,
                "dream": {"base_url": "https://dream-new.example/v1"},
            }
        )
    )

    assert response.status_code == 500
    assert response_json(response)["error"] == "runtime reload failed"
    assert runtime == runtime_before
    assert vars(engines["dream"]) == dream_before
    assert persistence_calls == []


@pytest.mark.parametrize(
    ("section", "env_name", "affected_sections"),
    [
        (
            "persona",
            "OMBRE_PERSONA_API_KEY",
            {"persona", "reflection"},
        ),
        (
            "reflection",
            "OMBRE_REFLECTION_API_KEY",
            {"reflection"},
        ),
    ],
)
@pytest.mark.asyncio
async def test_config_post_stages_pending_secret_before_publishing_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    section: str,
    env_name: str,
    affected_sections: set[str],
) -> None:
    runtime, engines, _persistence_calls = install_config_runtime(
        monkeypatch, tmp_path
    )
    runtime["embedding"]["api_key"] = ""
    monkeypatch.delenv("OMBRE_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("OMBRE_REFLECTION_API_KEY", raising=False)
    monkeypatch.delenv("OMBRE_PERSONA_API_KEY", raising=False)
    monkeypatch.setenv(env_name, "old-env-key")
    monkeypatch.setattr(config_api.sh, "_env_persistence_issue", lambda: None)
    env_writes: list[dict[str, str]] = []
    monkeypatch.setattr(
        config_api,
        "_atomic_update_env_vars",
        lambda updates: env_writes.append(dict(updates)),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist": True,
                "persist_env": True,
                section: {"api_key": "new-env-key"},
            }
        )
    )

    assert response.status_code == 200
    assert env_writes == [{env_name: "new-env-key"}]
    assert env_name in config_api.os.environ
    assert config_api.os.environ[env_name] == "new-env-key"
    for affected in affected_sections:
        assert engines[affected].api_key == "new-env-key"
