from __future__ import annotations

import asyncio
import os
import threading
from types import SimpleNamespace
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

import web.current_operations as current_operations
from web.current_compat import CurrentWebDependencies, CurrentWebServices, register_current_routes
from web.current_contract import refresh_bucket_indexes
from web.current_operations import _refresh_restore_indexes
from web.current_profile import _profile_update_direct

from .conftest import RecordingMCP, request_for, response_json


def register_with(**kwargs):
    mcp = RecordingMCP()
    deps = CurrentWebDependencies(config={}, **kwargs)
    register_current_routes(mcp, deps)
    return mcp.routes


async def _consume_file_response(response, *, headers=None, fail_send=False):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if fail_send:
            raise ConnectionError("client disconnected")
        messages.append(message)

    await response(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/backup/export",
            "headers": headers or [],
        },
        receive,
        send,
    )
    return messages


@pytest.mark.asyncio
async def test_api_routes_use_injected_dashboard_auth_guard():
    unauthorized = JSONResponse({"error": "Unauthorized"}, status_code=401)
    routes = register_with(auth_guard=lambda _request: unauthorized)

    response = await routes[("GET", "/api/domain-taxonomy")](
        request_for("GET", "/api/domain-taxonomy")
    )

    assert response is unauthorized


@pytest.mark.asyncio
async def test_missing_dependency_is_a_stable_503_json_error():
    routes = register_with(auth_guard=lambda _request: None)

    response = await routes[("GET", "/api/darkroom/status")](
        request_for("GET", "/api/darkroom/status")
    )

    assert response.status_code == 503
    assert response_json(response) == {
        "error": "current web dependency unavailable: darkroom_store"
    }


@pytest.mark.asyncio
async def test_anchor_confirmation_fails_closed_without_guarded_service():
    routes = register_with(auth_guard=lambda _request: None)

    response = await routes[("POST", "/api/anchor-proposals/confirm")](
        request_for(
            "POST",
            "/api/anchor-proposals/confirm",
            json_body={"bucket_id": "memory-1", "reason": "stable"},
        )
    )

    assert response.status_code == 503
    assert response_json(response) == {
        "error": "current web dependency unavailable: services.anchor_confirm"
    }


@pytest.mark.asyncio
async def test_portrait_delete_rejects_non_object_json_before_engine_call():
    engine = SimpleNamespace(delete_state_item=lambda **_kwargs: pytest.fail("called"))
    routes = register_with(
        auth_guard=lambda _request: None,
        portrait_engine=engine,
    )

    response = await routes[("DELETE", "/api/portrait-state/items")](
        request_for("DELETE", "/api/portrait-state/items", json_body=[])
    )

    assert response.status_code == 400
    assert response_json(response) == {"error": "json body must be an object"}


@pytest.mark.asyncio
async def test_daily_chat_confirm_requires_action_specific_confirmation():
    reflection = SimpleNamespace(
        confirm_daily_chat_memory=lambda *_args, **_kwargs: pytest.fail("called")
    )
    routes = register_with(
        auth_guard=lambda _request: None,
        reflection_engine=reflection,
    )

    response = await routes[("POST", "/api/daily-chat-memory/confirm")](
        request_for(
            "POST",
            "/api/daily-chat-memory/confirm",
            json_body={
                "action": "reject",
                "confirm": "WRITE",
                "candidate_ids": ["candidate-1"],
            },
        )
    )

    assert response.status_code == 400
    assert response_json(response) == {"error": "confirmation required: REJECT"}


@pytest.mark.asyncio
async def test_profile_proposal_route_delegates_to_explicit_service():
    calls = []

    async def propose(payload):
        calls.append(payload)
        return {"status": "ok", "proposals": [{"fact": "keeps promises"}]}

    routes = register_with(
        auth_guard=lambda _request: None,
        services=CurrentWebServices(profile_fact_proposals=propose),
    )

    response = await routes[("POST", "/api/profile-fact-proposals")](
        request_for(
            "POST",
            "/api/profile-fact-proposals",
            json_body={"bucket_id": "memory-1", "max_proposals": 3},
        )
    )

    assert response.status_code == 200
    assert calls == [{"bucket_id": "memory-1", "max_proposals": 3}]
    assert response_json(response)["proposals"][0]["fact"] == "keeps promises"


