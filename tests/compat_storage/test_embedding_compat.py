from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from embedding_engine import EmbeddingEngine
from ombrebrain.storage.embedding_outbox import EmbeddingOutbox
from utils import bucket_text_for_embedding


def _enabled_config(tmp_path):
    return {
        "buckets_dir": str(tmp_path / "buckets"),
        "embedding": {
            "enabled": True,
            "api_key": "test-key",
            "api_format": "openai_compat",
            "base_url": "https://embedding.example/v1",
            "model": "current-model",
            "dim": 3,
        },
    }


def test_embedding_rows_record_model_dimension_and_batch_reads_filter_stale(tmp_path):
    engine = EmbeddingEngine(_enabled_config(tmp_path))
    engine._store_embedding("current", [0.1, 0.2, 0.3], "current-hash")

    with sqlite3.connect(engine.db_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(embeddings)")
        }
        connection.execute(
            """INSERT INTO embeddings
               (bucket_id, embedding, model, dimension, updated_at, content_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "stale",
                json.dumps([0.4, 0.5, 0.6]),
                "old-model",
                3,
                "2026-07-16T12:00:00+00:00",
                "stale-hash",
            ),
        )

    assert {"model", "dimension"} <= columns
    assert asyncio.run(engine.get_embedding("current")) == [0.1, 0.2, 0.3]
    assert asyncio.run(engine.get_embedding("stale")) is None
    assert asyncio.run(
        engine.get_embeddings(["current", "stale", "current", ""])
    ) == {"current": [0.1, 0.2, 0.3]}
    assert engine.list_content_ids() == ["current"]
    assert engine.list_content_hashes() == {"current": "current-hash"}


def test_embedding_reads_legacy_rows_without_per_row_model_metadata(tmp_path):
    engine = EmbeddingEngine(_enabled_config(tmp_path))

    with sqlite3.connect(engine.db_path) as connection:
        connection.execute(
            """INSERT INTO embeddings
               (bucket_id, embedding, updated_at, content_hash)
               VALUES (?, ?, ?, ?)""",
            (
                "legacy-import",
                json.dumps([0.1, 0.2, 0.3]),
                "2026-07-16T12:00:00+00:00",
                "legacy-hash",
            ),
        )

    assert asyncio.run(engine.get_embedding("legacy-import")) == [0.1, 0.2, 0.3]
    assert engine.list_content_ids() == ["legacy-import"]
    assert engine.list_content_hashes() == {"legacy-import": "legacy-hash"}


def test_embedding_outbox_uses_state_dir_and_survives_restart(tmp_path):
    config = {
        "buckets_dir": str(tmp_path / "buckets"),
        "state_dir": str(tmp_path / "state"),
        "embedding": {"enabled": False, "background_indexing": True},
    }

    class Manager:
        pass

    class Engine:
        enabled = False

    first = EmbeddingOutbox(config, Manager(), Engine())
    assert first.enqueue("memory-1", "durable content") is True

    assert Path(first.path).parent == tmp_path / "state"
    restarted = EmbeddingOutbox(config, Manager(), Engine())
    assert restarted.pending_ids() == {"memory-1"}
    assert restarted.status()["pending"] == 1


def test_embedding_outbox_imports_existing_p0_queue_into_state_dir(tmp_path):
    buckets_dir = tmp_path / "buckets"
    state_dir = tmp_path / "state"

    class Manager:
        pass

    class Engine:
        enabled = False

    p0_config = {
        "buckets_dir": str(buckets_dir),
        "embedding": {"enabled": False, "background_indexing": True},
    }
    legacy = EmbeddingOutbox(p0_config, Manager(), Engine())
    assert legacy.enqueue("memory-1", "pending P0 content") is True

    migrated_config = {
        "buckets_dir": str(buckets_dir),
        "state_dir": str(state_dir),
        "embedding": {"enabled": False, "background_indexing": True},
    }
    migrated = EmbeddingOutbox(migrated_config, Manager(), Engine())

    assert migrated.pending_ids() == {"memory-1"}
    assert Path(migrated.path).parent == state_dir
    assert Path(migrated.path).is_file()
    assert EmbeddingOutbox(
        migrated_config,
        Manager(),
        Engine(),
    ).pending_ids() == {"memory-1"}


def test_embedding_outbox_ensure_started_is_idempotent(tmp_path):
    config = {
        "buckets_dir": str(tmp_path / "buckets"),
        "state_dir": str(tmp_path / "state"),
        "embedding": {"enabled": False, "background_indexing": True},
    }

    class Manager:
        async def list_all(self, include_archive=True):
            return []

    class Engine:
        enabled = False

        @staticmethod
        def list_content_ids():
            return []

        @staticmethod
        def list_content_hashes():
            return {}

    async def scenario():
        outbox = EmbeddingOutbox(config, Manager(), Engine())
        assert outbox.ensure_started() is True
        for _attempt in range(50):
            if outbox.running:
                break
            await asyncio.sleep(0.01)
        assert outbox.running is True
        assert outbox.ensure_started() is False
        await outbox.stop()
        assert outbox.running is False

    asyncio.run(scenario())


def test_embedding_outbox_processes_legacy_title_and_body_queue_items(tmp_path):
    bucket = {
        "id": "memory-1",
        "content": "the body",
        "metadata": {"name": "Historical title"},
    }

    class Manager:
        @staticmethod
        async def get(bucket_id):
            return bucket if bucket_id == bucket["id"] else None

    class Engine:
        enabled = True

        def __init__(self):
            self.calls = []

        async def generate_and_store(self, bucket_id, content):
            self.calls.append((bucket_id, content))
            return False

    config = {
        "buckets_dir": str(tmp_path / "buckets"),
        "state_dir": str(tmp_path / "state"),
        "embedding": {
            "enabled": True,
            "background_indexing": True,
            "retry_base_seconds": 0.01,
            "retry_max_seconds": 0.02,
        },
    }
    engine = Engine()
    outbox = EmbeddingOutbox(config, Manager(), engine)
    queued_text = bucket_text_for_embedding(bucket)

    assert outbox.enqueue(bucket["id"], queued_text) is True
    assert asyncio.run(outbox.process_once()) is True
    assert engine.calls == [(bucket["id"], queued_text)]
    assert outbox.status()["retrying"] == 1


def test_generate_embedding_keeps_kind_scoped_lru_and_returns_copies(tmp_path):
    config = _enabled_config(tmp_path)
    config["embedding"]["query_cache_size"] = 2
    engine = EmbeddingEngine(config)

    class FakeEmbeddings:
        def __init__(self):
            self.calls = []

        async def create(self, *, model, input):
            self.calls.append((model, input))
            value = float(len(self.calls))
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[value, value + 0.5])]
            )

    fake = FakeEmbeddings()
    engine.client = SimpleNamespace(embeddings=fake)

    first = asyncio.run(engine._generate_embedding("seafood", kind="query"))
    first.append(999.0)
    assert asyncio.run(
        engine._generate_embedding("seafood", kind="query")
    ) == [1.0, 1.5]
    assert len(fake.calls) == 1

    asyncio.run(engine._generate_embedding("seafood", kind="document"))
    asyncio.run(engine._generate_embedding("second", kind="query"))
    asyncio.run(engine._generate_embedding("seafood", kind="query"))
    assert len(fake.calls) == 4


def test_generate_embedding_zero_cache_bypasses_p0_backend_cache(tmp_path):
    config = _enabled_config(tmp_path)
    config["embedding"]["query_cache_size"] = 0
    engine = EmbeddingEngine(config)

    class Backend:
        def __init__(self):
            self.calls = []

        async def generate_async(self, text):
            self.calls.append(text)
            return [1.0, 2.0, 3.0]

        @staticmethod
        def model_name():
            return "current-model"

        @staticmethod
        def vector_dim():
            return 3

    backend = Backend()
    engine.__dict__["_backend"] = backend
    engine.client = None

    asyncio.run(engine._generate_embedding("uncached", kind="query"))
    asyncio.run(engine._generate_embedding("uncached", kind="query"))

    assert len(backend.calls) == 2
