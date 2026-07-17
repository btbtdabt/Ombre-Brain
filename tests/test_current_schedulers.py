from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

import current_schedulers
from current_schedulers import (
    CurrentSchedulers,
    SchedulerInitialDelays,
    _backfill_memory_enrichment,
    _daily_activity_materials_from_reflection_results,
    _daily_impression_material_for_date,
    _rebuild_word_map_index,
    _store_daily_activity_summary_result,
    _word_map_daily_rebuild_settings,
    _word_map_should_run_daily_rebuild,
)
from server_app import RuntimeLifecycle


class RecordingLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def _record(self, level: str, message: str, *args: object) -> None:
        self.messages.append((level, message % args if args else message))

    def info(self, message: str, *args: object) -> None:
        self._record("info", message, *args)

    def warning(self, message: str, *args: object, **_kwargs: object) -> None:
        self._record("warning", message, *args)


def test_word_map_daily_settings_and_due_check_match_origin_behavior() -> None:
    settings = _word_map_daily_rebuild_settings(
        {
            "word_map": {
                "enabled": "yes",
                "daily_rebuild_enabled": "true",
                "daily_rebuild_hour": 29,
                "daily_rebuild_minute": -4,
                "daily_rebuild_include_archive": "on",
                "daily_rebuild_check_interval_minutes": 0,
            }
        }
    )

    assert settings == {
        "enabled": True,
        "hour": 23,
        "minute": 0,
        "include_archive": True,
        "check_interval_seconds": 60,
    }
    before = datetime(2026, 7, 17, 22, 59)
    due = datetime(2026, 7, 17, 23, 0)
    assert not _word_map_should_run_daily_rebuild(before, "", settings)
    assert _word_map_should_run_daily_rebuild(due, "", settings)
    assert not _word_map_should_run_daily_rebuild(due, "2026-07-17", settings)


def test_daily_activity_helpers_preserve_material_and_storage_contract() -> None:
    candidates, impressions = _daily_activity_materials_from_reflection_results(
        [
            {
                "candidates": [{"id": "candidate-1"}, "invalid"],
                "daily_impression": {"id": "reflection_daily_2026-07-16"},
            },
            {"candidates": [{"id": "candidate-2"}]},
            "invalid",
        ]
    )
    portrait = SimpleNamespace(
        upsert_recent_timeline_item=lambda item, date_key: {
            "timeline_id": item["timeline_id"],
            "date": date_key,
        }
    )

    stored = _store_daily_activity_summary_result(
        {
            "status": "ready",
            "date": "2026-07-16",
            "activity_summary": {
                "timeline_id": "daily_activity_summary:2026-07-16"
            },
        },
        portrait,
    )

    assert candidates == [{"id": "candidate-1"}, {"id": "candidate-2"}]
    assert impressions == [{"id": "reflection_daily_2026-07-16"}]
    assert stored["status"] == "stored"
    assert stored["portrait"]["date"] == "2026-07-16"


@pytest.mark.asyncio
async def test_daily_impression_helper_reads_the_origin_bucket_shape() -> None:
    class Manager:
        async def get(self, bucket_id: str):
            assert bucket_id == "reflection_daily_2026-07-16"
            return {
                "id": bucket_id,
                "content": "A quiet but productive day.",
                "metadata": {"confidence": 0.82},
            }

    assert await _daily_impression_material_for_date("not-a-date", Manager()) == {}
    assert await _daily_impression_material_for_date("2026-07-16", Manager()) == {
        "id": "reflection_daily_2026-07-16",
        "content": "A quiet but productive day.",
        "confidence": 0.82,
        "date": "2026-07-16",
    }


