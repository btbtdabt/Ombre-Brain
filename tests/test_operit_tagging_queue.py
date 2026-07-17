from __future__ import annotations

import asyncio
import json
from collections import Counter

import pytest

from import_memory import ImportEngine


def _operit_backup(count: int) -> str:
    return json.dumps(
        {
            "exportDate": 1784246400000,
            "links": [],
            "memories": [
                {
                    "uuid": f"00000000-0000-0000-0000-{index:012d}",
                    "title": f"Entry {index}",
                    "content": f"raw Operit content {index}",
                    "tagNames": ["original"],
                    "createdAt": 1784246400000 + index,
                    "updatedAt": 1784246400000 + index,
                }
                for index in range(1, count + 1)
            ],
        }
    )


class RecordingEmbeddingEngine:
    async def get_embedding(self, _bucket_id: str):
        return None

    async def generate_and_store(self, *_args, **_kwargs):
        raise AssertionError("ImportEngine must not write embeddings directly")


class RecordingBucketManager:
    def __init__(self, events: list[tuple[str, str]]):
        self.events = events
        self.by_id: dict[str, dict] = {}

    async def get(self, bucket_id: str):
        return self.by_id.get(bucket_id)

    async def create(self, **kwargs):
        bucket_id = str(kwargs["bucket_id"])
        self.events.append(("create", bucket_id))
        self.by_id[bucket_id] = {
            "id": bucket_id,
            "content": kwargs["content"],
            "metadata": {
                "name": kwargs.get("name") or "",
                "tags": list(kwargs.get("tags") or []),
                "domain": list(kwargs.get("domain") or []),
                "valence": kwargs.get("valence", 0.5),
                "arousal": kwargs.get("arousal", 0.3),
                "last_active": kwargs.get("last_active"),
                "updated_at": kwargs.get("updated_at"),
                **dict(kwargs.get("extra_metadata") or {}),
            },
        }
        return bucket_id

    async def ensure_embedding_index(self, bucket_id: str) -> bool:
        assert bucket_id in self.by_id
        self.events.append(("embed", bucket_id))
        await asyncio.sleep(0)
        return True

    async def update(self, bucket_id: str, **kwargs) -> bool:
        bucket = self.by_id[bucket_id]
        assert "content" not in kwargs
        self.events.append(("tag", bucket_id))
        metadata = bucket["metadata"]
        for key in ("tags", "domain", "valence", "arousal", "last_active", "updated_at"):
            if key in kwargs:
                metadata[key] = kwargs[key]
        for key, value in dict(kwargs.get("extra_metadata") or {}).items():
            if value is None:
                metadata.pop(key, None)
            else:
                metadata[key] = value
        return True


class FailOnceEmbeddingBucketManager(RecordingBucketManager):
    def __init__(self, events: list[tuple[str, str]]):
        super().__init__(events)
        self.failed = False

    async def ensure_embedding_index(self, bucket_id: str) -> bool:
        assert bucket_id in self.by_id
        self.events.append(("embed", bucket_id))
        if not self.failed:
            self.failed = True
            return False
        return True


class FailOnceRawBucketManager(RecordingBucketManager):
    def __init__(self, events: list[tuple[str, str]]):
        super().__init__(events)
        self.create_attempts = 0

    async def create(self, **kwargs):
        self.create_attempts += 1
        if self.create_attempts == 2:
            raise OSError("transient raw write failure")
        return await super().create(**kwargs)


class FailingAttemptMetadataManager(RecordingBucketManager):
    async def update(self, bucket_id: str, **kwargs) -> bool:
        extra = dict(kwargs.get("extra_metadata") or {})
        if extra.get("operit_tagging_status") in {"pending", "failed"}:
            raise OSError("tagging attempt metadata unavailable")
        return await super().update(bucket_id, **kwargs)


class BoundedRetryDehydrator:
    api_available = True
    model = "tagger-test"

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.attempts: Counter[str] = Counter()

    async def analyze(self, content: str) -> dict:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.attempts[content] += 1
        try:
            await asyncio.sleep(0.01)
            if content.endswith(" 2") and self.attempts[content] == 1:
                raise RuntimeError("retry once")
            return {
                "domain": ["Imported"],
                "tags": ["model-tag"],
                "valence": 0.7,
                "arousal": 0.4,
                "memory_subject": "user",
                "memory_layer": "event",
                "memory_classification_source": "model",
            }
        finally:
            self.active -= 1