@pytest.mark.asyncio
async def test_optional_body_route_rejects_malformed_json_before_engine_call():
    reflection = SimpleNamespace(reflect=lambda **_kwargs: pytest.fail("called"))
    routes = register_with(
        auth_guard=lambda _request: None,
        reflection_engine=reflection,
        bucket_mgr=object(),
    )

    response = await routes[("POST", "/api/reflection/run")](
        request_for(
            "POST",
            "/api/reflection/run",
            raw_body=b'{"period":',
            headers={"content-type": "application/json"},
        )
    )

    assert response.status_code == 400
    assert response_json(response) == {"error": "invalid json body"}


@pytest.mark.asyncio
async def test_oauth_post_alias_uses_307_to_preserve_method_and_body():
    routes = register_with()

    response = await routes[("POST", "/mcp/oauth/token")](
        request_for("POST", "/mcp/oauth/token", raw_body=b"grant_type=refresh_token")
    )

    assert response.status_code == 307
    assert response.headers["location"] == "/oauth/token"


@pytest.mark.asyncio
async def test_oauth_discovery_alias_returns_canonical_metadata_shape(monkeypatch):
    monkeypatch.setenv("OMBRE_CHATGPT_OAUTH_CLIENT_ID", "fixed-client")
    monkeypatch.setenv("OMBRE_CHATGPT_OAUTH_ACCESS_TOKEN", "fixed-access")
    monkeypatch.setenv("OMBRE_CHATGPT_OAUTH_CLIENT_SECRET", "fixed-secret")
    mcp = RecordingMCP()
    config = {"mcp_require_auth": True, "mcp_auth_mode": "oauth"}
    register_current_routes(
        mcp,
        CurrentWebDependencies(config=config),
    )
    config["mcp_auth_mode"] = "token"
    monkeypatch.setenv("OMBRE_CHATGPT_OAUTH_CLIENT_SECRET", "")

    response = await mcp.routes[("GET", "/.well-known/openid-configuration")](
        request_for("GET", "/.well-known/openid-configuration")
    )

    payload = response_json(response)
    assert response.status_code == 200
    assert payload["issuer"] == "http://testserver"
    assert payload["authorization_endpoint"].endswith("/oauth/authorize")
    assert payload["code_challenge_methods_supported"] == ["S256"]
    assert payload["token_endpoint_auth_methods_supported"] == [
        "none",
        "client_secret_post",
        "client_secret_basic",
    ]


@pytest.mark.asyncio
async def test_oauth_resource_alias_accepts_only_the_canonical_mcp_path(monkeypatch):
    monkeypatch.setenv("OMBRE_CHATGPT_OAUTH_CLIENT_ID", "fixed-client")
    monkeypatch.setenv("OMBRE_CHATGPT_OAUTH_ACCESS_TOKEN", "fixed-access")
    monkeypatch.setenv("OMBRE_CHATGPT_OAUTH_CLIENT_SECRET", "fixed-secret")
    mcp = RecordingMCP()
    register_current_routes(
        mcp,
        CurrentWebDependencies(
            config={"mcp_require_auth": True, "mcp_auth_mode": "oauth"}
        ),
    )
    route = mcp.routes[
        ("GET", "/mcp/.well-known/oauth-protected-resource/{resource_path:path}")
    ]

    accepted = await route(
        request_for(
            "GET",
            "/mcp/.well-known/oauth-protected-resource/mcp",
            path_params={"resource_path": "mcp"},
        )
    )
    rejected = await route(
        request_for(
            "GET",
            "/mcp/.well-known/oauth-protected-resource/retired",
            path_params={"resource_path": "retired"},
        )
    )

    assert accepted.status_code == 200
    assert response_json(accepted)["resource"].endswith("/mcp")
    assert rejected.status_code == 404


