from __future__ import annotations

from types import SimpleNamespace

import pytest

from web.current_compat import CurrentWebDependencies, register_current_routes

from .conftest import RecordingMCP, request_for, response_json


def routes_for(**kwargs):
    mcp = RecordingMCP()
    register_current_routes(
        mcp,
        CurrentWebDependencies(
            config={"identity": {"user_name": "Amy"}},
            auth_guard=lambda _request: None,
            **kwargs,
        ),
    )
    return mcp.routes


@pytest.mark.asyncio
async def test_reminder_create_maps_dashboard_payload_to_store():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return {"id": "rem-1", "title": kwargs["title"], "secret": "hidden"}

    routes = routes_for(reminder_store=SimpleNamespace(create=create))

    response = await routes[("POST", "/api/reminders")](
        request_for(
            "POST",
            "/api/reminders",
            json_body={"title": "Check in", "text": "Ask tomorrow", "daily_limit": 2},
        )
    )

    payload = response_json(response)
    assert response.status_code == 200
    assert calls[0]["content"] == "Ask tomorrow"
    assert calls[0]["source"] == "dashboard"
    assert calls[0]["daily_limit"] == 2
    assert payload["status"] == "created"
    assert payload["reminder"]["id"] == "rem-1"
    assert payload["reminder"]["title"] == "Check in"
    assert payload["reminder"]["content"] is None
    assert "secret" not in payload["reminder"]


@pytest.mark.asyncio
async def test_word_map_cards_requires_term_before_store_access():
    store = SimpleNamespace(cards_for_term=lambda *_args: pytest.fail("called"))
    routes = routes_for(word_map_store=store)

    response = await routes[("GET", "/api/word-map/cards")](
        request_for("GET", "/api/word-map/cards")
    )

    assert response.status_code == 400
    assert response_json(response) == {"error": "missing term parameter"}


@pytest.mark.asyncio
async def test_word_map_get_refreshes_private_identity_terms():
    store = SimpleNamespace(
        enabled=True,
        private_terms=set(),
        stats=lambda: {"terms": 0},
        list_nodes=lambda _limit: [],
        list_edges=lambda _limit: [],
    )
    identity_store = SimpleNamespace(
        load_private_nodes=lambda: [
            SimpleNamespace(seed_aliases=("private-alias",))
        ]
    )
    routes = routes_for(
        word_map_store=store,
        identity_semantic_store=identity_store,
    )

    response = await routes[("GET", "/api/word-map")](
        request_for("GET", "/api/word-map")
    )

    assert response.status_code == 200
    assert "private-alias" in response_json(response)["private_terms_excluded"]
    assert "private-alias" in store.private_terms


@pytest.mark.asyncio
async def test_word_map_rebuild_refreshes_private_terms_before_store_rebuild():
    class WordMapStore:
        enabled = True

        def __init__(self):
            self.private_terms = set()
            self.private_terms_at_rebuild = set()

        def rebuild(self, _buckets):
            self.private_terms_at_rebuild = set(self.private_terms)
            return {"terms": 0}

        def stats(self):
            return {"terms": 0}

        def list_nodes(self, _limit):
            return []

        def list_edges(self, _limit):
            return []

    class Manager:
        async def list_all(self, *, include_archive):
            assert include_archive is False
            return []

    store = WordMapStore()
    routes = routes_for(
        word_map_store=store,
        bucket_mgr=Manager(),
        identity_semantic_store=SimpleNamespace(
            load_private_nodes=lambda: [
                SimpleNamespace(seed_aliases=("private-alias",))
            ]
        ),
    )

    response = await routes[("POST", "/api/word-map/rebuild")](
        request_for("POST", "/api/word-map/rebuild")
    )

    assert response.status_code == 200
    assert response_json(response)["status"] == "rebuilt"
    assert "private-alias" in store.private_terms_at_rebuild


@pytest.mark.asyncio
async def test_darkroom_status_returns_store_public_payload():
    store = SimpleNamespace(status=lambda: {"status": "locked", "has_room": True})
    routes = routes_for(darkroom_store=store)

    response = await routes[("GET", "/api/darkroom/status")](
        request_for("GET", "/api/darkroom/status")
    )

    assert response.status_code == 200
    assert response_json(response) == {"status": "locked", "has_room": True}


@pytest.mark.asyncio
async def test_bulk_delete_requires_exact_confirmation():
    manager = SimpleNamespace(get=lambda _bucket_id: pytest.fail("called"))
    routes = routes_for(bucket_mgr=manager)

    response = await routes[("POST", "/api/buckets/delete")](
        request_for(
            "POST",
            "/api/buckets/delete",
            json_body={"bucket_ids": ["memory-1"], "confirm": "yes"},
        )
    )

    assert response.status_code == 400
    assert response_json(response) == {"error": "confirmation required"}