class NoTaggingDehydrator:
    api_available = False


@pytest.mark.asyncio
async def test_operit_raw_entries_land_before_manager_embedding_and_bounded_tagging(
    tmp_path,
):
    events: list[tuple[str, str]] = []
    manager = RecordingBucketManager(events)
    dehydrator = BoundedRetryDehydrator()
    engine = ImportEngine(
        {
            "buckets_dir": str(tmp_path / "buckets"),
            "state_dir": str(tmp_path / "state"),
            "import": {
                "operit_tagging_concurrency": 2,
                "operit_tagging_max_attempts": 3,
                "operit_tagging_retry_base_seconds": 0,
            },
        },
        manager,
        dehydrator,
        RecordingEmbeddingEngine(),
    )

    result = await engine.start(
        _operit_backup(5),
        filename="operit.json",
        import_mode="operit",
        operit_tagging=True,
    )

    event_names = [name for name, _bucket_id in events]
    assert event_names[:5] == ["create"] * 5
    assert event_names[5:10] == ["embed"] * 5
    assert dehydrator.max_active == 2
    assert dehydrator.attempts["raw Operit content 2"] == 2
    assert result["operit_phase"] == "completed"
    assert result["embeddings_processed"] == 5
    assert result["tagging_succeeded"] == 5
    assert result["tagging_failed"] == 0
    assert result["tagging_pending"] == 0
    assert result["api_calls"] == 6
    assert {bucket["content"] for bucket in manager.by_id.values()} == {
        f"raw Operit content {index}" for index in range(1, 6)
    }
    assert all(
        bucket["metadata"]["operit_tagging_status"] == "done"
        for bucket in manager.by_id.values()
    )


@pytest.mark.asyncio
async def test_operit_embedding_refresh_is_owned_by_bucket_manager(tmp_path):
    events: list[tuple[str, str]] = []
    manager = RecordingBucketManager(events)
    engine = ImportEngine(
        {
            "buckets_dir": str(tmp_path / "buckets"),
            "state_dir": str(tmp_path / "state"),
        },
        manager,
        NoTaggingDehydrator(),
        embedding_engine=None,
    )

    result = await engine.start(
        _operit_backup(1),
        import_mode="operit",
        operit_tagging=False,
    )

    assert [name for name, _bucket_id in events] == ["create", "embed"]
    assert result["embeddings_created"] == 1
    assert result["embeddings_failed"] == 0


@pytest.mark.asyncio
async def test_failed_operit_embedding_pauses_and_is_retried_on_resume(tmp_path):
    events: list[tuple[str, str]] = []
    manager = FailOnceEmbeddingBucketManager(events)
    config = {
        "buckets_dir": str(tmp_path / "buckets"),
        "state_dir": str(tmp_path / "state"),
    }
    raw = _operit_backup(2)
    engine = ImportEngine(
        config,
        manager,
        NoTaggingDehydrator(),
        RecordingEmbeddingEngine(),
    )

    paused = await engine.start(
        raw,
        import_mode="operit",
        operit_tagging=False,
    )

    assert paused["status"] == "paused"
    assert paused["operit_phase"] == "embedding"
    assert paused["embeddings_processed"] == 0
    assert paused["embeddings_failed"] == 1

    completed = await engine.start(
        raw,
        resume=True,
        import_mode="operit",
        operit_tagging=False,
    )

    first_bucket_id = next(iter(manager.by_id))
    assert completed["status"] == "completed"
    assert completed["embeddings_processed"] == 2
    assert [event for event in events if event == ("embed", first_bucket_id)] == [
        ("embed", first_bucket_id),
        ("embed", first_bucket_id),
    ]


