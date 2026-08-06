from __future__ import annotations

import json

import pytest

from import_memory import (
    _OVERLAP_CONTEXT_NOTICE,
    ImportEngine,
    ImportState,
    _import_content_hash,
    _import_event_date,
    _parse_chatgpt_json,
    _parse_markdown,
    _source_hash,
    chunk_turns,
    parse_operit_memory_backup,
    preview_import,
)


class NoLlmDehydrator:
    api_available = False


class StaticLlmDehydrator:
    api_available = True

    def __init__(self, items):
        self.items = items

    async def _chat(self, *_args, **_kwargs):
        return json.dumps(self.items, ensure_ascii=False)


class RecordingBucketManager:
    def __init__(self):
        self.created = []
        self.by_id = {}

    async def get(self, bucket_id):
        return self.by_id.get(bucket_id)

    async def create(self, **kwargs):
        bucket_id = kwargs.get("bucket_id") or kwargs.get("bucket_id_override") or "generated"
        self.created.append(dict(kwargs))
        self.by_id[bucket_id] = {
            "id": bucket_id,
            "content": kwargs["content"],
            "metadata": {
                **dict(kwargs.get("extra_metadata") or {}),
                "source": kwargs.get("source"),
            },
        }
        return bucket_id

    async def list_all(self, include_archive=False):
        return list(self.by_id.values())


class FailOnceBucketManager(RecordingBucketManager):
    def __init__(self):
        super().__init__()
        self.create_attempts = 0

    async def create(self, **kwargs):
        self.create_attempts += 1
        if self.create_attempts == 1:
            raise OSError("transient write failure")
        return await super().create(**kwargs)


class MergeDehydrator(StaticLlmDehydrator):

    async def merge(self, old_content, new_content):
        return f"{old_content}\n{new_content}"


class MergeBucketManager(RecordingBucketManager):
    def __init__(self, bucket):
        super().__init__()
        self.by_id[bucket["id"]] = bucket

    async def search(self, *_args, **_kwargs):
        bucket = next(iter(self.by_id.values()))
        return [{**bucket, "score": 99.0}]

    def find_exact_content(self, content, domain_filter=None):
        del domain_filter
        return next(
            (
                bucket
                for bucket in self.by_id.values()
                if bucket["content"] == content
            ),
            None,
        )

    async def update(self, bucket_id, **kwargs):
        bucket = self.by_id[bucket_id]
        bucket["content"] = kwargs.get("content", bucket["content"])
        metadata = bucket["metadata"]
        for key, value in dict(kwargs.get("extra_metadata") or {}).items():
            if value is None:
                metadata.pop(key, None)
            else:
                metadata[key] = value
        return True


def test_markdown_parser_recognizes_decorated_role_labels():
    turns = _parse_markdown(
        "**Human:** first line\ncontinued\n\n### Gemini: reply\nmore reply"
    )

    assert turns == [
        {"role": "user", "content": "first line\ncontinued", "timestamp": ""},
        {"role": "assistant", "content": "reply\nmore reply", "timestamp": ""},
    ]


def test_markdown_parser_recognizes_configured_identity_labels():
    turns = _parse_markdown(
        "Amy: first line\nQiu: reply",
        user_labels={"Amy"},
        assistant_labels={"Qiu"},
    )

    assert turns == [
        {"role": "user", "content": "first line", "timestamp": ""},
        {"role": "assistant", "content": "reply", "timestamp": ""},
    ]


def test_markdown_parser_retains_legacy_ombre_identity_labels():
    turns = _parse_markdown("小雨: 还记得吗\nHaven: 我记得")

    assert turns == [
        {"role": "user", "content": "还记得吗", "timestamp": ""},
        {"role": "assistant", "content": "我记得", "timestamp": ""},
    ]


