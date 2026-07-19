from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urljoin

import pytest

import web.auth as auth_routes
import web.buckets as bucket_routes
import web.config_api as config_routes
from tests.compat_web_current.conftest import RecordingMCP, request_for, response_json


ROOT = Path(__file__).resolve().parents[1]
ROOT_DASHBOARD_HTML = (ROOT / "frontend" / "dashboard.html").read_text(
    encoding="utf-8"
)
MEMORY_DASHBOARD_HTML = (ROOT / "frontend" / "memory-dashboard.html").read_text(
    encoding="utf-8"
)
CORE_ROOT = ROOT / "frontend" / "dashboard-assets" / "core"


def _function_body(html: str, name: str) -> str:
    match = re.search(
        rf"async function {re.escape(name)}\([^)]*\) \{{(?P<body>.*?)\n\}}",
        html,
        re.S,
    )
    assert match, f"function {name} not found"
    return match.group("body")


def _catch_body(function_body: str) -> str:
    matches = re.findall(r"catch(?:\s*\([^)]*\))?\s*\{(?P<body>.*?)\n\s*\}", function_body, re.S)
    assert matches, "catch block not found"
    return matches[-1]


def test_memory_dashboard_resolves_api_and_assets_from_the_app_mount() -> None:
    path_source = (CORE_ROOT / "path.js").read_text(encoding="utf-8")
    assert "memory-dashboard" in path_source
    assert "dashboard" in path_source
    assert "basePath" in path_source
    assert "asset" in path_source
    assert 'data-dashboard-shell="unified-dashboard"' in MEMORY_DASHBOARD_HTML


def test_unified_shell_uses_workspaces_instead_of_cross_dashboard_links() -> None:
    assert 'data-dashboard-shell="unified-dashboard"' in ROOT_DASHBOARD_HTML
    assert 'data-workspace="memory"' in ROOT_DASHBOARD_HTML
    assert 'data-workspace="system"' in ROOT_DASHBOARD_HTML
    assert 'data-unified-jump="memory-reminders"' in ROOT_DASHBOARD_HTML
    assert "主 Dashboard" not in MEMORY_DASHBOARD_HTML


def test_canonical_bucket_loader_has_timeout_and_retry_ui() -> None:
    body = _function_body(ROOT_DASHBOARD_HTML, "loadBuckets")
    assert "AbortController" in body
    assert 'id="bucket-load-status"' in ROOT_DASHBOARD_HTML
    assert 'id="bucket-load-retry"' in ROOT_DASHBOARD_HTML
    assert "loadBuckets" not in MEMORY_DASHBOARD_HTML


def test_canonical_bucket_loader_uses_bounded_light_pages_and_discloses_truncation() -> None:
    body = _function_body(ROOT_DASHBOARD_HTML, "loadBuckets")
    assert "'/api/buckets/light'" in body
    assert "limit=" in body
    assert "offset=" in body
    assert "requestedMode === 'archive' ? 'true' : 'false'" in body
    assert re.search(r"/api/buckets(?!/light)", body) is None
    assert "bucketServerTotal" in body
    assert "renderBucketLoadStatus" in body
    assert 'id="bucket-load-more"' in ROOT_DASHBOARD_HTML
    assert "loadMoreBuckets" in ROOT_DASHBOARD_HTML
    assert "currentFilter === 'archived' ? 'archive' : 'active'" in ROOT_DASHBOARD_HTML
    assert "bucketPaginationByMode" in ROOT_DASHBOARD_HTML


def test_canonical_auth_check_is_bounded() -> None:
    body = _function_body(ROOT_DASHBOARD_HTML, "checkAuth")
    assert "AbortController" in body
    assert "status === 502" in body
    assert "status === 503" in body
    assert "status === 504" in body
    assert "controller.signal.aborted" in body
    assert "checkAuth" not in MEMORY_DASHBOARD_HTML


def test_thin_memory_entry_has_no_duplicate_shared_application() -> None:
    assert "function authFetch" not in MEMORY_DASHBOARD_HTML
    assert "function loadBuckets" not in MEMORY_DASHBOARD_HTML
    assert 'data-tab="list"' not in MEMORY_DASHBOARD_HTML
    assert 'data-tab="breath"' not in MEMORY_DASHBOARD_HTML
    assert 'data-tab="network"' not in MEMORY_DASHBOARD_HTML
    assert 'data-tab="import"' not in MEMORY_DASHBOARD_HTML


def test_unified_api_only_retries_idempotent_requests() -> None:
    auth_fetch_body = _function_body(ROOT_DASHBOARD_HTML, "authFetch")
    assert "retryableMethod" in auth_fetch_body
    assert "method === 'GET' || method === 'HEAD'" in auth_fetch_body
    api_source = (CORE_ROOT / "api.js").read_text(encoding="utf-8")
    assert "GET" in api_source
    assert "HEAD" in api_source
    assert "retry" in api_source.lower()