@pytest.mark.asyncio
async def test_raw_write_failure_pauses_before_derived_phases_and_resumes(tmp_path):
    events: list[tuple[str, str]] = []
    manager = FailOnceRawBucketManager(events)
    engine = ImportEngine(
        {
            "buckets_dir": str(tmp_path / "buckets"),
            "state_dir": str(tmp_path / "state"),
        },
        manager,
        NoTaggingDehydrator(),
        RecordingEmbeddingEngine(),
    )
    raw = _operit_backup(3)

    paused = await engine.start(
        raw,
        import_mode="operit",
        operit_tagging=False,
    )

    assert paused["status"] == "paused"
    assert paused["operit_phase"] == "raw"
    assert paused["processed"] == 1
    assert [name for name, _bucket_id in events] == ["create"]

    completed = await engine.start(
        raw,
        resume=True,
        import_mode="operit",
        operit_tagging=False,
    )

    assert completed["status"] == "completed"
    assert completed["processed"] == 3
    assert [name for name, _bucket_id in events].count("create") == 3
    assert [name for name, _bucket_id in events].count("embed") == 3


class PausingDehydrator:
    api_available = True
    model = "pausing-tagger"

    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.contents: list[str] = []

    async def analyze(self, content: str) -> dict:
        self.contents.append(content)
        self.entered.set()
        await self.release.wait()
        return {
            "domain": ["Imported"],
            "tags": ["resumed"],
            "valence": 0.5,
            "arousal": 0.3,
            "memory_subject": "user",
            "memory_layer": "event",
            "memory_classification_source": "model",
        }


class ImmediateTagger:
    api_available = True
    model = "resume-tagger"

    def __init__(self):
        self.contents: list[str] = []

    async def analyze(self, content: str) -> dict:
        self.contents.append(content)
        return {
            "domain": ["Imported"],
            "tags": ["resumed"],
            "valence": 0.5,
            "arousal": 0.3,
            "memory_subject": "user",
            "memory_layer": "event",
            "memory_classification_source": "model",
        }


@pytest.mark.asyncio
async def test_operit_tagging_resumes_only_pending_entries(tmp_path):
    events: list[tuple[str, str]] = []
    manager = RecordingBucketManager(events)
    config = {
        "buckets_dir": str(tmp_path / "buckets"),
        "state_dir": str(tmp_path / "state"),
        "import": {
            "operit_tagging_concurrency": 1,
            "operit_tagging_retry_base_seconds": 0,
        },
    }
    raw = _operit_backup(2)
    pausing = PausingDehydrator()
    first_engine = ImportEngine(
        config,
        manager,
        pausing,
        RecordingEmbeddingEngine(),
    )

    first_task = asyncio.create_task(
        first_engine.start(
            raw,
            filename="resume-operit.json",
            import_mode="operit",
            operit_tagging=True,
        )
    )
    await asyncio.wait_for(pausing.entered.wait(), timeout=2)
    first_engine.pause()
    pausing.release.set()
    paused = await asyncio.wait_for(first_task, timeout=2)

    assert paused["status"] == "paused"
    assert paused["tagging_succeeded"] == 1
    assert paused["tagging_pending"] == 1
    assert paused["embeddings_processed"] == 2
    resumed_tagger = ImmediateTagger()
    resumed_engine = ImportEngine(
        config,
        manager,
        resumed_tagger,
        RecordingEmbeddingEngine(),
    )
    completed = await resumed_engine.start(
        raw,
        filename="resume-operit.json",
        resume=True,
        import_mode="operit",
        operit_tagging=True,
    )

    assert completed["status"] == "completed"
    assert completed["tagging_succeeded"] == 2
    assert completed["tagging_pending"] == 0
    assert completed["embeddings_processed"] == 2
    assert resumed_tagger.contents == ["raw Operit content 2"]
    assert [name for name, _bucket_id in events].count("create") == 2


