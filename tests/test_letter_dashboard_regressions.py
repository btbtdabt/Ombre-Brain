import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from web import letters


ROOT = Path(__file__).resolve().parents[1]


class FakeMCP:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(fn):
            for method in methods:
                self.routes[(method, path)] = fn
            return fn

        return decorator


class DeleteRequest:
    path_params = {"letter_id": "letter-ghost"}
    query_params = {"confirm": "true"}


class CreateRequest:
    async def json(self):
        return {
            "author": "ai",
            "content": "同一条服务路径写入的信",
            "user_name": "Amy",
            "title": "一封信",
            "date": "2026-08-05",
            "ai_name": "秋",
        }


class LetterServiceSpy:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return "letter-1", "秋"

    @staticmethod
    def normalize_author(author, ai_name=""):
        raw = str(author or "").strip()
        return "秋" if raw.lower() in {"ai", "claude"} or raw == "秋" else raw


class EditRequest:
    path_params = {"letter_id": "letter-1"}

    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


class EditBucketManager:
    def __init__(self):
        self.updates = []

    async def get(self, _bucket_id):
        return {
            "id": "letter-1",
            "content": "原文",
            "metadata": {"type": "letter", "title": "原标题"},
        }

    async def update(self, bucket_id, **kwargs):
        self.updates.append((bucket_id, kwargs))
        return True


class ListRequest:
    query_params = {"author": "ai"}


class ListBucketManager:
    async def list_all(self, include_archive=False):
        assert include_archive is False
        return [
            {
                "id": "configured-ai",
                "content": "配置身份写的信",
                "metadata": {
                    "type": "letter",
                    "author": "秋",
                    "created": "2026-08-05T00:00:00Z",
                },
            },
            {
                "id": "other",
                "content": "其他署名",
                "metadata": {
                    "type": "letter",
                    "author": "WrongEnvName",
                    "created": "2026-08-04T00:00:00Z",
                },
            },
        ]


class MissingBucketManager:
    def __init__(self):
        self.embedding_outbox = SimpleNamespace(discard=lambda bucket_id: None)
        self.invalidated = False

    async def get(self, bucket_id):
        return None

    def _invalidate_bm25(self):
        self.invalidated = True


def payload(response):
    return json.loads(response.body.decode("utf-8"))


def test_dashboard_lucide_observer_cannot_eat_button_clicks():
    dashboard = (ROOT / "frontend" / "dashboard.html").read_text(encoding="utf-8")

    assert "button i, button svg { pointer-events: none; }" in dashboard
    assert "obs.disconnect();" in dashboard
    assert "finally {" in dashboard
    assert "obs.observe(document.body, {childList: true, subtree: true});" in dashboard
    assert 'data-lucide="moon-off"' not in dashboard


@pytest.mark.asyncio
async def test_dashboard_letter_create_uses_canonical_letter_service(monkeypatch):
    service = LetterServiceSpy()
    monkeypatch.setattr(letters.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(letters.sh, "letter_service", service, raising=False)
    monkeypatch.setattr(
        letters.sh,
        "bucket_mgr",
        SimpleNamespace(create=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("dashboard must not bypass LetterService")
        )),
    )
    mcp = FakeMCP()
    letters.register(mcp)

    response = await mcp.routes[("POST", "/api/letter")](CreateRequest())

    assert response.status_code == 200
    assert payload(response) == {"ok": True, "id": "letter-1"}
    assert service.calls == [{
        "author": "ai",
        "content": "同一条服务路径写入的信",
        "user_name": "Amy",
        "title": "一封信",
        "date": "2026-08-05",
        "ai_name": "秋",
        "event_actor": "human",
    }]


@pytest.mark.asyncio
async def test_dashboard_letter_edit_validates_title_and_records_human_actor(
    monkeypatch,
):
    manager = EditBucketManager()
    monkeypatch.setattr(letters.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(letters.sh, "bucket_mgr", manager)
    monkeypatch.setattr(letters.sh, "letter_service", LetterServiceSpy(), raising=False)
    monkeypatch.setenv("AI_NAME", "WrongEnvName")
    mcp = FakeMCP()
    letters.register(mcp)
    handler = mcp.routes[("PATCH", "/api/letter/{letter_id}")]

    response = await handler(EditRequest({"title": "新\n标题"}))
    author = await handler(EditRequest({"author": "ai"}))
    empty = await handler(EditRequest({"title": "  "}))
    overlong = await handler(EditRequest({"title": "长" * 121}))

    assert response.status_code == 200
    assert manager.updates == [
        ("letter-1", {"title": "新 标题", "event_actor": "human"}),
        ("letter-1", {"author": "秋", "event_actor": "human"}),
    ]
    assert author.status_code == 200
    assert empty.status_code == 400
    assert overlong.status_code == 400


@pytest.mark.asyncio
async def test_dashboard_ai_filter_uses_configured_letter_identity(monkeypatch):
    monkeypatch.setattr(letters.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(letters.sh, "bucket_mgr", ListBucketManager())
    monkeypatch.setattr(letters.sh, "letter_service", LetterServiceSpy(), raising=False)
    monkeypatch.setenv("AI_NAME", "WrongEnvName")
    mcp = FakeMCP()
    letters.register(mcp)

    response = await mcp.routes[("GET", "/api/letters")](ListRequest())

    assert response.status_code == 200
    assert [item["id"] for item in payload(response)["letters"]] == ["configured-ai"]


@pytest.mark.asyncio
async def test_delete_missing_letter_repairs_vector_and_runtime_cache(monkeypatch):
    manager = MissingBucketManager()
    deleted_vectors = []
    monkeypatch.setattr(letters.sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(letters.sh, "bucket_mgr", manager)
    monkeypatch.setattr(
        letters.sh,
        "embedding_engine",
        SimpleNamespace(delete_embedding=deleted_vectors.append),
    )
    mcp = FakeMCP()
    letters.register(mcp)

    response = await mcp.routes[("DELETE", "/api/letter/{letter_id}")](DeleteRequest())
    body = payload(response)

    assert response.status_code == 200
    assert body == {
        "ok": True,
        "deleted": False,
        "cleaned": True,
        "already_missing": True,
    }
    assert deleted_vectors == ["letter-ghost"]
    assert manager.invalidated is True