@pytest.mark.asyncio
async def test_nested_dashboard_asset_is_served_and_traversal_is_rejected(tmp_path):
    asset_root = tmp_path / "dashboard-assets"
    nested = asset_root / "modules"
    nested.mkdir(parents=True)
    expected = nested / "panel.js"
    expected.write_text("export const panel = true;", encoding="utf-8")
    (tmp_path / "secret.js").write_text("secret", encoding="utf-8")
    routes = register_with(asset_root=asset_root)

    response = await routes[("GET", "/dashboard-assets/{path:path}")](
        request_for(
            "GET",
            "/dashboard-assets/modules/panel.js",
            path_params={"path": "modules/panel.js"},
        )
    )
    blocked = await routes[("GET", "/dashboard-assets/{path:path}")](
        request_for(
            "GET",
            "/dashboard-assets/../secret.js",
            path_params={"path": "../secret.js"},
        )
    )

    assert response.status_code == 200
    assert Path(response.path) == expected
    assert blocked.status_code == 404


@pytest.mark.asyncio
async def test_backup_export_builds_archive_without_blocking_request_loop(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    archive_path = tmp_path / "responsive-export.zip"

    class BackupManager:
        def create_archive(self):
            archive_path.write_bytes(b"responsive archive")
            entered.set()
            release.wait(timeout=1)
            return str(archive_path), {"file_count": 0}

    routes = register_with(
        auth_guard=lambda _request: None,
        backup_manager=BackupManager(),
    )
    timer = threading.Timer(0.8, release.set)
    timer.start()
    heartbeat = threading.Event()
    task = asyncio.create_task(
        routes[("GET", "/api/backup/export")](
            request_for("GET", "/api/backup/export")
        )
    )

    async def pulse_event_loop():
        await asyncio.sleep(0)
        heartbeat.set()

    pulse = asyncio.create_task(pulse_event_loop())
    try:
        assert await asyncio.to_thread(heartbeat.wait, 0.3)
        assert await asyncio.to_thread(entered.wait, 0.5)
        release.set()
        await pulse
        response = await asyncio.wait_for(task, timeout=1.5)
        await _consume_file_response(response)
    finally:
        release.set()
        timer.cancel()


@pytest.mark.asyncio
async def test_backup_export_prepare_observes_errors_and_streams_by_one_time_ticket(
    tmp_path,
):
    archive_path = tmp_path / "prepared-export.zip"

    class BackupManager:
        def create_archive(self):
            archive_path.write_bytes(b"prepared archive")
            return str(archive_path), {"file_count": 1}

    routes = register_with(
        auth_guard=lambda _request: None,
        backup_manager=BackupManager(),
    )
    prepare = routes[("POST", "/api/backup/export/prepare")]
    status = routes[("GET", "/api/backup/export/status")]
    export = routes[("GET", "/api/backup/export")]

    prepared = await prepare(request_for("POST", "/api/backup/export/prepare"))
    payload = response_json(prepared)
    busy = response_json(
        await status(request_for("GET", "/api/backup/export/status"))
    )
    download = await export(
        request_for(
            "GET",
            "/api/backup/export",
            query_string=f"ticket={payload['ticket']}",
        )
    )

    assert prepared.status_code == 200
    assert payload["ok"] is True
    assert payload["ticket"]
    assert busy == {"ok": True, "active": True}
    assert download.status_code == 200
    await _consume_file_response(download)
    assert response_json(
        await status(request_for("GET", "/api/backup/export/status"))
    ) == {"ok": True, "active": False}
    reused = await export(
        request_for(
            "GET",
            "/api/backup/export",
            query_string=f"ticket={payload['ticket']}",
        )
    )
    assert reused.status_code == 410


@pytest.mark.asyncio
async def test_backup_export_prepare_reports_auth_busy_and_archive_failures(tmp_path):
    called = False

    class NeverManager:
        def create_archive(self):
            nonlocal called
            called = True
            return str(tmp_path / "never.zip"), {}

    unauthorized_routes = register_with(
        auth_guard=lambda _request: JSONResponse({"error": "login"}, status_code=401),
        backup_manager=NeverManager(),
    )
    unauthorized = await unauthorized_routes[
        ("POST", "/api/backup/export/prepare")
    ](request_for("POST", "/api/backup/export/prepare"))
    assert unauthorized.status_code == 401
    assert called is False

    release = threading.Event()
    entered = threading.Event()

    class BlockingManager:
        def create_archive(self):
            path = tmp_path / "blocking.zip"
            path.write_bytes(b"blocking")
            entered.set()
            release.wait(timeout=2)
            return str(path), {}

    busy_routes = register_with(
        auth_guard=lambda _request: None,
        backup_manager=BlockingManager(),
    )
    export_task = asyncio.create_task(
        busy_routes[("GET", "/api/backup/export")](
            request_for("GET", "/api/backup/export")
        )
    )
    assert await asyncio.to_thread(entered.wait, 1)
    busy = await busy_routes[("POST", "/api/backup/export/prepare")](
        request_for("POST", "/api/backup/export/prepare")
    )
    assert busy.status_code == 409
    release.set()
    await _consume_file_response(await export_task)

    class FailingManager:
        def create_archive(self):
            raise OSError("archive creation failed")

    failing_routes = register_with(
        auth_guard=lambda _request: None,
        backup_manager=FailingManager(),
    )
    failed = await failing_routes[("POST", "/api/backup/export/prepare")](
        request_for("POST", "/api/backup/export/prepare")
    )
    assert failed.status_code == 500
    assert "archive creation failed" in response_json(failed)["error"]
    assert response_json(
        await failing_routes[("GET", "/api/backup/export/status")](
            request_for("GET", "/api/backup/export/status")
        )
    ) == {"ok": True, "active": False}


@pytest.mark.asyncio
async def test_backup_restore_runs_blocking_archive_work_off_request_loop():
    entered = threading.Event()
    release = threading.Event()

    class BackupManager:
        async def restore_archive(self, _archive_path, *, mode):
            assert mode == "overwrite"
            entered.set()
            release.wait(timeout=1)
            return {
                "status": "restored",
                "restored_ids": [],
                "embedding_snapshot": "restored",
            }

    routes = register_with(
        auth_guard=lambda _request: None,
        backup_manager=BackupManager(),
        services=CurrentWebServices(
            refresh_restore_indexes=lambda _bucket_ids: {
                "refreshed": 0,
                "errors": [],
            }
        ),
    )
    timer = threading.Timer(0.8, release.set)
    timer.start()
    heartbeat = threading.Event()
    task = asyncio.create_task(
        routes[("POST", "/api/backup/restore")](
            request_for(
                "POST",
                "/api/backup/restore",
                raw_body=b"backup",
                query_string="mode=overwrite",
                headers={"content-type": "application/zip"},
            )
        )
    )

    async def pulse_event_loop():
        await asyncio.sleep(0)
        heartbeat.set()

    pulse = asyncio.create_task(pulse_event_loop())
    try:
        assert await asyncio.to_thread(heartbeat.wait, 0.3)
        assert await asyncio.to_thread(entered.wait, 0.5)
        release.set()
        await pulse
        response = await asyncio.wait_for(task, timeout=1.5)
        assert response.status_code == 200
    finally:
        release.set()
        timer.cancel()


@pytest.mark.asyncio
async def test_backup_vault_admission_is_shared_and_cross_loop_safe(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    archive_path = tmp_path / "held-export.zip"

    class BackupManager:
        def create_archive(self):
            archive_path.write_bytes(b"held archive")
            entered.set()
            release.wait(timeout=2)
            return str(archive_path), {"file_count": 0}

        async def restore_archive(self, *_args, **_kwargs):
            pytest.fail("concurrent restore must not reach the manager")

    routes = register_with(
        auth_guard=lambda _request: None,
        backup_manager=BackupManager(),
    )
    export = routes[("GET", "/api/backup/export")]
    restore = routes[("POST", "/api/backup/restore")]
    first_task = asyncio.create_task(
        export(request_for("GET", "/api/backup/export"))
    )
    assert await asyncio.to_thread(entered.wait, 1)

    def invoke_restore_on_another_loop():
        return asyncio.run(
            restore(
                request_for(
                    "POST",
                    "/api/backup/restore",
                    raw_body=b"must-not-be-read",
                    query_string="mode=overwrite",
                    headers={"content-type": "application/zip"},
                )
            )
        )

    try:
        rejected = await asyncio.to_thread(invoke_restore_on_another_loop)
        assert rejected.status_code == 409
        assert "backup operation" in response_json(rejected)["error"].lower()
    finally:
        release.set()
    first = await asyncio.wait_for(first_task, timeout=1)
    await _consume_file_response(first)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["range", "disconnect"])
async def test_backup_export_always_cleans_archive_and_releases_reservation(
    tmp_path,
    failure_mode,
):
    created = []

    class BackupManager:
        def create_archive(self):
            archive_path = tmp_path / f"export-{len(created)}.zip"
            archive_path.write_bytes(b"verified archive")
            created.append(archive_path)
            return str(archive_path), {"file_count": 0}

    routes = register_with(
        auth_guard=lambda _request: None,
        backup_manager=BackupManager(),
    )
    handler = routes[("GET", "/api/backup/export")]
    response = await handler(request_for("GET", "/api/backup/export"))
    archive_path = Path(response.path)

    if failure_mode == "range":
        await _consume_file_response(
            response,
            headers=[(b"range", b"bytes=999999999-")],
        )
    else:
        with pytest.raises(ConnectionError, match="client disconnected"):
            await _consume_file_response(response, fail_send=True)

    assert not os.path.exists(archive_path)
    retry = await handler(request_for("GET", "/api/backup/export"))
    assert retry.status_code == 200
    await _consume_file_response(retry)


@pytest.mark.asyncio
async def test_backup_restore_rejects_unknown_mode_before_upload_or_store_access():
    routes = register_with(
        auth_guard=lambda _request: None,
        backup_manager=SimpleNamespace(
            restore_archive=lambda *_args, **_kwargs: pytest.fail("called")
        ),
    )

    response = await routes[("POST", "/api/backup/restore")](
        request_for(
            "POST",
            "/api/backup/restore",
            query_string="mode=replace",
            raw_body=b"not-a-zip",
        )
    )

    assert response.status_code == 400
    assert response_json(response) == {"error": "mode must be skip or overwrite"}


@pytest.mark.asyncio
async def test_backup_restore_bounds_chunked_multipart_before_store_access(
    monkeypatch,
):
    monkeypatch.setattr(current_operations, "MAX_ARCHIVE_BYTES", 8)
    boundary = "ombre-backup-boundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="backup.zip"\r\n'
        "Content-Type: application/zip\r\n\r\n"
    ).encode("ascii") + (b"x" * (1024 * 1024 + 64)) + f"\r\n--{boundary}--\r\n".encode("ascii")
    chunks = [body[index:index + 16_384] for index in range(0, len(body), 16_384)]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/backup/restore",
        "raw_path": b"/api/backup/restore",
        "query_string": b"mode=overwrite",
        "headers": [
            (
                b"content-type",
                f"multipart/form-data; boundary={boundary}".encode("ascii"),
            )
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "path_params": {},
    }
    chunk_index = 0

    async def receive():
        nonlocal chunk_index
        if chunk_index >= len(chunks):
            return {"type": "http.disconnect"}
        chunk = chunks[chunk_index]
        chunk_index += 1
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": chunk_index < len(chunks),
        }

    routes = register_with(
        auth_guard=lambda _request: None,
        backup_manager=SimpleNamespace(
            restore_archive=lambda *_args, **_kwargs: pytest.fail("called")
        ),
    )

    response = await routes[("POST", "/api/backup/restore")](
        Request(scope, receive)
    )

    assert response.status_code == 400
    assert "Upload too large" in response_json(response)["error"]