@pytest.mark.asyncio
async def test_reflection_cycle_refreshes_config_and_runs_activity_summary() -> None:
    logger = RecordingLogger()
    now = datetime(2026, 7, 17, 6, 30)
    run_due_args: tuple[object, ...] | None = None
    activity_kwargs: dict[str, object] | None = None

    class ReflectionEngine:
        enabled = True
        auto_enabled = True
        check_interval_minutes = 7
        daily_enabled = True
        memory_affect_anchor_enabled = False
        relationship_weather_affect_anchor_enabled = False
        daily_min_memory_items = 5
        daily_conversation_turn_limit = 12
        daily_activity_summary_turn_limit = 12
        daily_activity_summary_max_tokens = 320
        daily_chat_memory_mode = "review"
        daily_chat_memory_hour = 0
        daily_chat_memory_turn_limit = 0
        daily_chat_memory_max_per_day = 3

        def _local_now(self) -> datetime:
            return now

        async def run_due(self, *args: object):
            nonlocal run_due_args
            run_due_args = args
            return [{"candidates": [{"id": "candidate-1"}]}]

        async def run_daily_activity_summary(self, **kwargs: object):
            nonlocal activity_kwargs
            activity_kwargs = kwargs
            return {
                "status": "ready",
                "date": "2026-07-16",
                "activity_summary": {
                    "timeline_id": "daily_activity_summary:2026-07-16",
                    "source": "daily_activity_summary",
                },
            }

    class Manager:
        async def get(self, bucket_id: str):
            assert bucket_id == "reflection_daily_2026-07-16"
            return {
                "id": bucket_id,
                "content": "Fallback impression",
                "metadata": {"confidence": 0.7},
            }

    class PortraitEngine:
        def __init__(self) -> None:
            self.upserts: list[tuple[dict[str, object], str]] = []

        def has_recent_timeline_item(self, **kwargs: object) -> bool:
            assert kwargs == {
                "date_key": "2026-07-16",
                "source": "daily_activity_summary",
                "timeline_id": "daily_activity_summary:2026-07-16",
            }
            return False

        def upsert_recent_timeline_item(
            self, item: dict[str, object], date_key: str
        ) -> dict[str, object]:
            self.upserts.append((item, date_key))
            return {"stored": True, "date": date_key}

    manager = Manager()
    reflection = ReflectionEngine()
    portrait = PortraitEngine()
    persona = object()
    embedding = object()
    gateway = object()
    raw_events = object()
    runtime = SimpleNamespace(
        config={
            "reflection": {
                "enabled": True,
                "auto_enabled": True,
                "daily_enabled": False,
                "memory_affect_anchor_enabled": True,
                "relationship_weather_affect_anchor_enabled": True,
                "daily_min_memory_items": 200,
                "daily_conversation_turn_limit": -2,
                "daily_activity_summary_enabled": True,
                "daily_activity_summary_turn_limit": 20_000,
                "daily_activity_summary_max_tokens": 40,
                "daily_chat_memory_mode": "AUTO",
                "daily_chat_memory_hour": -3,
                "daily_chat_memory_turn_limit": 20_000,
                "daily_chat_memory_max_per_day": 20,
                "enrich_backfill_enabled": False,
            }
        },
        bucket_mgr=manager,
        reflection_engine=reflection,
        portrait_engine=portrait,
        persona_engine=persona,
        embedding_engine=embedding,
        gateway_state_store=gateway,
        raw_event_store=raw_events,
        logger=logger,
    )

    results = await CurrentSchedulers(runtime).run_reflection_once()

    assert run_due_args == (manager, persona, embedding, gateway, raw_events)
    assert reflection.daily_enabled is False
    assert reflection.memory_affect_anchor_enabled is True
    assert reflection.relationship_weather_affect_anchor_enabled is True
    assert reflection.daily_min_memory_items == 100
    assert reflection.daily_conversation_turn_limit == 0
    assert reflection.daily_activity_summary_turn_limit == 10_000
    assert reflection.daily_activity_summary_max_tokens == 80
    assert reflection.daily_chat_memory_mode == "auto"
    assert reflection.daily_chat_memory_hour == 0
    assert reflection.daily_chat_memory_turn_limit == 10_000
    assert reflection.daily_chat_memory_max_per_day == 10
    assert activity_kwargs is not None
    assert activity_kwargs["daily_chat_memory_candidates"] == [
        {"id": "candidate-1"}
    ]
    assert activity_kwargs["daily_impressions"] == [
        {
            "id": "reflection_daily_2026-07-16",
            "content": "Fallback impression",
            "confidence": 0.7,
            "date": "2026-07-16",
        }
    ]
    assert activity_kwargs["key"] == "2026-07-16"
    assert portrait.upserts[0][1] == "2026-07-16"
    assert results[-1]["status"] == "stored"