def test_chatgpt_parser_filters_non_conversation_roles():
    data = {
        "mapping": {
            "system": {
                "message": {
                    "author": {"role": "system"},
                    "content": {"parts": ["hidden system prompt"]},
                    "create_time": 1,
                }
            },
            "user": {
                "message": {
                    "author": {"role": "user"},
                    "content": {"parts": ["hello"]},
                    "create_time": 2,
                }
            },
            "assistant": {
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"parts": ["hi"]},
                    "create_time": 3,
                }
            },
        }
    }

    assert [turn["role"] for turn in _parse_chatgpt_json(data)] == ["user", "assistant"]


def test_chatgpt_parser_preserves_original_numeric_timestamp():
    raw_timestamp = 1_700_000_000.125
    data = {
        "mapping": {
            "user": {
                "message": {
                    "author": {"role": "user"},
                    "content": {"parts": ["hello"]},
                    "create_time": raw_timestamp,
                }
            }
        }
    }

    assert _parse_chatgpt_json(data)[0]["timestamp"] == str(raw_timestamp)


@pytest.mark.parametrize(
    "timestamp",
    (
        1_700_000_000,
        1_700_000_000_000,
        1_700_000_000_000_000,
        1_700_000_000_000_000_000,
    ),
)
def test_import_event_date_normalizes_epoch_units(timestamp):
    assert _import_event_date(timestamp) == "2023-11-15"


def test_import_event_date_converts_timezone_and_local_formats():
    assert _import_event_date("2026-07-17T17:00:00Z") == "2026-07-18"
    assert _import_event_date("2026年07月17日 12:30") == "2026-07-17"


def test_oversized_turn_is_split_with_overlap_and_custom_human_label():
    chunks = chunk_turns(
        [{"role": "user", "content": "A" * 1800, "timestamp": "2026-07-16"}],
        target_tokens=120,
        human_label="Amy",
    )

    assert len(chunks) > 1
    assert chunks[0]["content"].startswith("[Amy] ")
    assert _OVERLAP_CONTEXT_NOTICE not in chunks[1]["content"]
    assert _OVERLAP_CONTEXT_NOTICE in chunks[1]["llm_content"]
    assert "[Amy] " in chunks[1]["llm_content"]


def test_operit_parser_rejects_generic_memories_objects():
    assert parse_operit_memory_backup('{"memories":[{"content":"generic"}]}') is None


def test_operit_preview_reports_raw_entries_without_api_calls():
    preview = preview_import(
        '{"exportDate":1,"memories":[{"uuid":"a","content":"exact"}]}',
        "operit.json",
    )

    assert preview["ok"] is True
    assert preview["detected_format"] == "operit"
    assert preview["chunks_count"] == 1
    assert preview["estimated_api_calls"] == 0


@pytest.mark.asyncio
async def test_operit_backup_imports_exact_content_without_an_llm(tmp_path):
    manager = RecordingBucketManager()
    engine = ImportEngine(
        {"buckets_dir": str(tmp_path / "buckets"), "state_dir": str(tmp_path / "state")},
        manager,
        NoLlmDehydrator(),
    )
    raw_content = "  exact Operit body\nwith spacing  "
    backup = json.dumps(
        {
            "exportDate": 1784246400000,
            "links": [],
            "memories": [
                {
                    "uuid": "12345678-1234-5678-1234-567812345678",
                    "title": "Operit title",
                    "content": raw_content,
                    "contentType": "text/markdown",
                    "source": "manual",
                    "credibility": 0.9,
                    "importance": 0.8,
                    "folderPath": "/stable",
                    "createdAt": 1784246400000,
                    "updatedAt": 1784246460000,
                    "tagNames": ["stable", "stable"],
                }
            ],
        },
        ensure_ascii=False,
    )

    result = await engine.start(
        backup,
        filename="operit-memory.json",
        import_mode="operit",
        operit_tagging=False,
    )

    assert result["status"] == "completed"
    assert result["import_format"] == "operit"
    assert result["memories_created"] == 1
    assert result["memories_raw"] == 1
    assert result["api_calls"] == 0
    assert manager.created[0]["content"] == raw_content
    assert manager.created[0]["bucket_id"] == "operit_12345678123456781234567812345678"
    assert manager.created[0]["source"] == "operit"
    assert manager.created[0]["extra_metadata"]["operit_uuid"] == (
        "12345678-1234-5678-1234-567812345678"
    )