@pytest.mark.asyncio
async def test_backup_restore_awaits_manager_and_refreshes_restored_indexes():
    calls = []

    class BackupManager:
        async def restore_archive(self, archive_path, *, mode):
            calls.append((Path(archive_path).read_bytes(), mode))
            return {
                "status": "restored",
                "restored_ids": ["memory-1"],
                "embedding_snapshot": "restored",
            }

    async def refresh_indexes(bucket_ids):
        calls.append(("refresh", bucket_ids))
        return {"refreshed": len(bucket_ids), "errors": []}

    routes = register_with(
        auth_guard=lambda _request: None,
        backup_manager=BackupManager(),
        services=CurrentWebServices(refresh_restore_indexes=refresh_indexes),
    )

    response = await routes[("POST", "/api/backup/restore")](
        request_for(
            "POST",
            "/api/backup/restore",
            raw_body=b"compat-backup",
            query_string="mode=overwrite",
            headers={"content-type": "application/zip"},
        )
    )

    assert response.status_code == 200
    assert calls == [
        (b"compat-backup", "overwrite"),
        ("refresh", ["memory-1"]),
    ]
    assert response_json(response) == {
        "status": "restored",
        "restored_ids": ["memory-1"],
        "embedding_snapshot": "restored",
        "scope": "memory-vault",
        "derived_indexes": {"refreshed": 1, "errors": []},
        "embeddings_queued": 0,
    }