@pytest.mark.asyncio
async def test_reflection_cycle_deduplicates_existing_activity_summary() -> None:
    class ReflectionEngine:
        enabled = True
        auto_enabled = True
        check_interval_minutes = 60
        daily_activity_summary_enabled = True
        daily_chat_memory_hour = 0

        def _local_now(self) -> datetime:
            return datetime(2026, 7, 17, 4)

        async def run_due(self, *_args: object):
            return []

        async def run_daily_activity_summary(self, **_kwargs: object):
            pytest.fail("existing portrait timeline item must suppress a duplicate")

    portrait = SimpleNamespace(has_recent_timeline_item=lambda **_kwargs: True)
    runtime = SimpleNamespace(
        config={"reflection": {"enrich_backfill_enabled": False}},
        reflection_engine=ReflectionEngine(),
        portrait_engine=portrait,
        bucket_mgr=object(),
        persona_engine=object(),
        embedding_engine=object(),
        gateway_state_store=object(),
        raw_event_store=object(),
        logger=RecordingLogger(),
    )

    assert await CurrentSchedulers(runtime).run_reflection_once() == []


@pytest.mark.asyncio
async def test_enrichment_backfill_filters_orders_refreshes_and_isolates_errors() -> None:
    logger = RecordingLogger()
    buckets = [
        {
            "id": "good",
            "metadata": {"confidence": 0, "updated_at": "2026-07-16T10:00:00"},
        },
        {
            "id": "broken",
            "metadata": {"confidence": 0, "updated_at": "2026-07-17T10:00:00"},
        },
        {"id": "known", "metadata": {"confidence": 0.3}},
        {"id": "feel", "metadata": {"confidence": 0, "type": "feel"}},
        {"id": "protected", "metadata": {"confidence": 0, "protected": True}},
        {"id": "self", "metadata": {"confidence": 0, "self_anchor": True}},
    ]

    class Manager:
        async def list_all(self, *, include_archive: bool):
            assert include_archive is False
            return buckets

        async def get(self, bucket_id: str):
            return next(item for item in buckets if item["id"] == bucket_id)

    class ReflectionEngine:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object, object, bool]] = []

        async def enrich_bucket(
            self,
            bucket_id: str,
            manager: object,
            edge_store: object,
            *,
            embedding_engine: object,
            force: bool,
        ) -> None:
            self.calls.append((bucket_id, manager, embedding_engine, force))
            if bucket_id == "broken":
                raise RuntimeError("classification failed")

    refreshed: list[str] = []
    manager = Manager()
    engine = ReflectionEngine()
    runtime = SimpleNamespace(
        config={"reflection": {"enrich_backfill_limit": 5}},
        bucket_mgr=manager,
        reflection_engine=engine,
        memory_edge_store=object(),
        embedding_engine=object(),
        refresh_bucket_indexes=lambda bucket: refreshed.append(bucket["id"]),
        logger=logger,
    )

    result = await _backfill_memory_enrichment(runtime, limit=5)

    assert [call[0] for call in engine.calls] == ["broken", "good"]
    assert all(call[1] is manager and call[3] is True for call in engine.calls)
    assert refreshed == ["good"]
    assert result == {
        "processed": 1,
        "ids": ["good"],
        "errors": ["broken: classification failed"],
    }