@pytest.mark.asyncio
async def test_bulk_update_reports_store_refusal_as_failed():
    class Manager:
        async def get(self, _bucket_id):
            return {
                "id": "memory-1",
                "metadata": {"type": "dynamic"},
            }

        async def archive(self, _bucket_id):
            return False

    routes = routes_for(bucket_mgr=Manager())

    response = await routes[("POST", "/api/buckets/bulk-update")](
        request_for(
            "POST",
            "/api/buckets/bulk-update",
            json_body={"bucket_ids": ["memory-1"], "status": "archived"},
        )
    )

    assert response.status_code == 200
    assert response_json(response) == {
        "matched": 1,
        "changed": 0,
        "unchanged": 0,
        "not_found": 0,
        "invalid": 0,
        "failed": 1,
        "changed_ids": [],
        "changed_count": 0,
        "results": [
            {
                "id": "memory-1",
                "status": "failed",
                "reason": "archive_failed",
            }
        ],
    }


@pytest.mark.asyncio
async def test_memory_write_accepts_configured_bearer_and_uses_bucket_manager(monkeypatch):
    monkeypatch.delenv("OMBRE_MEMORY_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("OMBRE_GATEWAY_TOKEN", raising=False)
    calls = []

    class Manager:
        async def get(self, bucket_id):
            return None if bucket_id is None else {
                "id": bucket_id,
                "metadata": {"name": "A memory"},
                "content": "Remember this",
            }

        async def create(self, **kwargs):
            calls.append(kwargs)
            return "memory-1"

    mcp = RecordingMCP()
    register_current_routes(
        mcp,
        CurrentWebDependencies(
            config={"gateway": {"token": "write-secret"}},
            auth_guard=lambda _request: None,
            bucket_mgr=Manager(),
            embedding_engine=SimpleNamespace(enabled=False),
        ),
    )

    response = await mcp.routes[("POST", "/api/memories")](
        request_for(
            "POST",
            "/api/memories",
            headers={"Authorization": "Bearer write-secret"},
            json_body={"title": "A memory", "content": "Remember this"},
        )
    )

    assert response.status_code == 200
    assert response_json(response) == {
        "status": "created",
        "id": "memory-1",
        "source": "chatgpt",
        "embedding": "disabled",
    }
    assert calls[0]["source"] == "chatgpt"
    assert calls[0]["bucket_type"] == "dynamic"


@pytest.mark.asyncio
async def test_memory_update_preserves_metadata_omitted_by_partial_request(monkeypatch):
    monkeypatch.setenv("OMBRE_MEMORY_WRITE_TOKEN", "write-secret")
    calls = []
    stored = {
        "id": "memory-1",
        "content": "Original",
        "metadata": {
            "name": "Original title",
            "tags": ["stable"],
            "domain": ["identity"],
            "importance": 9,
            "valence": 0.8,
            "arousal": 0.2,
            "anchor": True,
            "resolved": True,
            "digested": True,
            "confidence": 0.95,
            "source": "mcp",
        },
    }

    class Manager:
        async def get(self, _bucket_id):
            return stored

        async def update(self, _bucket_id, **kwargs):
            calls.append(kwargs)
            return True

    routes = routes_for(
        bucket_mgr=Manager(),
        embedding_engine=SimpleNamespace(enabled=False),
    )
    response = await routes[("POST", "/api/memories")](
        request_for(
            "POST",
            "/api/memories",
            headers={"Authorization": "Bearer write-secret"},
            json_body={
                "id": "memory-1",
                "title": "Edited title",
                "content": "Edited content",
            },
        )
    )

    assert response.status_code == 200
    assert set(calls[0]) == {"content", "name", "updated_at"}
    assert calls[0]["content"] == "Edited content"
    assert calls[0]["name"] == "Edited title"


@pytest.mark.asyncio
async def test_memory_write_fails_closed_when_token_is_not_configured(monkeypatch):
    monkeypatch.delenv("OMBRE_MEMORY_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("OMBRE_GATEWAY_TOKEN", raising=False)
    routes = routes_for(bucket_mgr=SimpleNamespace())

    response = await routes[("POST", "/api/memories")](
        request_for(
            "POST",
            "/api/memories",
            json_body={"title": "A memory", "content": "Remember this"},
        )
    )

    assert response.status_code == 503
    assert response_json(response) == {
        "error": "memory write token is not configured"
    }


@pytest.mark.asyncio
async def test_profile_facts_keep_metadata_evidence_when_edge_store_fails():
    profile = {
        "id": "profile-1",
        "content": "### fact\nKeeps promises",
        "metadata": {
            "tags": ["profile_fact"],
            "evidence": [{"bucket_id": "evidence-1"}],
        },
    }
    evidence = {
        "id": "evidence-1",
        "content": "Source memory",
        "metadata": {"name": "Source"},
    }

    class Manager:
        async def list_all(self, *, include_archive):
            assert include_archive is True
            return [profile]

        async def get(self, bucket_id):
            return evidence if bucket_id == "evidence-1" else None

    edge_store = SimpleNamespace(
        list_edges=lambda: (_ for _ in ()).throw(RuntimeError("edge store offline"))
    )
    routes = routes_for(bucket_mgr=Manager(), memory_edge_store=edge_store)

    response = await routes[("GET", "/api/profile-facts")](
        request_for("GET", "/api/profile-facts")
    )

    assert response.status_code == 200
    payload = response_json(response)
    assert payload["count"] == 1
    assert payload["facts"][0]["evidence"] == [
        {
            "bucket_id": "evidence-1",
            "moment_id": "",
            "name": "Source",
            "exists": True,
        }
    ]


@pytest.mark.asyncio
async def test_profile_facts_prefer_moment_evidence_over_duplicate_bucket_edge():
    profile = {
        "id": "profile-1",
        "content": "### fact\nKeeps promises",
        "metadata": {
            "tags": ["profile_fact"],
            "evidence": [
                {"bucket_id": "evidence-1", "moment_id": "moment-1"}
            ],
        },
    }
    evidence = {
        "id": "evidence-1",
        "content": "Source memory",
        "metadata": {"name": "Source"},
    }

    class Manager:
        async def list_all(self, *, include_archive):
            assert include_archive is True
            return [profile]

        async def get(self, bucket_id):
            return evidence if bucket_id == "evidence-1" else None

    edge_store = SimpleNamespace(
        list_edges=lambda: [
            {
                "source": "profile-1",
                "target": "evidence-1",
                "relation_type": "evidenced_by",
            }
        ]
    )
    routes = routes_for(bucket_mgr=Manager(), memory_edge_store=edge_store)

    response = await routes[("GET", "/api/profile-facts")](
        request_for("GET", "/api/profile-facts")
    )

    assert response_json(response)["facts"][0]["evidence"] == [
        {
            "bucket_id": "evidence-1",
            "moment_id": "moment-1",
            "name": "Source",
            "exists": True,
        }
    ]


@pytest.mark.asyncio
async def test_profile_facts_include_legacy_metadata_evidence_fields():
    profile = {
        "id": "profile-1",
        "content": "### fact\nKeeps promises",
        "metadata": {
            "tags": ["profile_fact"],
            "evidence_bucket_id": "evidence-1",
            "evidence_moment_id": "moment-1",
            "source_bucket_id": "source-1",
            "source_moment_id": "moment-2",
        },
    }

    class Manager:
        async def list_all(self, *, include_archive):
            assert include_archive is True
            return [profile]

        async def get(self, bucket_id):
            if bucket_id in {"evidence-1", "source-1"}:
                return {
                    "id": bucket_id,
                    "content": "Source memory",
                    "metadata": {"name": bucket_id},
                }
            return None

    routes = routes_for(bucket_mgr=Manager())

    response = await routes[("GET", "/api/profile-facts")](
        request_for("GET", "/api/profile-facts")
    )

    assert response_json(response)["facts"][0]["evidence"] == [
        {
            "bucket_id": "evidence-1",
            "moment_id": "moment-1",
            "name": "evidence-1",
            "exists": True,
        },
        {
            "bucket_id": "source-1",
            "moment_id": "moment-2",
            "name": "source-1",
            "exists": True,
        },
    ]


@pytest.mark.asyncio
async def test_reflection_run_passes_injected_engines_and_stores():
    calls = []

    class Reflection:
        async def reflect(self, **kwargs):
            calls.append(kwargs)
            return {"status": "written", "period": kwargs["period"]}

    bucket_mgr = object()
    persona = object()
    embedding = object()
    gateway = object()
    routes = routes_for(
        reflection_engine=Reflection(),
        bucket_mgr=bucket_mgr,
        persona_engine=persona,
        embedding_engine=embedding,
        gateway_state_store=gateway,
    )

    response = await routes[("POST", "/api/reflection/run")](
        request_for(
            "POST",
            "/api/reflection/run",
            json_body={"period": "weekly", "force": True},
        )
    )

    assert response.status_code == 200
    assert response_json(response) == {"status": "written", "period": "weekly"}
    assert calls == [
        {
            "period": "weekly",
            "bucket_mgr": bucket_mgr,
            "persona_engine": persona,
            "embedding_engine": embedding,
            "force": True,
            "conversation_turn_store": gateway,
        }
    ]