@pytest.mark.asyncio
async def test_restore_fallback_rebuilds_deterministic_entity_edges():
    calls = []
    bucket = {
        "id": "memory-1",
        "content": "艾米喜欢海鲜",
        "metadata": {"type": "dynamic", "tags": []},
    }

    class Manager:
        async def get(self, _bucket_id):
            return bucket

        async def list_all(self, *, include_archive):
            assert include_archive is True
            return [bucket]

    def store(name, *, upsert=False, replace=False):
        methods: dict[str, Any] = {
            "delete_for_bucket": lambda bucket_id: calls.append(
                (name, "delete", bucket_id)
            )
        }
        if upsert:
            methods["upsert_bucket"] = lambda value: calls.append((name, "upsert", value["id"]))
        if replace:
            methods["replace_bucket_edges"] = lambda bucket_id, edges: calls.append(
                (name, "replace", bucket_id, len(edges))
            )
        return SimpleNamespace(**methods)

    async def rebuild_memory_edges(bucket_id, manager, edge_store, embedding, *, dry_run):
        calls.append(
            (
                "memory_edges",
                "backfill",
                bucket_id,
                manager is dependencies.bucket_mgr,
                edge_store is dependencies.memory_edge_store,
                embedding,
                dry_run,
            )
        )

    dependencies = CurrentWebDependencies(
        config={"identity": {"user_name": "艾米", "ai_name": "秋"}},
        bucket_mgr=Manager(),
        memory_moment_store=store("moments", upsert=True),
        memory_node_store=SimpleNamespace(
            delete=lambda bucket_id: calls.append(("nodes", "delete", bucket_id)),
            upsert_bucket=lambda value: calls.append(("nodes", "upsert", value["id"])),
        ),
        memory_edge_store=store("memory_edges"),
        entity_edge_store=store("entity_edges", replace=True),
        reflection_engine=SimpleNamespace(
            backfill_edges_for_bucket=rebuild_memory_edges
        ),
    )

    result = await _refresh_restore_indexes(dependencies, ["memory-1"])

    assert result == {"refreshed": 1, "errors": []}
    assert ("entity_edges", "replace", "memory-1", 1) in calls
    assert (
        "memory_edges",
        "backfill",
        "memory-1",
        True,
        True,
        None,
        False,
    ) in calls