@pytest.mark.asyncio
async def test_word_map_cycle_skips_startup_catchup_then_rebuilds_next_day() -> None:
    current_now = datetime(2026, 7, 17, 5, 0)
    normal = {"id": "normal", "metadata": {"confidence": 0.8}}
    self_anchor = {"id": "self", "metadata": {"self_anchor": True}}

    class Manager:
        def __init__(self) -> None:
            self.include_archive: list[bool] = []

        async def list_all(self, *, include_archive: bool):
            self.include_archive.append(include_archive)
            return [normal, self_anchor]

    class WordMapStore:
        enabled = True

        def __init__(self) -> None:
            self.private_terms: set[str] = set()
            self.rebuilt: list[list[dict[str, object]]] = []

        def rebuild(self, buckets: list[dict[str, object]]):
            self.rebuilt.append(buckets)
            return {"nodes": len(buckets)}

    manager = Manager()
    store = WordMapStore()
    identity_store = SimpleNamespace(
        load_private_nodes=lambda: [SimpleNamespace(seed_aliases=["private-name"])]
    )
    runtime = SimpleNamespace(
        config={
            "word_map": {
                "enabled": True,
                "daily_rebuild_enabled": True,
                "daily_rebuild_hour": 4,
                "daily_rebuild_minute": 30,
                "daily_rebuild_include_archive": True,
            }
        },
        bucket_mgr=manager,
        word_map_store=store,
        identity_semantic_store=identity_store,
        logger=RecordingLogger(),
    )
    scheduler = CurrentSchedulers(runtime, now=lambda: current_now)

    assert await scheduler.run_word_map_once() is None
    assert store.rebuilt == []

    current_now = datetime(2026, 7, 18, 5, 0)
    result = await scheduler.run_word_map_once()

    assert result is not None
    assert result["status"] == "rebuilt"
    assert result["bucket_count"] == 1
    assert result["include_archive"] is True
    assert result["private_terms_excluded"] == ["private-name"]
    assert manager.include_archive == [True]
    assert store.rebuilt == [[normal]]
    assert "private-name" in store.private_terms


@pytest.mark.asyncio
async def test_word_map_first_cycle_resamples_clock_after_startup_suppression() -> None:
    clock = iter(
        [
            datetime(2026, 7, 17, 4, 29, 59),
            datetime(2026, 7, 17, 4, 30, 0),
        ]
    )

    class Manager:
        async def list_all(self, *, include_archive: bool):
            assert include_archive is False
            return [{"id": "normal", "metadata": {}}]

    class Store:
        enabled = True

        def __init__(self) -> None:
            self.private_terms: set[str] = set()

        def rebuild(self, buckets: list[dict[str, object]]):
            return {"nodes": len(buckets)}

    runtime = SimpleNamespace(
        config={
            "word_map": {
                "enabled": True,
                "daily_rebuild_enabled": True,
                "daily_rebuild_hour": 4,
                "daily_rebuild_minute": 30,
            }
        },
        bucket_mgr=Manager(),
        word_map_store=Store(),
        identity_semantic_store=None,
        logger=RecordingLogger(),
    )

    result = await CurrentSchedulers(runtime, now=lambda: next(clock)).run_word_map_once()

    assert result is not None
    assert result["status"] == "rebuilt"


@pytest.mark.asyncio
async def test_word_map_rebuild_isolates_reflection_identity_term_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = RecordingLogger()

    def fail_reflection_terms(_config: dict[str, object]) -> list[str]:
        raise ValueError("bad identity role config")

    monkeypatch.setattr(
        current_schedulers,
        "reflection_identity_terms",
        fail_reflection_terms,
    )

    class Manager:
        async def list_all(self, *, include_archive: bool):
            assert include_archive is False
            return [{"id": "normal", "metadata": {}}]

    class Store:
        def __init__(self) -> None:
            self.private_terms: set[str] = set()

        def rebuild(self, buckets: list[dict[str, object]]):
            return {"nodes": len(buckets)}

    store = Store()
    runtime = SimpleNamespace(
        config={"reflection": {"identity_role_edges": "invalid"}},
        bucket_mgr=Manager(),
        word_map_store=store,
        identity_semantic_store=SimpleNamespace(
            load_private_nodes=lambda: [SimpleNamespace(seed_aliases=["private-name"])]
        ),
        logger=logger,
    )

    result = await _rebuild_word_map_index(runtime)

    assert result["status"] == "rebuilt"
    assert result["private_terms_excluded"] == ["private-name"]
    assert store.private_terms == {"private-name"}
    warnings = "\n".join(
        message for level, message in logger.messages if level == "warning"
    )
    assert "Failed to load reflection identity role aliases" in warnings


