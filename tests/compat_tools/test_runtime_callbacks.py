from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools import _runtime as runtime
from tools.current import _helpers


def test_shared_current_text_helpers_preserve_compaction_and_logging(monkeypatch):
    warnings = []
    monkeypatch.setattr(
        runtime,
        "logger",
        SimpleNamespace(warning=lambda message, *args: warnings.append((message, args))),
    )

    assert _helpers.clip_text("  one\n two three  ", 9) == "one two…"
    _helpers.log_warning("failed %s", "once")

    assert warnings == [("failed %s", ("once",))]


@pytest.mark.asyncio
async def test_embedding_refresh_prefers_the_shared_runtime_callback(monkeypatch):
    calls = []

    async def refresh(bucket_id: str) -> bool:
        calls.append(bucket_id)
        return True

    monkeypatch.setattr(runtime, "queue_embedding_refresh", refresh)
    monkeypatch.setattr(runtime, "bucket_mgr", None)

    assert await _helpers.queue_embedding_refresh("memory-1") is True
    assert calls == ["memory-1"]


@pytest.mark.asyncio
async def test_embedding_refresh_fallback_starts_the_outbox(monkeypatch):
    starts = []
    outbox = SimpleNamespace(
        enqueue=lambda bucket_id, content, *, reset_retry: (
            bucket_id == "memory-1" and content == "remember" and reset_retry
        ),
        ensure_started=lambda: starts.append(True),
    )

    class Manager:
        embedding_outbox = outbox

        async def get(self, bucket_id):
            return {"id": bucket_id, "content": "remember", "metadata": {}}

    monkeypatch.setattr(runtime, "queue_embedding_refresh", None)
    monkeypatch.setattr(runtime, "bucket_mgr", Manager())
    monkeypatch.setattr(runtime, "embedding_outbox", outbox)

    assert await _helpers.queue_embedding_refresh("memory-1") is True
    assert starts == [True]


def test_index_refresh_prefers_the_shared_runtime_callback(monkeypatch):
    calls = []
    bucket = {"id": "memory-1", "content": "remember", "metadata": {}}
    monkeypatch.setattr(runtime, "refresh_bucket_indexes", calls.append)

    _helpers.refresh_bucket_indexes(bucket)

    assert calls == [bucket]


def test_index_refresh_fallback_updates_moments_nodes_and_entities(monkeypatch):
    calls = []
    bucket = {"id": "memory-1", "content": "Amy likes tea", "metadata": {}}
    monkeypatch.setattr(runtime, "refresh_bucket_indexes", None)
    monkeypatch.setattr(
        runtime,
        "memory_moment_store",
        SimpleNamespace(upsert_bucket=lambda value: calls.append(("moment", value["id"]))),
    )
    monkeypatch.setattr(
        runtime,
        "memory_node_store",
        SimpleNamespace(upsert_bucket=lambda value: calls.append(("node", value["id"]))),
    )
    monkeypatch.setattr(
        runtime,
        "entity_edge_store",
        SimpleNamespace(
            replace_bucket_edges=lambda bucket_id, _edges: calls.append(
                ("entity", bucket_id)
            )
        ),
    )
    monkeypatch.setattr(
        runtime,
        "config",
        {"identity": {"user_name": "Amy", "ai_name": "Haven"}},
    )

    _helpers.refresh_bucket_indexes(bucket)

    assert calls == [
        ("moment", "memory-1"),
        ("node", "memory-1"),
        ("entity", "memory-1"),
    ]