@pytest.mark.asyncio
async def test_direct_index_refresh_updates_moments_nodes_and_entity_edges():
    calls = []
    bucket = {
        "id": "memory-1",
        "content": "艾米喜欢海鲜",
        "metadata": {"type": "dynamic"},
    }
    dependencies = CurrentWebDependencies(
        config={"identity": {"user_name": "艾米", "ai_name": "秋"}},
        memory_moment_store=SimpleNamespace(
            upsert_bucket=lambda value: calls.append(("moments", value["id"]))
        ),
        memory_node_store=SimpleNamespace(
            upsert_bucket=lambda value: calls.append(("nodes", value["id"]))
        ),
        entity_edge_store=SimpleNamespace(
            replace_bucket_edges=lambda bucket_id, edges: calls.append(
                ("entities", bucket_id, len(edges))
            )
        ),
    )

    await refresh_bucket_indexes(dependencies, bucket)

    assert calls == [
        ("moments", "memory-1"),
        ("nodes", "memory-1"),
        ("entities", "memory-1", 1),
    ]


@pytest.mark.asyncio
async def test_profile_deprecate_refreshes_derived_indexes():
    calls = []
    bucket = {
        "id": "profile-1",
        "content": "### fact\n艾米喜欢海鲜",
        "metadata": {
            "tags": ["profile_fact", "profile_preference"],
            "created": "2026-01-01T00:00:00+00:00",
            "active": True,
        },
    }

    class Manager:
        async def get(self, _bucket_id):
            return bucket

        async def update(self, _bucket_id, **updates):
            bucket["metadata"].update(updates)
            return True

    response = await _profile_update_direct(
        CurrentWebDependencies(
            config={},
            bucket_mgr=Manager(),
            memory_node_store=SimpleNamespace(
                upsert_bucket=lambda value: calls.append(value["id"])
            ),
        ),
        "profile-1",
        {"action": "deprecate"},
    )

    assert response.status_code == 200
    assert calls == ["profile-1"]


