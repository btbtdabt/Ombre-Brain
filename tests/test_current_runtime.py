from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from current_runtime import (
    ANCHOR_PROPOSAL_PROMPT_TEMPLATE,
    PROFILE_FACT_PROPOSAL_PROMPT_TEMPLATE,
    RuntimeCollaborators,
)
from tools.current import memory as current_memory
from web.current_contract import CURRENT_REQUIRED_SERVICES, CurrentWebDependencies
from web.current_services import CurrentServiceAdapters


class FakeBucketManager:
    def __init__(self, buckets: list[dict[str, Any]], media_dir: Path) -> None:
        self.buckets = {str(bucket["id"]): bucket for bucket in buckets}
        self.media_store = SimpleNamespace(media_dir=media_dir)

    async def get(self, bucket_id: str) -> dict[str, Any] | None:
        return self.buckets.get(bucket_id)

    async def list_all(self, *, include_archive: bool) -> list[dict[str, Any]]:
        _ = include_archive
        return list(self.buckets.values())

    async def search(self, _query: str, **_kwargs: Any) -> list[dict[str, Any]]:
        return list(self.buckets.values())


class FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.results = ["profile-result", "anchor-result"]

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        content = self.results[len(self.calls) - 1]
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeDehydrator:
    def __init__(self) -> None:
        self.completions = FakeCompletions()
        self.client = SimpleNamespace(
            chat=SimpleNamespace(completions=self.completions),
        )
        self.api_available = True
        self.model = "proposal-model"
        self.option_calls: list[tuple[int, float]] = []

    def _completion_options(self, *, max_tokens: int, temperature: float) -> dict[str, Any]:
        self.option_calls.append((max_tokens, temperature))
        return {"max_tokens": max_tokens, "temperature": temperature}


class FakeEmbeddingEngine:
    enabled = True

    def __init__(self, db_path: Path) -> None:
        self.db_path = str(db_path)

    async def search_similar(self, _query: str, *, top_k: int) -> list[tuple[str, float]]:
        _ = top_k
        return []