def test_successful_login_reloads_the_authenticated_active_route() -> None:
    root_login = _function_body(ROOT_DASHBOARD_HTML, "doLogin")
    assert "completeDashboardAuthentication" in root_login
    assert "doLogin" not in MEMORY_DASHBOARD_HTML


@pytest.mark.asyncio
async def test_dashboard_compat_redirect_is_mount_relative() -> None:
    mcp = RecordingMCP()
    config_routes.register(mcp)
    query = "workspace=models-data&panel=models-surfacing&q=Amy%20Aki&q=two"
    request = request_for("GET", "/ombre/dashboard", query_string=query)
    request.scope["root_path"] = "/ombre"

    response = await mcp.routes[("GET", "/dashboard")](request)

    assert response.status_code == 302
    assert response.headers["location"] == f"memory-dashboard?{query}"
    assert urljoin(str(request.url), response.headers["location"]) == (
        f"http://testserver/ombre/memory-dashboard?{query}"
    )


def test_root_dashboard_auth_status_errors_fail_closed() -> None:
    catch_body = _catch_body(_function_body(ROOT_DASHBOARD_HTML, "checkAuth"))
    assert "return true;" not in catch_body
    assert "getElementById('auth-overlay').style.display = 'none'" not in catch_body
    assert "return false;" in catch_body


def test_memory_dashboard_does_not_ship_hard_coded_identity_defaults() -> None:
    assert "Rain" not in MEMORY_DASHBOARD_HTML
    assert "Haven" not in MEMORY_DASHBOARD_HTML


def test_root_dashboard_blank_human_name_uses_identity_fallback() -> None:
    assert "留空 = 默认「人类」" not in ROOT_DASHBOARD_HTML
    body = _function_body(ROOT_DASHBOARD_HTML, "saveHumanName")
    assert "|| '人类'" not in body