@pytest.mark.asyncio
async def test_profile_edit_preserves_dashboard_key_and_section_contracts():
    bucket = {
        "id": "profile-1",
        "content": "### fact\nOld fact\n\n### evidence-context\nOriginal evidence",
        "metadata": {
            "tags": ["profile_fact", "profile_preference"],
            "profile_kind": "preference",
            "created": "2026-01-01T00:00:00+00:00",
        },
    }

    class Manager:
        async def get(self, _bucket_id):
            return bucket

        async def update(self, _bucket_id, **updates):
            bucket["content"] = updates.pop("content", bucket["content"])
            bucket["metadata"].update(updates)
            return True

    response = await _profile_update_direct(
        CurrentWebDependencies(config={}, bucket_mgr=Manager()),
        "profile-1",
        {
            "action": "edit",
            "fact": "Updated fact",
            "profile_kind": "Favorite-Food",
            "subject": " Amy Person ",
            "predicate": "Likes Food!",
        },
    )

    assert response.status_code == 200
    assert bucket["metadata"]["profile_kind"] == "favorite_food"
    assert bucket["metadata"]["subject"] == "amy_person"
    assert bucket["metadata"]["predicate"] == "likes_food"
    assert "profile_favorite_food" in bucket["metadata"]["tags"]
    assert "### evidence_context\nOriginal evidence" in bucket["content"]


@pytest.mark.asyncio
async def test_restore_reindex_reports_missing_bucket_as_error():
    class Manager:
        async def get(self, _bucket_id):
            return None

        async def list_all(self, *, include_archive):
            assert include_archive is True
            return []

    result = await _refresh_restore_indexes(
        CurrentWebDependencies(config={}, bucket_mgr=Manager()),
        ["missing-1"],
    )

    assert result == {"refreshed": 0, "errors": ["missing-1"]}