def test_import_state_loads_production_counters_for_old_state_files(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "import_state.json").write_text(
        '{"status":"paused","total_chunks":2,"processed":1}', encoding="utf-8"
    )
    state = ImportState(str(state_dir))

    assert state.load() is True
    assert state.data["memories_duplicate_skipped"] == 0
    assert state.data["memories_failed"] == 0
    assert state.data["embeddings_created"] == 0
    assert state.data["embeddings_failed"] == 0
    assert state.data["embeddings_total"] == 0
    assert state.data["embeddings_processed"] == 0
    assert state.data["import_format"] == ""
    assert state.data["operit_phase"] == ""
    assert state.data["tagging_pending"] == 0
    assert state.data["_operit_tagging_attempts"] == {}


def test_import_engine_honors_production_configuration_surface(tmp_path):
    engine = ImportEngine(
        {
            "buckets_dir": str(tmp_path / "buckets"),
            "state_dir": str(tmp_path / "state"),
            "import": {
                "chunk_target_tokens": 4200,
                "extract_max_input_chars": 1234,
                "max_items_per_chunk": 3,
                "max_tags": 4,
                "max_tag_chars": 9,
                "auto_merge_enabled": True,
                "merge_threshold": 93,
                "merge_min_content_similarity": 97,
                "merge_require_domain_overlap": False,
                "merge_require_source_match": False,
                "merge_block_disjoint_dates": False,
                "operit_tagging_enabled": False,
                "operit_tagging_concurrency": 4,
                "operit_tagging_max_attempts": 5,
                "operit_tagging_retry_base_seconds": 0.25,
            },
        },
        RecordingBucketManager(),
        NoLlmDehydrator(),
    )

    assert engine.chunk_target_tokens == 4200
    assert engine.extract_max_input_chars == 1234
    assert engine.max_items_per_chunk == 3
    assert engine.max_tags == 4
    assert engine.max_tag_chars == 9
    assert engine.auto_merge_enabled is True
    assert engine.import_merge_threshold == 93
    assert engine.merge_min_content_similarity == 97
    assert engine.merge_require_domain_overlap is False
    assert engine.merge_require_source_match is False
    assert engine.merge_block_disjoint_dates is False
    assert engine.operit_tagging_enabled is False
    assert engine.operit_tagging_concurrency == 4
    assert engine.operit_tagging_max_attempts == 5
    assert engine.operit_tagging_retry_base_seconds == 0.25
    assert engine.state.state_file == str(tmp_path / "state" / "import_state.json")


@pytest.mark.asyncio
async def test_duplicate_items_from_one_extraction_create_one_bucket(tmp_path):
    item = {
        "name": "same",
        "content": "Amy consistently prefers exact source-preserving imports.",
        "domain": ["life"],
        "tags": ["import"],
        "importance": 5,
    }
    manager = RecordingBucketManager()
    engine = ImportEngine(
        {"buckets_dir": str(tmp_path)},
        manager,
        StaticLlmDehydrator([item, dict(item)]),
    )

    await engine._process_single_chunk(
        {
            "content": "[Amy] source",
            "source_file": "history.md",
            "source_hash": "abc",
            "source_chunk_id": "abc:00001",
            "chunk_index": 1,
            "chunk_total": 1,
            "turn_count": 1,
        },
        preserve_raw=False,
    )

    assert len(manager.created) == 1
    assert manager.created[0]["extra_metadata"]["source_chunk_ids"] == ["abc:00001"]


