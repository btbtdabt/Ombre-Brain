import json
import os
from types import SimpleNamespace

import pytest

import web.config_api as config_api
from reranker_engine import RerankerEngine
from utils import get_ai_name, load_config


class FakeMCP:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(fn):
            for method in methods:
                self.routes[(method, path)] = fn
            return fn

        return decorator


class JsonRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_env_config_can_clear_ai_display_name(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_NAME", "trainsprout")
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(config_api.sh, "_project_env_path", lambda: str(tmp_path / ".env"))
    monkeypatch.setattr(config_api.sh, "config", {})

    mcp = FakeMCP()
    config_api.register(mcp)

    response = await mcp.routes[("POST", "/api/env-config")](
        JsonRequest({"updates": {"AI_NAME": ""}})
    )
    payload = json.loads(response.body)

    assert payload["ok"] is True
    assert "AI_NAME" in payload["updated"]
    assert os.environ.get("AI_NAME") is None
    assert get_ai_name() == "AI"


@pytest.mark.asyncio
async def test_env_config_rejects_nul_before_runtime_or_persistence(
    monkeypatch, tmp_path
):
    writes = []
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(
        config_api.sh, "_project_env_path", lambda: str(tmp_path / ".env")
    )
    monkeypatch.setattr(config_api.sh, "config", {})
    monkeypatch.setattr(
        config_api, "_atomic_update_env_vars", lambda updates: writes.append(updates)
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/env-config")](
        JsonRequest({"updates": {"AI_NAME": "unsafe\0value"}})
    )
    payload = json.loads(response.body)

    assert response.status_code == 400
    assert payload["ok"] is False
    assert payload["updated"] == []
    assert "NUL" in payload["error"]
    assert writes == []
    assert os.environ.get("AI_NAME") != "unsafe\0value"


@pytest.mark.asyncio
async def test_compress_runtime_reload_rolls_back_when_env_persistence_fails(
    monkeypatch, tmp_path
):
    import openai

    created_clients = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created_clients.append(self)

    old_client = object()
    dehydrator = SimpleNamespace(
        api_key="old-key",
        base_url="https://old.example/v1",
        model="old-model",
        timeout_seconds=60.0,
        api_format="openai_compat",
        api_available=True,
        client=old_client,
    )
    runtime_config = {
        "dehydration": {
            "api_key": "old-key",
            "base_url": "https://old.example/v1",
            "model": "old-model",
            "timeout_seconds": 60,
            "api_format": "openai_compat",
        }
    }
    def fail_env_persistence(_updates):
        raise OSError("Device or resource busy")

    monkeypatch.setattr(config_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(
        config_api.sh, "_project_env_path", lambda: str(tmp_path / ".env")
    )
    monkeypatch.setattr(config_api.sh, "config", runtime_config)
    monkeypatch.setattr(config_api.sh, "dehydrator", dehydrator)
    monkeypatch.setattr(config_api, "_atomic_update_env_vars", fail_env_persistence)
    monkeypatch.setattr(
        config_api,
        "atomic_update_config_yaml",
        lambda _mutate: pytest.fail("env-config must never persist secrets to YAML"),
    )
    monkeypatch.setattr(openai, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setenv("OMBRE_COMPRESS_API_KEY", "old-key")
    monkeypatch.setenv("OMBRE_COMPRESS_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("OMBRE_COMPRESS_MODEL", "old-model")
    monkeypatch.setenv("OMBRE_COMPRESS_TIMEOUT_SECONDS", "60")

    updates = {
        # Deliberately not client-construction order: the route must stage the
        # complete batch and build exactly one client from the final values.
        "OMBRE_COMPRESS_MODEL": "new-model",
        "OMBRE_COMPRESS_API_KEY": "new-key",
        "OMBRE_COMPRESS_TIMEOUT_SECONDS": "45",
        "OMBRE_COMPRESS_BASE_URL": "https://new.example/v1",
    }
    mcp = FakeMCP()
    config_api.register(mcp)

    response = await mcp.routes[("POST", "/api/env-config")](
        JsonRequest({"updates": updates})
    )
    payload = json.loads(response.body)

    assert response.status_code == 409
    assert payload["ok"] is False
    assert payload["partial"] is False
    assert payload["updated"] == []
    assert payload["persisted"] == []
    assert payload["error"] == "environment persistence failed"

    assert runtime_config["dehydration"]["api_key"] == "old-key"
    assert runtime_config["dehydration"]["base_url"] == "https://old.example/v1"
    assert runtime_config["dehydration"]["model"] == "old-model"
    assert dehydrator.api_key == "old-key"
    assert dehydrator.base_url == "https://old.example/v1"
    assert dehydrator.model == "old-model"
    assert dehydrator.timeout_seconds == 60.0
    assert dehydrator.api_available is True
    assert len(created_clients) == 1
    assert dehydrator.client is old_client
    assert created_clients[0].kwargs == {
        "api_key": "new-key",
        "base_url": "https://new.example/v1",
        "timeout": 45.0,
    }
    assert os.environ["OMBRE_COMPRESS_API_KEY"] == "old-key"
    assert os.environ["OMBRE_COMPRESS_BASE_URL"] == "https://old.example/v1"


@pytest.mark.asyncio
async def test_compress_client_rebuild_failure_is_not_reported_as_success(
    monkeypatch, tmp_path
):
    import openai

    def fail_client_rebuild(**kwargs):
        raise ValueError("invalid base URL")

    persistence_called = False

    def persist_unexpectedly(_mutate):
        nonlocal persistence_called
        persistence_called = True

    old_client = object()
    dehydrator = SimpleNamespace(
        api_key="old-key",
        base_url="https://old.example/v1",
        model="old-model",
        timeout_seconds=60.0,
        api_format="openai_compat",
        api_available=True,
        client=old_client,
    )
    runtime_config = {
        "dehydration": {
            "api_key": "old-key",
            "base_url": "https://old.example/v1",
            "model": "old-model",
            "timeout_seconds": 60,
            "api_format": "openai_compat",
        }
    }

    monkeypatch.setattr(config_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(
        config_api.sh, "_project_env_path", lambda: str(tmp_path / ".env")
    )
    monkeypatch.setattr(config_api.sh, "config", runtime_config)
    monkeypatch.setattr(config_api.sh, "dehydrator", dehydrator)
    monkeypatch.setattr(config_api, "atomic_update_config_yaml", persist_unexpectedly)
    monkeypatch.setattr(openai, "AsyncOpenAI", fail_client_rebuild)
    monkeypatch.setenv("OMBRE_COMPRESS_API_KEY", "old-key")
    monkeypatch.setenv("OMBRE_COMPRESS_BASE_URL", "https://old.example/v1")

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/env-config")](
        JsonRequest(
            {
                "updates": {
                    "OMBRE_COMPRESS_API_KEY": "new-key",
                    "OMBRE_COMPRESS_BASE_URL": "not-a-valid-base-url",
                }
            }
        )
    )
    payload = json.loads(response.body)

    assert payload["ok"] is False
    assert payload["partial"] is False
    assert payload["updated"] == []
    assert payload["persisted"] == []
    assert payload["error"] == "provider configuration could not be applied"
    assert persistence_called is False
    assert runtime_config["dehydration"]["api_key"] == "old-key"
    assert runtime_config["dehydration"]["base_url"] == "https://old.example/v1"
    assert dehydrator.api_key == "old-key"
    assert dehydrator.base_url == "https://old.example/v1"
    assert dehydrator.client is old_client
    assert os.environ["OMBRE_COMPRESS_API_KEY"] == "old-key"
    assert os.environ["OMBRE_COMPRESS_BASE_URL"] == "https://old.example/v1"


def test_v1_environment_names_remain_compatible(request, monkeypatch, tmp_path):
    # load_config() 把 legacy PASSWORD 映射成 OMBRE_DASHBOARD_PASSWORD 时是直接写
    # os.environ 的。monkeypatch.delenv 在变量原本就不存在时不记录任何东西，于是
    # 还原不了这个「测试期间才被创建」的变量——它会泄漏到本次 session 的后续用例，
    # 让 web/auth 的用例在随机序下变红（env 密码模式会让请求在 JSON 校验前短路）。
    request.addfinalizer(
        lambda: os.environ.pop("OMBRE_DASHBOARD_PASSWORD", None)
    )
    monkeypatch.delenv("OMBRE_COMPRESS_API_KEY", raising=False)
    monkeypatch.delenv("OMBRE_COMPRESS_BASE_URL", raising=False)
    monkeypatch.delenv("OMBRE_DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setenv("OMBRE_API_KEY", "legacy-key")
    monkeypatch.setenv("OMBRE_BASE_URL", "https://legacy.example/v1")
    monkeypatch.delenv("OMBRE_EMBED_API_KEY", raising=False)
    monkeypatch.setenv("OMBRE_EMBEDDING_API_KEY", "legacy-embedding-key")
    monkeypatch.setenv("PASSWORD", "legacy-password")
    monkeypatch.setenv("OMBRE_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.delenv("OMBRE_BUCKETS_DIR", raising=False)

    config = load_config(str(tmp_path / "missing-config.yaml"))

    assert config["dehydration"]["api_key"] == "legacy-key"
    assert config["dehydration"]["base_url"] == "https://legacy.example/v1"
    assert config["embedding"]["api_key"] == "legacy-embedding-key"
    assert os.environ["OMBRE_EMBED_API_KEY"] == "legacy-embedding-key"
    assert os.environ["OMBRE_DASHBOARD_PASSWORD"] == "legacy-password"
    assert config["media_dir"] == str(tmp_path / "vault" / "_media")


@pytest.mark.asyncio
async def test_env_config_get_reports_legacy_embedding_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OMBRE_EMBED_API_KEY", raising=False)
    monkeypatch.setenv("OMBRE_EMBEDDING_API_KEY", "legacy-dashboard-key")
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(
        config_api.sh, "_project_env_path", lambda: str(tmp_path / ".env")
    )
    monkeypatch.setattr(config_api.sh, "_read_env_var", lambda name: "")
    config = load_config(str(tmp_path / "missing-config.yaml"))
    monkeypatch.setattr(config_api.sh, "config", config)

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("GET", "/api/env-config")](JsonRequest({}))
    payload = json.loads(response.body)

    field = payload["fields"]["OMBRE_EMBED_API_KEY"]
    assert field["is_set"] is True
    assert field["value"] == "lega...-key"


def test_reranker_environment_overrides_are_loaded(monkeypatch, tmp_path):
    monkeypatch.setenv("OMBRE_RERANKER_API_KEY", "reranker-key")
    monkeypatch.setenv("OMBRE_RERANKER_BASE_URL", "https://rerank.example/v1")
    monkeypatch.setenv("OMBRE_RERANKER_MODEL", "reranker-model")
    monkeypatch.setenv("OMBRE_RERANKER_ENABLED", "false")

    config = load_config(str(tmp_path / "missing-config.yaml"))

    assert config["reranker"]["api_key"] == "reranker-key"
    assert config["reranker"]["base_url"] == "https://rerank.example/v1"
    assert config["reranker"]["model"] == "reranker-model"
    assert config["reranker"]["enabled"] is False

    engine = RerankerEngine(config)
    assert engine.api_key == "reranker-key"
    assert engine.base_url == "https://rerank.example/v1"
    assert engine.model == "reranker-model"
    assert engine.enabled is False


def test_state_directory_runtime_overlay_and_environment_precedence(
    monkeypatch, tmp_path
):
    buckets_dir = tmp_path / "buckets"
    state_dir = tmp_path / "state"
    runtime_config = state_dir / "config.runtime.yaml"
    state_dir.mkdir()
    runtime_config.write_text(
        "gateway:\n  skip_recent_rounds: 17\nstate_dir: ignored-by-env\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OMBRE_BUCKETS_DIR", str(buckets_dir))
    monkeypatch.setenv("OMBRE_STATE_DIR", str(state_dir))
    monkeypatch.delenv("OMBRE_RUNTIME_CONFIG_PATH", raising=False)

    config = load_config(str(tmp_path / "missing-config.yaml"))

    assert config["state_dir"] == str(state_dir)
    assert config["_runtime_config_path"] == str(runtime_config)
    assert config["gateway"]["skip_recent_rounds"] == 17
    assert state_dir.is_dir()


def test_unreadable_runtime_overlay_does_not_block_startup(monkeypatch, tmp_path):
    buckets_dir = tmp_path / "buckets"
    state_dir = tmp_path / "state"
    runtime_config = state_dir / "config.runtime.yaml"
    state_dir.mkdir()
    runtime_config.touch()
    monkeypatch.setenv("OMBRE_BUCKETS_DIR", str(buckets_dir))
    monkeypatch.setenv("OMBRE_STATE_DIR", str(state_dir))
    monkeypatch.delenv("OMBRE_RUNTIME_CONFIG_PATH", raising=False)
    original_open = open

    def guarded_open(path, *args, **kwargs):
        if os.fspath(path) == str(runtime_config):
            raise PermissionError("runtime overlay unavailable")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded_open)

    config = load_config(str(tmp_path / "missing-config.yaml"))

    assert config["state_dir"] == str(state_dir)
    assert config["_runtime_config_path"] == str(runtime_config)


def test_vault_directory_discovers_sibling_runtime_overlay(monkeypatch, tmp_path):
    vault_dir = tmp_path / "vault"
    state_dir = tmp_path / "state"
    runtime_config = state_dir / "config.runtime.yaml"
    state_dir.mkdir()
    runtime_config.write_text(
        "gateway:\n  skip_recent_rounds: 23\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OMBRE_BUCKETS_DIR", raising=False)
    monkeypatch.delenv("OMBRE_STATE_DIR", raising=False)
    monkeypatch.delenv("OMBRE_RUNTIME_CONFIG_PATH", raising=False)
    monkeypatch.setenv("OMBRE_VAULT_DIR", str(vault_dir))

    config = load_config(str(tmp_path / "missing-config.yaml"))

    assert config["buckets_dir"] == str(vault_dir)
    assert config["state_dir"] == str(state_dir)
    assert config["_runtime_config_path"] == str(runtime_config)
    assert config["gateway"]["skip_recent_rounds"] == 23


@pytest.mark.asyncio
async def test_embedding_provider_tuple_rebuilds_and_persists_once(
    monkeypatch, tmp_path
):
    runtime_config = {
        "embedding": {
            "enabled": True,
            "api_key": "old-key",
            "api_format": "ollama",
            "base_url": "",
            "model": "bge-m3",
        }
    }
    rebuild_snapshots = []
    persisted_env = []

    def rebuild_once():
        rebuild_snapshots.append(dict(runtime_config["embedding"]))
        return SimpleNamespace(enabled=True)

    def persist_once(updates):
        persisted_env.append(dict(updates))

    monkeypatch.setattr(config_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(
        config_api.sh, "_project_env_path", lambda: str(tmp_path / ".env")
    )
    monkeypatch.setattr(config_api.sh, "config", runtime_config)
    monkeypatch.setattr(
        config_api.sh, "embedding_engine", SimpleNamespace(enabled=True)
    )
    monkeypatch.setattr(config_api, "_rebuild_embedding_runtime", rebuild_once)
    monkeypatch.setattr(config_api, "_atomic_update_env_vars", persist_once)
    monkeypatch.setattr(
        config_api,
        "atomic_update_config_yaml",
        lambda _mutate: pytest.fail("env-config must never persist secrets to YAML"),
    )

    updates = {
        "OMBRE_EMBED_API_KEY": "new-key",
        "OMBRE_EMBED_BASE_URL": "https://api.siliconflow.cn/v1",
        "OMBRE_EMBED_MODEL": "BAAI/bge-m3",
        "OMBRE_EMBED_FORMAT": "openai_compat",
    }
    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/env-config")](
        JsonRequest({"updates": updates})
    )
    payload = json.loads(response.body)

    assert payload["ok"] is True
    assert payload["partial"] is False
    assert payload["updated"] == list(updates)
    assert len(rebuild_snapshots) == 1
    assert rebuild_snapshots[0] == {
        "enabled": True,
        "api_key": "new-key",
        "api_format": "openai_compat",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "BAAI/bge-m3",
    }
    assert persisted_env == [updates]


@pytest.mark.asyncio
async def test_clearing_embedding_key_rolls_back_engine_and_all_holders_on_env_failure(
    monkeypatch, tmp_path
):
    from tools import _runtime as tools_runtime

    old_backend = object()
    engine = SimpleNamespace(enabled=True, _backend=old_backend)
    bucket_mgr = SimpleNamespace(embedding_engine=engine)
    import_engine = SimpleNamespace(embedding_engine=engine)
    migrate_engine = SimpleNamespace(_embedding_engine=engine)

    class Outbox:
        embedding_engine = engine

        def set_embedding_engine(self, replacement):
            self.embedding_engine = replacement

    outbox = Outbox()
    runtime_config = {"embedding": {"enabled": True, "api_key": "old-key"}}
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(
        config_api.sh, "_project_env_path", lambda: str(tmp_path / ".env")
    )
    monkeypatch.setattr(config_api.sh, "config", runtime_config)
    monkeypatch.setattr(config_api.sh, "embedding_engine", engine)
    monkeypatch.setattr(config_api.sh, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(config_api.sh, "import_engine", import_engine)
    monkeypatch.setattr(config_api.sh, "migrate_engine", migrate_engine)
    monkeypatch.setattr(config_api.sh, "embedding_outbox", outbox)
    monkeypatch.setattr(tools_runtime, "embedding_engine", engine)
    monkeypatch.setattr(
        config_api,
        "_atomic_update_env_vars",
        lambda _updates: (_ for _ in ()).throw(OSError("bind write failed")),
    )
    monkeypatch.setenv("OMBRE_EMBED_API_KEY", "old-key")

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/env-config")](
        JsonRequest({"updates": {"OMBRE_EMBED_API_KEY": ""}})
    )

    assert response.status_code == 409
    assert runtime_config == {"embedding": {"enabled": True, "api_key": "old-key"}}
    assert os.environ["OMBRE_EMBED_API_KEY"] == "old-key"
    assert engine.enabled is True
    assert engine._backend is old_backend
    assert config_api.sh.embedding_engine is engine
    assert bucket_mgr.embedding_engine is engine
    assert import_engine.embedding_engine is engine
    assert migrate_engine._embedding_engine is engine
    assert tools_runtime.embedding_engine is engine
    assert outbox.embedding_engine is engine
