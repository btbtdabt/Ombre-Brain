import asyncio
import json
import threading

import pytest

import web.import_api as import_api
from import_memory import preview_import


class FakeMCP:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(fn):
            for method in methods:
                self.routes[(method, path)] = fn
            return fn

        return decorator


class BodyRequest:
    def __init__(self, body: str, filename: str = "upload.md"):
        self.headers = {}
        self.query_params = {"filename": filename}
        self._body = body.encode("utf-8")

    async def body(self):
        return self._body


class FakeDehydrator:
    api_available = True


class FakeImportEngine:
    is_running = False
    dehydrator = FakeDehydrator()


def test_preview_import_counts_turns_chunks_and_estimated_calls():
    from import_memory import preview_import

    raw = "Human: 我喜欢茶\nAssistant: 我记住了\nUser: 明天提醒我整理导入体验"

    preview = preview_import(raw, filename="chat.md", human_label="阿立")

    assert preview["ok"] is True
    assert preview["detected_format"] == "markdown"
    assert preview["turns_count"] == 3
    assert preview["chunks_count"] == 1
    assert preview["estimated_api_calls"] == 1
    assert "[阿立]" in preview["first_chunk_preview"]


def test_preview_import_warns_when_invalid_json_falls_back_to_text():
    from import_memory import preview_import

    preview = preview_import("{not json", filename="bad.json")

    assert preview["ok"] is True
    assert preview["detected_format"] == "text"
    assert preview["turns_count"] == 1
    assert any("JSON" in warning for warning in preview["warnings"])


def test_preview_conversation_mode_bypasses_operit_detection():
    preview = preview_import(
        '{"exportDate":1,"memories":[{"uuid":"a","content":"exact"}]}',
        filename="operit.json",
        import_mode="conversation",
    )

    assert preview["ok"] is True
    assert preview["detected_format"] != "operit"


@pytest.mark.asyncio
async def test_import_preflight_route_returns_preview_with_runtime_readiness(monkeypatch):
    monkeypatch.setattr(import_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(import_api.sh, "import_engine", FakeImportEngine())
    monkeypatch.setattr(import_api.sh, "config", {"human": "阿立"})

    mcp = FakeMCP()
    import_api.register(mcp)

    response = await mcp.routes[("POST", "/api/import/preflight")](
        BodyRequest("Human: hi\nAssistant: hello", filename="chat.md")
    )
    payload = json.loads(response.body)

    assert payload["ok"] is True
    assert payload["can_start"] is True
    assert payload["llm_ready"] is True
    assert payload["import_running"] is False
    assert payload["filename"] == "chat.md"
    assert payload["turns_count"] == 2
    assert payload["chunks_count"] == 1


@pytest.mark.asyncio
async def test_import_preflight_honors_explicit_operit_mode_and_tagging(monkeypatch):
    class NoLlmDehydrator:
        api_available = False

    class OperitImportEngine:
        is_running = False
        dehydrator = NoLlmDehydrator()
        operit_tagging_enabled = True

    monkeypatch.setattr(import_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(import_api.sh, "import_engine", OperitImportEngine())
    monkeypatch.setattr(import_api.sh, "config", {"human": "Amy"})
    mcp = FakeMCP()
    import_api.register(mcp)
    request = BodyRequest(
        '{"exportDate":1,"memories":[{"uuid":"a","content":"exact"}]}',
        filename="operit.json",
    )
    request.query_params.update(
        {"import_mode": "operit", "operit_tagging": "false"}
    )

    response = await mcp.routes[("POST", "/api/import/preflight")](request)
    payload = json.loads(response.body)

    assert payload["detected_format"] == "operit"
    assert payload["import_mode"] == "operit"
    assert payload["operit_tagging_enabled"] is False
    assert payload["llm_required"] is False
    assert payload["llm_ready"] is False
    assert payload["can_start"] is True


@pytest.mark.asyncio
async def test_import_preflight_rejects_unknown_mode(monkeypatch):
    monkeypatch.setattr(import_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(import_api.sh, "import_engine", FakeImportEngine())
    mcp = FakeMCP()
    import_api.register(mcp)
    request = BodyRequest("Human: hello")
    request.query_params["import_mode"] = "guess"

    response = await mcp.routes[("POST", "/api/import/preflight")](request)

    assert response.status_code == 400
    assert "mode" in json.loads(response.body)["error"].lower()


@pytest.mark.asyncio
async def test_preflight_is_off_loop_and_rejects_parallel_body_before_read(monkeypatch):
    monkeypatch.setattr(import_api.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(import_api.sh, "import_engine", FakeImportEngine())
    monkeypatch.setattr(import_api.sh, "config", {"human": "阿立"})
    entered = threading.Event()
    release = threading.Event()

    def blocking_preview(*_args, **_kwargs):
        entered.set()
        release.wait(timeout=2)
        return {"ok": True, "turns_count": 1, "chunks_count": 1}

    monkeypatch.setattr(import_api, "preview_import", blocking_preview)
    mcp = FakeMCP()
    import_api.register(mcp)
    preflight = mcp.routes[("POST", "/api/import/preflight")]

    class MustNotReadRequest(BodyRequest):
        async def body(self):
            raise AssertionError("parallel preflight must be rejected before body read")

    first = asyncio.create_task(preflight(BodyRequest("Human: one")))
    while not entered.is_set():
        await asyncio.sleep(0)

    second = await preflight(MustNotReadRequest("Human: two"))
    assert second.status_code == 409
    assert "active" in json.loads(second.body)["error"].lower()

    release.set()
    response = await asyncio.wait_for(first, timeout=2)
    assert response.status_code == 200