@pytest.mark.asyncio
async def test_cancelled_operit_import_persists_resumable_state(tmp_path):
    events: list[tuple[str, str]] = []
    manager = RecordingBucketManager(events)
    config = {
        "buckets_dir": str(tmp_path / "buckets"),
        "state_dir": str(tmp_path / "state"),
        "import": {
            "operit_tagging_concurrency": 1,
            "operit_tagging_retry_base_seconds": 0,
        },
    }
    raw = _operit_backup(1)
    pausing = PausingDehydrator()
    engine = ImportEngine(
        config,
        manager,
        pausing,
        RecordingEmbeddingEngine(),
    )
    task = asyncio.create_task(
        engine.start(raw, import_mode="operit", operit_tagging=True)
    )
    await asyncio.wait_for(pausing.entered.wait(), timeout=2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert engine.state.load() is True
    assert engine.state.data["status"] == "paused"
    assert engine.state.can_resume is True
    embed_count_before_resume = [
        name for name, _bucket_id in events
    ].count("embed")

    resumed_tagger = ImmediateTagger()
    resumed_engine = ImportEngine(
        config,
        manager,
        resumed_tagger,
        RecordingEmbeddingEngine(),
    )
    completed = await resumed_engine.start(
        raw,
        resume=True,
        import_mode="operit",
        operit_tagging=True,
    )

    assert completed["status"] == "completed"
    assert completed["tagging_succeeded"] == 1
    assert [name for name, _bucket_id in events].count("embed") == (
        embed_count_before_resume
    )


class AlwaysFailTagger:
    api_available = True
    model = "failing-tagger"

    def __init__(self):
        self.entered = asyncio.Event()
        self.calls = 0

    async def analyze(self, _content: str) -> dict:
        self.calls += 1
        self.entered.set()
        raise RuntimeError("retry later")


@pytest.mark.asyncio
async def test_pause_interrupts_operit_retry_backoff(tmp_path):
    events: list[tuple[str, str]] = []
    manager = RecordingBucketManager(events)
    tagger = AlwaysFailTagger()
    engine = ImportEngine(
        {
            "buckets_dir": str(tmp_path / "buckets"),
            "state_dir": str(tmp_path / "state"),
            "import": {
                "operit_tagging_concurrency": 1,
                "operit_tagging_max_attempts": 3,
                "operit_tagging_retry_base_seconds": 30,
            },
        },
        manager,
        tagger,
        RecordingEmbeddingEngine(),
    )

    task = asyncio.create_task(
        engine.start(
            _operit_backup(1),
            import_mode="operit",
            operit_tagging=True,
        )
    )
    await asyncio.wait_for(tagger.entered.wait(), timeout=2)
    engine.pause()
    paused = await asyncio.wait_for(task, timeout=1)

    bucket = next(iter(manager.by_id.values()))
    assert paused["status"] == "paused"
    assert paused["tagging_pending"] == 1
    assert bucket["metadata"]["operit_tagging_attempts"] == 1


@pytest.mark.asyncio
async def test_retry_bound_survives_attempt_metadata_failure_and_resume(tmp_path):
    events: list[tuple[str, str]] = []
    manager = FailingAttemptMetadataManager(events)
    tagger = AlwaysFailTagger()
    engine = ImportEngine(
        {
            "buckets_dir": str(tmp_path / "buckets"),
            "state_dir": str(tmp_path / "state"),
            "import": {
                "operit_tagging_concurrency": 1,
                "operit_tagging_max_attempts": 2,
                "operit_tagging_retry_base_seconds": 30,
            },
        },
        manager,
        tagger,
        RecordingEmbeddingEngine(),
    )
    raw = _operit_backup(1)
    task = asyncio.create_task(
        engine.start(raw, import_mode="operit", operit_tagging=True)
    )
    await asyncio.wait_for(tagger.entered.wait(), timeout=2)
    engine.pause()
    paused = await asyncio.wait_for(task, timeout=1)

    assert paused["status"] == "paused"
    assert engine.state.data["_operit_tagging_attempts"]
    assert tagger.calls == 1

    engine.operit_tagging_retry_base_seconds = 0
    completed = await engine.start(
        raw,
        resume=True,
        import_mode="operit",
        operit_tagging=True,
    )

    assert completed["status"] == "completed"
    assert completed["tagging_failed"] == 1
    assert tagger.calls == 2


@pytest.mark.asyncio
async def test_forced_operit_mode_rejects_conversation_input_and_releases_slot(
    tmp_path,
):
    engine = ImportEngine(
        {"buckets_dir": str(tmp_path)},
        RecordingBucketManager([]),
        BoundedRetryDehydrator(),
        RecordingEmbeddingEngine(),
    )

    with pytest.raises(ValueError, match="valid Operit"):
        await engine.start(
            "Human: this is a conversation",
            filename="chat.md",
            import_mode="operit",
        )

    assert engine.is_running is False
    assert engine.active_job_id == ""