@pytest.mark.asyncio
async def test_portrait_cycle_reuses_runtime_and_dream_cycle_uses_factories() -> None:
    logger = RecordingLogger()
    bucket_mgr = object()
    persona = object()
    embedding = object()
    raw_events = object()

    class PortraitEngine:
        enabled = True
        auto_enabled = True
        check_interval_minutes = 11

        async def run_due(self, *args: object):
            assert args == (bucket_mgr, persona)
            return [{"status": "updated"}]

    class DreamEngine:
        check_interval_minutes = 13

        async def run_due(self, *args: object, **kwargs: object):
            assert args == (bucket_mgr, embedding)
            assert kwargs == {"raw_event_store": raw_events}
            return {"status": "created", "id": "dream-1"}

    runtime = SimpleNamespace(
        config={},
        bucket_mgr=bucket_mgr,
        persona_engine=persona,
        embedding_engine=embedding,
        raw_event_store=raw_events,
        portrait_engine=PortraitEngine(),
        dream_engine=DreamEngine(),
        logger=logger,
    )
    scheduler = CurrentSchedulers(
        runtime,
        dream_engine_factory=lambda _config: runtime.dream_engine,
        embedding_engine_factory=lambda _config: embedding,
    )

    assert await scheduler.run_portrait_once() == [{"status": "updated"}]
    assert await scheduler.run_dream_once() == {
        "status": "created",
        "id": "dream-1",
    }
    info = "\n".join(message for level, message in logger.messages if level == "info")
    assert "Portrait run-due results" in info
    assert "Dream run-due result" in info


@pytest.mark.asyncio
async def test_dream_cycle_reloads_engines_and_interval_from_live_config() -> None:
    config = {
        "dream": {"revision": 1, "check_interval_minutes": 7},
        "embedding": {"revision": "first"},
    }
    bucket_mgr = object()
    raw_events = object()
    created_dreams: list[object] = []
    created_embeddings: list[object] = []
    run_pairs: list[tuple[int, str]] = []

    class FreshDreamEngine:
        def __init__(self, revision: int, interval: int) -> None:
            self.revision = revision
            self.check_interval_minutes = interval

        async def run_due(
            self,
            manager: object,
            embedding_engine: object,
            *,
            raw_event_store: object,
        ) -> dict[str, object]:
            assert manager is bucket_mgr
            assert raw_event_store is raw_events
            run_pairs.append(
                (self.revision, str(getattr(embedding_engine, "revision")))
            )
            return {"status": "created", "revision": self.revision}

    def dream_factory(config_arg: dict[str, object]) -> FreshDreamEngine:
        settings = config_arg["dream"]
        assert isinstance(settings, dict)
        engine = FreshDreamEngine(
            int(settings["revision"]),
            int(settings["check_interval_minutes"]),
        )
        created_dreams.append(engine)
        return engine

    def embedding_factory(config_arg: dict[str, object]):
        settings = config_arg["embedding"]
        assert isinstance(settings, dict)

        class FreshEmbedding:
            def __init__(self, revision: str) -> None:
                self.revision = revision
                self.closed = False

            async def aclose(self) -> None:
                self.closed = True

        engine = FreshEmbedding(str(settings["revision"]))
        created_embeddings.append(engine)
        return engine

    runtime = SimpleNamespace(
        config=config,
        bucket_mgr=bucket_mgr,
        raw_event_store=raw_events,
        dream_engine=SimpleNamespace(check_interval_minutes=99),
        logger=RecordingLogger(),
    )
    scheduler = CurrentSchedulers(
        runtime,
        dream_engine_factory=dream_factory,
        embedding_engine_factory=embedding_factory,
    )

    first = await scheduler.run_dream_once()
    config["dream"] = {"revision": 2, "check_interval_minutes": 17}
    config["embedding"] = {"revision": "second"}
    second = await scheduler.run_dream_once()

    assert first["revision"] == 1
    assert second["revision"] == 2
    assert len({id(engine) for engine in created_dreams}) == 2
    assert len({id(engine) for engine in created_embeddings}) == 2
    assert run_pairs == [(1, "first"), (2, "second")]
    assert all(getattr(engine, "closed") for engine in created_embeddings)
    assert scheduler._dream_interval() == 17 * 60