@pytest.mark.asyncio
async def test_imported_bucket_uses_source_event_date(tmp_path):
    item = {
        "name": "dated memory",
        "content": "Amy and Qiu discussed a durable event on this date.",
        "domain": ["life"],
        "tags": ["date"],
        "importance": 5,
    }
    manager = RecordingBucketManager()
    engine = ImportEngine(
        {"buckets_dir": str(tmp_path)},
        manager,
        StaticLlmDehydrator([item]),
    )

    await engine._process_single_chunk(
        {
            "content": "[Amy] source",
            "timestamp_start": "2026-07-17T17:00:00Z",
            "timestamp_end": "2026-07-17T17:01:00Z",
            "source_file": "history.md",
            "source_hash": "dated",
            "source_chunk_id": "dated:00001",
            "chunk_index": 1,
            "chunk_total": 1,
            "turn_count": 2,
        },
        preserve_raw=False,
    )

    created = manager.created[0]
    assert created["date"] == "2026-07-18"
    assert created["extra_metadata"]["import_event_date"] == "2026-07-18"
    assert created["extra_metadata"]["source_refs"][0]["event_date"] == (
        "2026-07-18"
    )


@pytest.mark.asyncio
async def test_resume_restores_cross_chunk_dedupe_state(tmp_path, monkeypatch):
    import import_memory as import_memory_module

    item = {
        "name": "overlap",
        "content": "The overlap repeats this exact durable memory.",
        "domain": ["life"],
        "tags": ["overlap"],
        "importance": 5,
    }
    raw = "Human: first\nAssistant: second"
    chunks = [
        {"content": "[用户] first", "turn_count": 1},
        {"content": "[上下文提示] first\n[本段内容] second", "turn_count": 1},
    ]
    monkeypatch.setattr(
        import_memory_module,
        "chunk_turns",
        lambda *_args, **_kwargs: list(chunks),
    )
    manager = RecordingBucketManager()
    config = {
        "buckets_dir": str(tmp_path / "buckets"),
        "state_dir": str(tmp_path / "state"),
        "human": "用户",
    }
    engine = ImportEngine(config, manager, StaticLlmDehydrator([item]))
    source_hash = _source_hash("用户", raw)
    engine.state.reset("history.md", source_hash, 2)
    engine.state.data["processed"] = 1
    engine.state.data["status"] = "paused"
    engine.state.data["_seen_content_hashes"] = [_import_content_hash(item["content"])]
    engine.state.save()

    result = await engine.start(raw, filename="history.md", resume=True)

    assert result["status"] == "completed"
    assert manager.created == []