@pytest.mark.asyncio
async def test_auth_status_returns_minimal_identity_only_when_authenticated(
    monkeypatch,
) -> None:
    monkeypatch.setattr(auth_routes.sh, "_is_authenticated", lambda _request: True)
    monkeypatch.setattr(auth_routes.sh, "_is_setup_needed", lambda: False)
    monkeypatch.setattr(
        auth_routes.sh,
        "config",
        {"identity": {"user_display_name": "Amy", "ai_name": "Aki"}},
    )
    mcp = RecordingMCP()
    auth_routes.register(mcp)

    response = await mcp.routes[("GET", "/auth/status")](
        request_for("GET", "/auth/status")
    )

    assert response.status_code == 200
    assert response_json(response) == {
        "authenticated": True,
        "setup_needed": False,
        "identity": {"user_name": "Amy", "ai_name": "Aki"},
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_auth_status_prefers_explicit_human_override(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.sh, "_is_authenticated", lambda _request: True)
    monkeypatch.setattr(auth_routes.sh, "_is_setup_needed", lambda: False)
    monkeypatch.setattr(
        auth_routes.sh,
        "config",
        {"human": "Ren Lei", "identity": {"user_display_name": "Amy", "ai_name": "Aki"}},
    )
    mcp = RecordingMCP()
    auth_routes.register(mcp)

    response = await mcp.routes[("GET", "/auth/status")](
        request_for("GET", "/auth/status")
    )

    assert response.status_code == 200
    assert response_json(response)["identity"] == {"user_name": "Ren Lei", "ai_name": "Aki"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_auth_status_omits_identity_when_not_authenticated(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.sh, "_is_authenticated", lambda _request: False)
    monkeypatch.setattr(auth_routes.sh, "_is_setup_needed", lambda: True)
    monkeypatch.setattr(
        auth_routes.sh,
        "config",
        {"identity": {"user_display_name": "Amy", "ai_name": "Aki"}},
    )
    mcp = RecordingMCP()
    auth_routes.register(mcp)

    response = await mcp.routes[("GET", "/auth/status")](
        request_for("GET", "/auth/status")
    )

    assert response.status_code == 200
    assert response_json(response) == {
        "authenticated": False,
        "setup_needed": True,
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_human_settings_get_falls_back_to_identity_user_display_name(
    monkeypatch,
) -> None:
    monkeypatch.setattr(bucket_routes.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(
        bucket_routes.sh,
        "config",
        {"identity": {"user_display_name": "Amy", "ai_name": "Aki"}},
    )
    mcp = RecordingMCP()
    bucket_routes.register(mcp)

    response = await mcp.routes[("GET", "/api/settings/human")](
        request_for("GET", "/api/settings/human")
    )

    assert response.status_code == 200
    assert response_json(response) == {"human": "Amy"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_human_settings_get_prefers_explicit_human_over_identity_fallback(
    monkeypatch,
) -> None:
    monkeypatch.setattr(bucket_routes.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(
        bucket_routes.sh,
        "config",
        {"human": "Ren Lei", "identity": {"user_display_name": "Amy", "ai_name": "Aki"}},
    )
    mcp = RecordingMCP()
    bucket_routes.register(mcp)

    response = await mcp.routes[("GET", "/api/settings/human")](
        request_for("GET", "/api/settings/human")
    )

    assert response.status_code == 200
    assert response_json(response) == {"human": "Ren Lei"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_human_settings_get_preserves_legacy_default_without_identity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(bucket_routes.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(bucket_routes.sh, "config", {})
    mcp = RecordingMCP()
    bucket_routes.register(mcp)

    response = await mcp.routes[("GET", "/api/settings/human")](
        request_for("GET", "/api/settings/human")
    )

    assert response.status_code == 200
    assert response_json(response) == {"human": "人类"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_blank_human_setting_clears_override_and_uses_identity(
    monkeypatch,
) -> None:
    class FakeBucketManager:
        @asynccontextmanager
        async def human_name_change_turn(self):
            yield

    config = {
        "human": "Ren Lei",
        "identity": {"user_display_name": "Amy", "ai_name": "Aki"},
    }
    persisted = dict(config)
    rename_calls: list[tuple[str, str]] = []

    def persist(mutate) -> None:
        mutate(persisted)

    async def rename(old: str, new: str) -> dict[str, int]:
        rename_calls.append((old, new))
        return {"buckets_changed": 0, "replacements": 0}

    monkeypatch.setattr(bucket_routes.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(bucket_routes.sh, "config", config)
    monkeypatch.setattr(bucket_routes.sh, "bucket_mgr", FakeBucketManager())
    monkeypatch.setattr(bucket_routes.sh, "dehydrator", SimpleNamespace(human="Ren Lei"))
    monkeypatch.setattr(bucket_routes, "atomic_update_config_yaml", persist)
    monkeypatch.setattr(bucket_routes, "rename_human_in_buckets", rename)
    mcp = RecordingMCP()
    bucket_routes.register(mcp)

    response = await mcp.routes[("POST", "/api/settings/human")](
        request_for("POST", "/api/settings/human", json_body={"human": ""})
    )

    assert response.status_code == 200
    assert response_json(response)["human"] == "Amy"
    assert "human" not in config
    assert "human" not in persisted
    dehydrator = bucket_routes.sh.dehydrator
    assert dehydrator is not None
    assert dehydrator.human == "Amy"
    assert rename_calls == [("Ren Lei", "Amy")]


@pytest.mark.asyncio
async def test_setting_human_from_legacy_blank_config_renames_user_default(
    monkeypatch,
) -> None:
    class FakeBucketManager:
        @asynccontextmanager
        async def human_name_change_turn(self):
            yield

    config: dict[str, object] = {}
    persisted: dict[str, object] = {}
    rename_calls: list[tuple[str, str]] = []

    def persist(mutate) -> None:
        mutate(persisted)

    async def rename(old: str, new: str) -> dict[str, int]:
        rename_calls.append((old, new))
        return {"buckets_changed": 1, "replacements": 1}

    monkeypatch.setattr(bucket_routes.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(bucket_routes.sh, "config", config)
    monkeypatch.setattr(bucket_routes.sh, "bucket_mgr", FakeBucketManager())
    monkeypatch.setattr(bucket_routes.sh, "dehydrator", SimpleNamespace(human="用户"))
    monkeypatch.setattr(bucket_routes, "atomic_update_config_yaml", persist)
    monkeypatch.setattr(bucket_routes, "rename_human_in_buckets", rename)
    mcp = RecordingMCP()
    bucket_routes.register(mcp)

    response = await mcp.routes[("POST", "/api/settings/human")](
        request_for("POST", "/api/settings/human", json_body={"human": "Amy"})
    )

    assert response.status_code == 200
    assert config["human"] == "Amy"
    assert persisted["human"] == "Amy"
    assert rename_calls == [("用户", "Amy")]


@pytest.mark.asyncio
async def test_clearing_human_without_identity_persists_restart_stable_default(
    monkeypatch,
) -> None:
    class FakeBucketManager:
        @asynccontextmanager
        async def human_name_change_turn(self):
            yield

    config: dict[str, object] = {"human": "Ren Lei"}
    persisted: dict[str, object] = dict(config)
    rename_calls: list[tuple[str, str]] = []

    def persist(mutate) -> None:
        mutate(persisted)

    async def rename(old: str, new: str) -> dict[str, int]:
        rename_calls.append((old, new))
        return {"buckets_changed": 0, "replacements": 0}

    monkeypatch.setattr(bucket_routes.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(bucket_routes.sh, "config", config)
    monkeypatch.setattr(bucket_routes.sh, "bucket_mgr", FakeBucketManager())
    monkeypatch.setattr(bucket_routes.sh, "dehydrator", SimpleNamespace(human="Ren Lei"))
    monkeypatch.setattr(bucket_routes, "atomic_update_config_yaml", persist)
    monkeypatch.setattr(bucket_routes, "rename_human_in_buckets", rename)
    mcp = RecordingMCP()
    bucket_routes.register(mcp)

    response = await mcp.routes[("POST", "/api/settings/human")](
        request_for("POST", "/api/settings/human", json_body={"human": ""})
    )

    assert response.status_code == 200
    assert response_json(response)["human"] == "人类"
    assert config["human"] == "人类"
    assert persisted["human"] == "人类"
    assert rename_calls == [("Ren Lei", "人类")]


@pytest.mark.asyncio
async def test_blank_human_rejects_invalid_identity_fallback(monkeypatch) -> None:
    class FakeBucketManager:
        @asynccontextmanager
        async def human_name_change_turn(self):
            yield

    config = {"identity": {"user_display_name": "A" * 21}}
    monkeypatch.setattr(bucket_routes.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(bucket_routes.sh, "config", config)
    monkeypatch.setattr(bucket_routes.sh, "bucket_mgr", FakeBucketManager())
    mcp = RecordingMCP()
    bucket_routes.register(mcp)

    response = await mcp.routes[("POST", "/api/settings/human")](
        request_for("POST", "/api/settings/human", json_body={"human": ""})
    )

    assert response.status_code == 400
    assert "20" in response_json(response)["error"]


@pytest.mark.asyncio
async def test_sync_existing_uses_effective_human_name_when_override_missing(
    monkeypatch,
) -> None:
    class FakeBucketManager:
        @asynccontextmanager
        async def human_name_change_turn(self):
            yield

    rename_calls: list[tuple[str, str]] = []

    async def rename(old: str, new: str) -> dict[str, int]:
        rename_calls.append((old, new))
        return {"buckets_changed": 0, "replacements": 0}

    monkeypatch.setattr(bucket_routes.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(
        bucket_routes.sh,
        "config",
        {"identity": {"user_display_name": "Amy", "ai_name": "Aki"}},
    )
    monkeypatch.setattr(bucket_routes.sh, "bucket_mgr", FakeBucketManager())
    monkeypatch.setattr(bucket_routes, "rename_human_in_buckets", rename)
    mcp = RecordingMCP()
    bucket_routes.register(mcp)

    response = await mcp.routes[("POST", "/api/settings/human/sync-existing")](
        request_for("POST", "/api/settings/human/sync-existing", json_body={"from": "用户"})
    )

    assert response.status_code == 200
    assert response_json(response) == {
        "ok": True,
        "from": "用户",
        "to": "Amy",
        "renamed": {"buckets_changed": 0, "replacements": 0},
    }
    assert response.headers["cache-control"] == "no-store"
    assert rename_calls == [("用户", "Amy")]


@pytest.mark.asyncio
async def test_sensitive_bucket_gets_return_no_store_headers(monkeypatch) -> None:
    class FakeBucketManager:
        async def list_all(self, include_archive: bool):
            assert include_archive is True
            return [
                {
                    "id": "bucket-1",
                    "content": "secret",
                    "metadata": {
                        "name": "bucket-1",
                        "created": "2026-07-19T00:00:00Z",
                        "last_active": "2026-07-19T00:00:00Z",
                        "type": "dynamic",
                        "domain": [],
                        "tags": [],
                    },
                }
            ]

        async def get(self, bucket_id: str):
            if bucket_id != "bucket-1":
                return None
            return {
                "id": "bucket-1",
                "content": "secret",
                "metadata": {"created": "2026-07-19T00:00:00Z", "tags": []},
            }

        async def get_triggered_feels(self, bucket_id: str):
            return []

    monkeypatch.setattr(bucket_routes.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(bucket_routes.sh, "bucket_mgr", FakeBucketManager())
    monkeypatch.setattr(
        bucket_routes.sh,
        "decay_engine",
        SimpleNamespace(calculate_score=lambda _meta: 0.5),
    )
    mcp = RecordingMCP()
    bucket_routes.register(mcp)

    buckets_response = await mcp.routes[("GET", "/api/buckets")](request_for("GET", "/api/buckets"))
    detail_response = await mcp.routes[("GET", "/api/bucket/{bucket_id}")](
        request_for(
            "GET",
            "/api/bucket/bucket-1",
            path_params={"bucket_id": "bucket-1"},
        )
    )
    assert buckets_response.headers["cache-control"] == "no-store"
    assert detail_response.headers["cache-control"] == "no-store"