@pytest.mark.asyncio
async def test_dream_cycle_closes_fresh_embedding_after_failure() -> None:
    class FailingDreamEngine:
        check_interval_minutes = 5

        async def run_due(self, *_args: object, **_kwargs: object):
            raise RuntimeError("dream failed")

    class FreshEmbedding:
        closed = False

        async def aclose(self) -> None:
            self.closed = True

    embedding = FreshEmbedding()
    runtime = SimpleNamespace(
        config={},
        bucket_mgr=object(),
        raw_event_store=object(),
        logger=RecordingLogger(),
    )
    scheduler = CurrentSchedulers(
        runtime,
        dream_engine_factory=lambda _config: FailingDreamEngine(),
        embedding_engine_factory=lambda _config: embedding,
    )

    with pytest.raises(RuntimeError, match="dream failed"):
        await scheduler.run_dream_once()

    assert embedding.closed is True


@pytest.mark.asyncio
async def test_scheduler_start_and_stop_owns_named_cancellable_tasks() -> None:
    class IdleEngine:
        enabled = True
        auto_enabled = True
        check_interval_minutes = 60

    runtime = SimpleNamespace(
        config={"word_map": {"daily_rebuild_enabled": True}},
        reflection_engine=IdleEngine(),
        portrait_engine=IdleEngine(),
        dream_engine=IdleEngine(),
        logger=RecordingLogger(),
    )
    delays = SchedulerInitialDelays(
        reflection=3600,
        portrait=3600,
        dream=3600,
        word_map=3600,
    )
    scheduler = CurrentSchedulers(runtime, initial_delays=delays)

    await scheduler.start()
    await scheduler.start()
    await asyncio.sleep(0)
    tasks = scheduler.tasks

    assert scheduler.task_names == {
        "ombre-reflection-scheduler",
        "ombre-portrait-scheduler",
        "ombre-word-map-scheduler",
        "ombre-dream-scheduler",
    }
    assert len(tasks) == 4

    await scheduler.stop()
    await scheduler.stop()

    assert scheduler.tasks == ()
    assert all(task.done() for task in tasks)


@pytest.mark.asyncio
async def test_runtime_lifecycle_stops_schedulers_before_shared_services() -> None:
    events: list[str] = []

    class Service:
        def __init__(self, name: str) -> None:
            self.name = name

        async def start(self) -> None:
            events.append(f"{self.name}:start")

        async def stop(self) -> None:
            events.append(f"{self.name}:stop")

        async def aclose(self) -> None:
            events.append(f"{self.name}:close")

    lifecycle = RuntimeLifecycle(
        logger=RecordingLogger(),
        decay_engine=Service("decay"),
        embedding_outbox=Service("outbox"),
        embedding_engine=Service("embedding"),
        current_schedulers=Service("schedulers"),
    )

    await lifecycle.start()
    await lifecycle.stop()

    assert events == [
        "decay:start",
        "outbox:start",
        "schedulers:start",
        "schedulers:stop",
        "outbox:stop",
        "embedding:close",
        "decay:stop",
    ]