@pytest.mark.asyncio
async def test_failed_item_is_not_persisted_as_seen_and_can_retry_after_resume(tmp_path):
    item = {
        "name": "retry",
        "content": "A transient storage failure must not discard this memory.",
        "domain": ["life"],
        "tags": ["retry"],
        "importance": 5,
    }
    config = {
        "buckets_dir": str(tmp_path / "buckets"),
        "state_dir": str(tmp_path / "state"),
    }
    manager = FailOnceBucketManager()
    first_engine = ImportEngine(config, manager, StaticLlmDehydrator([item]))

    await first_engine._process_single_chunk(
        {
            "content": "[Amy] first attempt",
            "source_file": "history.md",
            "source_hash": "retry-source",
            "source_chunk_id": "retry-source:00001",
            "chunk_index": 1,
            "chunk_total": 2,
            "turn_count": 1,
        },
        preserve_raw=False,
    )
    first_engine.state.save()

    assert first_engine.state.data["_seen_content_hashes"] == []
    assert first_engine.state.data["memories_failed"] == 1

    resumed_engine = ImportEngine(config, manager, StaticLlmDehydrator([item]))
    assert resumed_engine.state.load() is True
    resumed_engine._seen_import_hashes = set(
        resumed_engine.state.data["_seen_content_hashes"]
    )
    await resumed_engine._process_single_chunk(
        {
            "content": "[Amy] resumed overlap",
            "source_file": "history.md",
            "source_hash": "retry-source",
            "source_chunk_id": "retry-source:00002",
            "chunk_index": 2,
            "chunk_total": 2,
            "turn_count": 1,
        },
        preserve_raw=False,
    )

    assert manager.create_attempts == 2
    assert len(manager.created) == 1
    assert resumed_engine.state.data["_seen_content_hashes"] == [
        _import_content_hash(item["content"])
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auto_merge_enabled", "preserve_raw", "counter"),
    (
        (False, False, "memories_duplicate_skipped"),
        (True, False, "memories_merged"),
        (False, True, "memories_duplicate_skipped"),
    ),
)
async def test_cross_source_normal_flow_keeps_complete_provenance(
    tmp_path,
    auto_merge_enabled,
    preserve_raw,
    counter,
):
    existing = {
        "id": "existing",
        "content": "Amy keeps exact import evidence for durable memories.",
        "metadata": {
            "domain": ["life"],
            "importance": 5,
            "valence": 0.5,
            "arousal": 0.5,
            "import_source_file": "first.md",
            "import_source_hash": "hash-a",
            "source_refs": [
                {
                    "chunk_id": "hash-a:00001",
                    "source_file": "first.md",
                    "source_hash": "hash-a",
                }
            ],
        },
    }
    item = {
        "name": "provenance",
        "content": "Amy keeps exact import evidence for durable memories.",
        "domain": ["life"],
        "tags": ["import"],
        "importance": 5,
        "valence": 0.5,
        "arousal": 0.5,
    }
    manager = MergeBucketManager(existing)
    engine = ImportEngine(
        {
            "buckets_dir": str(tmp_path),
            "import": {
                "auto_merge_enabled": auto_merge_enabled,
                "merge_threshold": 90,
                "merge_min_content_similarity": 0,
                "merge_require_source_match": False,
                "merge_block_disjoint_dates": False,
            },
        },
        manager,
        MergeDehydrator([item]),
    )

    await engine._process_single_chunk(
        {
            "content": "[Amy] repeated source evidence",
            "source_file": "second.md",
            "source_hash": "hash-b",
            "source_chunk_id": "hash-b:00001",
            "chunk_index": 1,
            "chunk_total": 1,
            "turn_count": 1,
        },
        preserve_raw=preserve_raw,
    )

    metadata = manager.by_id["existing"]["metadata"]
    assert engine.state.data[counter] == 1
    assert metadata["import_source_files"] == ["first.md", "second.md"]
    assert metadata["import_source_hashes"] == ["hash-a", "hash-b"]
    assert "import_source_file" not in metadata
    assert "import_source_hash" not in metadata
    assert engine._duplicate_match_allowed(manager.by_id["existing"], "hash-b") is True


@pytest.mark.asyncio
async def test_protected_match_does_not_suppress_import_evidence(tmp_path):
    item = {
        "name": "protected evidence",
        "content": "This exact memory also needs its own import source evidence.",
        "domain": ["life"],
        "tags": ["evidence"],
        "importance": 5,
    }
    manager = RecordingBucketManager()
    manager.by_id["protected"] = {
        "id": "protected",
        "content": item["content"],
        "metadata": {"protected": True, "type": "permanent"},
    }
    engine = ImportEngine(
        {"buckets_dir": str(tmp_path)},
        manager,
        StaticLlmDehydrator([item]),
    )

    await engine._process_single_chunk(
        {
            "content": "[Amy] evidence",
            "source_file": "history.md",
            "source_hash": "new-source",
            "source_chunk_id": "new-source:00001",
            "chunk_index": 1,
            "chunk_total": 1,
            "turn_count": 1,
        },
        preserve_raw=False,
    )

    assert len(manager.created) == 1
    assert manager.created[0]["extra_metadata"]["import_source_hash"] == "new-source"