class FakeEmbeddingOutbox:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, str]] = []
        self.discarded: list[str] = []
        self.start_calls = 0

    def enqueue(self, bucket_id: str, content: str) -> bool:
        self.enqueued.append((bucket_id, content))
        return True

    def discard(self, bucket_id: str) -> bool:
        self.discarded.append(bucket_id)
        return True

    def ensure_started(self) -> bool:
        self.start_calls += 1
        return True


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeCollaborators:
    buckets_dir = tmp_path / "buckets"
    state_dir = tmp_path / "state"
    private_identity = tmp_path / "identity.json"
    private_identity.write_text(
        json.dumps(
            {
                "canonical": {
                    "private_relation.title_marker": {
                        "seed_aliases": ["专属称呼"],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = {
        "buckets_dir": str(buckets_dir),
        "state_dir": str(state_dir),
        "identity": {
            "ai_name": "Haven",
            "user_name": "Xiaoyu",
            "user_display_name": "小雨",
        },
        "embedding": {"enabled": True},
        "word_map": {"enabled": True},
        "memory_diffusion": {"enabled": True, "min_activation": 0.0},
        "identity_semantics": {
            "enabled": True,
            "private_config_path": str(private_identity),
            "evidence_tags": ["relationship"],
        },
        "reflection": {"enabled": False},
        "portrait": {"enabled": False},
        "dream": {"enabled": False},
        "persona": {"enabled": False},
    }
    bucket = {
        "id": "memory-1",
        "content": "小雨不喜欢模板安慰，也记得专属称呼和共同项目。",
        "metadata": {
            "id": "memory-1",
            "name": "关系偏好",
            "tags": ["relationship"],
            "domain": ["relationship"],
            "importance": 8,
            "created": "2026-06-01T00:00:00+00:00",
            "updated_at": "2026-07-17T00:00:00+00:00",
            "last_active": "2026-07-17T00:00:00+00:00",
        },
    }
    manager = FakeBucketManager([bucket], tmp_path / "media")
    return RuntimeCollaborators(
        config=config,
        bucket_mgr=manager,
        dehydrator=FakeDehydrator(),
        decay_engine=object(),
        embedding_engine=FakeEmbeddingEngine(tmp_path / "embeddings.db"),
        embedding_outbox=FakeEmbeddingOutbox(),
        import_engine=object(),
        logger=SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    )


def test_runtime_maps_share_singleton_collaborators_and_callable_services(
    runtime: RuntimeCollaborators,
) -> None:
    tool_kwargs = runtime.tool_runtime_kwargs()
    web_kwargs = runtime.web_runtime_kwargs()
    dependencies = runtime.web_dependencies()

    assert isinstance(dependencies, CurrentWebDependencies)
    for name in (
        "config",
        "bucket_mgr",
        "decay_engine",
        "embedding_engine",
        "embedding_outbox",
        "darkroom_store",
        "dream_engine",
        "memory_edge_store",
        "memory_moment_store",
        "memory_node_store",
        "entity_edge_store",
        "identity_semantic_store",
        "word_map_store",
        "raw_event_store",
        "reminder_store",
        "reflection_engine",
        "persona_engine",
        "portrait_engine",
        "gateway_state_store",
    ):
        assert tool_kwargs[name] is web_kwargs[name]
        assert getattr(dependencies, name) is tool_kwargs[name]

    assert web_kwargs["backup_manager"] is runtime.backup_manager
    assert web_kwargs["services"] is runtime.web_services
    assert dependencies.services is runtime.web_services
    assert runtime.service_dependencies.profile_fact_writer is current_memory.profile_fact
    assert runtime.service_dependencies.anchor_writer is current_memory.trace
    adapter = getattr(runtime.web_services.inspect_diffusion, "__self__", None)
    assert isinstance(adapter, CurrentServiceAdapters)
    assert adapter.dependencies is runtime.service_dependencies
    for name in CURRENT_REQUIRED_SERVICES:
        callback = getattr(runtime.web_services, name)
        assert callable(callback)
        assert getattr(callback, "__self__", None) is adapter
    assert getattr(runtime.web_services.refresh_restore_indexes, "__self__", None) is runtime


@pytest.mark.asyncio
async def test_web_proposal_services_use_assembled_model_adapters(
    runtime: RuntimeCollaborators,
) -> None:
    runtime.dehydrator.completions.results = [
        json.dumps(
            [
                {
                    "fact": "小雨不喜欢模板安慰",
                    "profile_kind": "boundary",
                    "subject": "user",
                    "predicate": "dislikes",
                    "object": "模板安慰",
                    "evidence_bucket_id": "memory-1",
                    "confidence": 0.95,
                    "reason": "证据正文直接陈述",
                }
            ],
            ensure_ascii=False,
        ),
        json.dumps(
            [
                {
                    "bucket_id": "memory-1",
                    "anchor_kind": "preference",
                    "reason": "长期边界会影响未来回应",
                    "future_use": "生成回应前检查表达方式",
                    "confidence": 0.9,
                }
            ],
            ensure_ascii=False,
        ),
    ]
    profile_service = runtime.web_services.profile_fact_proposals
    anchor_service = runtime.web_services.anchor_proposals
    assert callable(profile_service)
    assert callable(anchor_service)

    profile = await profile_service({"bucket_id": "memory-1", "max_proposals": 3})
    anchor = await anchor_service({"bucket_id": "memory-1"})

    assert profile["status"] == "ok"
    assert profile["proposals"][0]["fact"] == "小雨不喜欢模板安慰"
    assert anchor["status"] == "ok"
    assert anchor["proposals"][0]["bucket_id"] == "memory-1"


@pytest.mark.asyncio
async def test_profile_and_anchor_model_adapters_preserve_historical_calls(
    runtime: RuntimeCollaborators,
) -> None:
    bucket = await runtime.bucket_mgr.get("memory-1")
    assert bucket is not None

    profile_result = await runtime.profile_fact_proposal_model(
        bucket=bucket,
        evidence_moment_id="",
        max_proposals=7,
    )
    anchor_result = await runtime.anchor_proposal_model(bucket=bucket)

    assert profile_result == "profile-result"
    assert anchor_result == "anchor-result"
    dehydrator = runtime.dehydrator
    assert dehydrator.option_calls == [(900, 0.0), (500, 0.0)]
    profile_call, anchor_call = dehydrator.completions.calls
    assert profile_call["model"] == "proposal-model"
    assert profile_call["messages"][0] == {
        "role": "system",
        "content": PROFILE_FACT_PROPOSAL_PROMPT_TEMPLATE.format(
            user_display_name="小雨",
            ai_name="Haven",
        ),
    }
    assert json.loads(profile_call["messages"][1]["content"]) == {
        "bucket_id": "memory-1",
        "bucket_name": "关系偏好",
        "bucket_tags": ["relationship"],
        "bucket_domain": ["relationship"],
        "evidence_moment_id": "",
        "content": "小雨不喜欢模板安慰，也记得专属称呼和共同项目。",
        "max_proposals": 3,
    }
    assert profile_call["max_tokens"] == 900
    assert profile_call["temperature"] == 0.0

    assert anchor_call["model"] == "proposal-model"
    assert anchor_call["messages"][0] == {
        "role": "system",
        "content": ANCHOR_PROPOSAL_PROMPT_TEMPLATE.format(
            user_display_name="小雨",
            ai_name="Haven",
        ),
    }
    assert json.loads(anchor_call["messages"][1]["content"]) == {
        "bucket_id": "memory-1",
        "bucket_name": "关系偏好",
        "bucket_type": "",
        "bucket_tags": ["relationship"],
        "bucket_domain": ["relationship"],
        "importance": 8,
        "created": "2026-06-01T00:00:00+00:00",
        "updated_at": "2026-07-17T00:00:00+00:00",
        "last_active": "2026-07-17T00:00:00+00:00",
        "content": "小雨不喜欢模板安慰，也记得专属称呼和共同项目。",
    }
    assert anchor_call["max_tokens"] == 500
    assert anchor_call["temperature"] == 0.0


@pytest.mark.asyncio
async def test_queue_embedding_refresh_uses_shared_outbox(
    runtime: RuntimeCollaborators,
) -> None:
    assert await runtime.queue_embedding_refresh("memory-1") is True
    assert runtime.embedding_outbox.enqueued[0][0] == "memory-1"
    assert "模板安慰" in runtime.embedding_outbox.enqueued[0][1]
    assert runtime.embedding_outbox.start_calls == 1

    assert await runtime.queue_embedding_refresh("missing") is False
    assert runtime.embedding_outbox.discarded == ["missing"]


@pytest.mark.asyncio
async def test_inspection_services_execute_against_current_stores(
    runtime: RuntimeCollaborators,
) -> None:
    related = {
        "id": "memory-2",
        "content": "共同项目需要避免模板化回复。",
        "metadata": {
            "id": "memory-2",
            "name": "共同项目",
            "tags": ["project"],
            "domain": ["work"],
            "importance": 5,
        },
    }
    runtime.bucket_mgr.buckets["memory-2"] = related
    runtime.memory_edge_store.add_edge(
        "memory-1",
        "memory-2",
        "supports",
        confidence=0.9,
        reason="shared project context",
    )
    diffusion_service = runtime.web_services.inspect_diffusion
    recall_service = runtime.web_services.inspect_recall
    assert callable(diffusion_service)
    assert callable(recall_service)

    diffusion = await diffusion_service(
        query="模板安慰",
        max_seeds=1,
        max_hits=5,
        edge_min_confidence=0.5,
    )
    recall = await recall_service(
        query="模板安慰",
        max_candidates=10,
        max_results=3,
        max_tokens=800,
        direct_render_mode="compact",
        domain="",
        valence=None,
        arousal=None,
    )

    assert diffusion["status"] == "ok"
    assert diffusion["seeds"]
    assert diffusion["options"]["edge_min_confidence"] == 0.5
    assert "runtime_gate" in diffusion["seeds"][0]
    assert diffusion["hits"]
    assert diffusion["hits"][0]["path_ids"][0] == "memory-1"
    assert diffusion["hits"][0]["paths"][0]["steps"]
    assert recall["status"] == "ok"
    assert recall["search_query"]
    assert recall["recall_thresholds"]["direct_render_mode"] == "compact"
    assert recall["candidate_count"] > 0
    assert recall["candidates"]
    assert {
        "gate",
        "admission",
        "layer_debug",
        "runtime_gate",
    } <= set(recall["candidates"][0])


@pytest.mark.asyncio
async def test_restore_callback_rebuilds_deterministic_indexes_and_keeps_explicit_edges(
    runtime: RuntimeCollaborators,
) -> None:
    old_bucket = {
        "id": "memory-1",
        "content": "小雨喜欢旧模板。",
        "metadata": {
            "id": "memory-1",
            "name": "旧关系偏好",
            "tags": ["relationship"],
            "domain": ["relationship"],
            "importance": 3,
        },
    }
    runtime.refresh_bucket_indexes(old_bucket)
    runtime.memory_edge_store.add_edge(
        "memory-1",
        "memory-2",
        "supports",
        confidence=0.9,
        reason="explicit user-authored relationship",
    )

    restore_service = runtime.web_services.refresh_restore_indexes
    assert callable(restore_service)
    result = await restore_service(["memory-1", "memory-1"])

    assert result == {"refreshed": 1, "errors": []}
    moments = runtime.memory_moment_store.list_for_bucket("memory-1")
    assert moments
    assert any("模板安慰" in str(moment["text"]) for moment in moments)
    assert all("旧模板" not in str(moment["text"]) for moment in moments)
    assert runtime.memory_node_store.get("memory-1") is not None

    entity_edges = runtime.entity_edge_store.list_edges()
    assert any(edge["relation"] == "dislikes" for edge in entity_edges)
    assert all("旧模板" not in edge["object_text"] for edge in entity_edges)
    assert runtime.identity_semantic_store.stats() == {
        "canonical": 1,
        "aliases": 1,
        "evidence": 1,
    }
    assert runtime.word_map_store.stats()["card_nodes"] > 0
    explicit_edges = runtime.memory_edge_store.list_edges()
    assert len(explicit_edges) == 1
    assert explicit_edges[0]["source"] == "memory-1"
    assert explicit_edges[0]["target"] == "memory-2"
    assert explicit_edges[0]["relation_type"] == "supports"

    missing = await restore_service(["missing"])
    assert missing == {"refreshed": 0, "errors": ["missing"]}
