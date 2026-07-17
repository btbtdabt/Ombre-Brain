"""Lifecycle-owned schedulers for current-production maintenance work."""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from self_anchor import is_self_anchor_bucket
from word_map import reflection_identity_terms


SleepCallable = Callable[[float], Awaitable[None]]
NowCallable = Callable[[], datetime]
CycleCallable = Callable[[], Awaitable[Any]]
IntervalCallable = Callable[[], float]
EngineFactory = Callable[[dict[str, Any]], Any]


def _default_dream_engine_factory(config: dict[str, Any]) -> Any:
    from dream_engine import DreamEngine

    return DreamEngine(config)


def _default_embedding_engine_factory(config: dict[str, Any]) -> Any:
    from embedding_engine import EmbeddingEngine

    return EmbeddingEngine(config)


@dataclass(frozen=True, slots=True)
class SchedulerInitialDelays:
    """Startup staggering retained from the current-production runtime."""

    reflection: float = 20.0
    portrait: float = 25.0
    dream: float = 30.0
    word_map: float = 35.0


def _int_between(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(low, min(high, number))


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _date_key(value: Any) -> str:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else ""


def _mapping_section(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    section = config.get(name, {})
    return section if isinstance(section, Mapping) else {}


def _word_map_daily_rebuild_settings(
    config_arg: Mapping[str, Any],
) -> dict[str, int | bool]:
    word_map_cfg = _mapping_section(config_arg, "word_map")
    return {
        "enabled": _bool_value(word_map_cfg.get("enabled"), False)
        and _bool_value(word_map_cfg.get("daily_rebuild_enabled"), True),
        "hour": _int_between(word_map_cfg.get("daily_rebuild_hour"), 4, 0, 23),
        "minute": _int_between(
            word_map_cfg.get("daily_rebuild_minute"), 30, 0, 59
        ),
        "include_archive": _bool_value(
            word_map_cfg.get("daily_rebuild_include_archive"), False
        ),
        "check_interval_seconds": _int_between(
            word_map_cfg.get("daily_rebuild_check_interval_minutes"),
            15,
            1,
            1440,
        )
        * 60,
    }


def _word_map_daily_target(
    now: datetime, settings: Mapping[str, int | bool]
) -> datetime:
    return now.replace(
        hour=int(settings.get("hour") or 0),
        minute=int(settings.get("minute") or 0),
        second=0,
        microsecond=0,
    )


def _word_map_should_run_daily_rebuild(
    now: datetime,
    last_run_date: str,
    settings: Mapping[str, int | bool],
) -> bool:
    if not settings.get("enabled"):
        return False
    date_key = now.date().isoformat()
    if last_run_date == date_key:
        return False
    return now >= _word_map_daily_target(now, settings)


def _refresh_word_map_private_terms(collaborators: Any) -> list[str]:
    terms: set[str] = set()
    identity_store = getattr(collaborators, "identity_semantic_store", None)
    if identity_store is not None:
        try:
            terms.update(
                str(alias).strip()
                for node in identity_store.load_private_nodes()
                for alias in node.seed_aliases
                if str(alias).strip()
            )
        except Exception as exc:
            collaborators.logger.warning(
                "Failed to load private identity seed aliases: %s", exc
            )
    try:
        terms.update(
            str(term).strip()
            for term in reflection_identity_terms(dict(collaborators.config))
            if str(term).strip()
        )
    except Exception as exc:
        collaborators.logger.warning(
            "Failed to load reflection identity role aliases: %s", exc
        )
    store = collaborators.word_map_store
    if terms:
        store.private_terms |= terms
    return sorted(terms)


async def _rebuild_word_map_index(
    collaborators: Any,
    *,
    include_archive: bool = False,
) -> dict[str, Any]:
    private_terms = _refresh_word_map_private_terms(collaborators)
    buckets = await collaborators.bucket_mgr.list_all(
        include_archive=include_archive
    )
    buckets = [bucket for bucket in buckets if not is_self_anchor_bucket(bucket)]
    stats = collaborators.word_map_store.rebuild(buckets)
    if inspect.isawaitable(stats):
        stats = await stats
    return {
        "status": "rebuilt",
        "bucket_count": len(buckets),
        "include_archive": include_archive,
        "stats": stats,
        "private_terms_excluded": private_terms,
    }


def _bucket_needs_memory_enrichment(bucket: Any) -> bool:
    if not isinstance(bucket, dict) or is_self_anchor_bucket(bucket):
        return False
    metadata = bucket.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    if metadata.get("type") == "feel" or metadata.get("protected"):
        return False
    try:
        confidence = float(metadata.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError, OverflowError):
        confidence = 0.0
    return confidence <= 0.0


def _enrichment_sort_key(bucket: dict[str, Any]) -> str:
    metadata = bucket.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    return str(metadata.get("updated_at") or metadata.get("created") or "")


async def _backfill_memory_enrichment(
    collaborators: Any,
    limit: int | None = None,
) -> dict[str, Any]:
    reflection_cfg = _mapping_section(collaborators.config, "reflection")
    default_limit = _int_between(
        reflection_cfg.get("enrich_backfill_limit"), 5, 0, 50
    )
    limit = _int_between(limit, default_limit, 0, 50)
    if limit <= 0:
        return {"processed": 0, "ids": [], "errors": []}

    try:
        all_buckets = await collaborators.bucket_mgr.list_all(
            include_archive=False
        )
    except Exception as exc:
        collaborators.logger.warning(
            "Memory enrichment backfill list failed / enrich 补跑列桶失败: %s",
            exc,
        )
        return {"processed": 0, "ids": [], "errors": [str(exc)]}

    candidates = [
        bucket for bucket in all_buckets if _bucket_needs_memory_enrichment(bucket)
    ]
    candidates.sort(key=_enrichment_sort_key, reverse=True)

    processed: list[str] = []
    errors: list[str] = []
    for bucket in candidates[:limit]:
        bucket_id = str(bucket.get("id") or "").strip()
        if not bucket_id:
            continue
        try:
            await collaborators.reflection_engine.enrich_bucket(
                bucket_id,
                collaborators.bucket_mgr,
                collaborators.memory_edge_store,
                embedding_engine=collaborators.embedding_engine,
                force=True,
            )
            refresh = getattr(collaborators, "refresh_bucket_indexes", None)
            if callable(refresh):
                updated_bucket = await collaborators.bucket_mgr.get(bucket_id)
                if isinstance(updated_bucket, dict):
                    refresh_result = refresh(updated_bucket)
                    if inspect.isawaitable(refresh_result):
                        await refresh_result
            processed.append(bucket_id)
        except Exception as exc:
            collaborators.logger.warning(
                "Memory enrichment backfill failed / enrich 补跑失败: %s: %s",
                bucket_id,
                exc,
            )
            errors.append(f"{bucket_id}: {exc}")
    return {"processed": len(processed), "ids": processed, "errors": errors}


def _store_daily_activity_summary_result(
    result: Any,
    portrait_engine_arg: Any,
    logger: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"status": "invalid", "reason": "result_not_object"}
    if result.get("status") != "ready":
        return result
    item = result.get("activity_summary")
    item = item if isinstance(item, dict) else {}
    if not item:
        return {**result, "status": "skipped", "reason": "empty_activity_summary"}
    date_key = str(result.get("date") or item.get("source_date") or "").strip()
    try:
        stored = portrait_engine_arg.upsert_recent_timeline_item(item, date_key)
    except Exception as exc:
        if logger is not None:
            logger.warning("Daily activity summary portrait upsert failed: %s", exc)
        return {**result, "status": "error", "error": str(exc)}
    return {**result, "status": "stored", "portrait": stored}


def _daily_activity_materials_from_reflection_results(
    results: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    daily_impressions: list[dict[str, Any]] = []
    for result in results or []:
        if not isinstance(result, dict):
            continue
        candidates.extend(
            item
            for item in (result.get("candidates") or [])
            if isinstance(item, dict)
        )
        daily_impression = result.get("daily_impression")
        if isinstance(daily_impression, dict):
            daily_impressions.append(daily_impression)
    return candidates, daily_impressions


async def _daily_impression_material_for_date(
    date_key: str,
    bucket_mgr_arg: Any,
) -> dict[str, Any]:
    safe_date = _date_key(date_key)
    if not safe_date:
        return {}
    try:
        bucket = await bucket_mgr_arg.get(f"reflection_daily_{safe_date}")
    except Exception:
        bucket = None
    if not isinstance(bucket, dict):
        return {}
    metadata = bucket.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    return {
        "id": bucket.get("id") or f"reflection_daily_{safe_date}",
        "content": bucket.get("content") or "",
        "confidence": metadata.get("confidence", 0.7),
        "date": safe_date,
    }


class CurrentSchedulers:
    """Own all current-production maintenance tasks for one runtime lifespan."""

    def __init__(
        self,
        collaborators: Any,
        *,
        logger: Any | None = None,
        sleep: SleepCallable = asyncio.sleep,
        now: NowCallable = datetime.now,
        initial_delays: SchedulerInitialDelays | None = None,
        dream_engine_factory: EngineFactory = _default_dream_engine_factory,
        embedding_engine_factory: EngineFactory = _default_embedding_engine_factory,
    ) -> None:
        self.collaborators = collaborators
        self.logger = logger or collaborators.logger
        self._sleep = sleep
        self._now = now
        self._initial_delays = initial_delays or SchedulerInitialDelays()
        self._dream_engine_factory = dream_engine_factory
        self._embedding_engine_factory = embedding_engine_factory
        self._dream_interval_seconds = 3600.0
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._word_map_last_run_date: str | None = None

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(self._tasks.values())

    @property
    def task_names(self) -> set[str]:
        return set(self._tasks)

    def _reflection_config(self) -> Mapping[str, Any]:
        return _mapping_section(self.collaborators.config, "reflection")

    def _apply_reflection_scheduler_config(self) -> bool:
        engine = self.collaborators.reflection_engine
        reflection_cfg = self._reflection_config()
        engine.enabled = bool(reflection_cfg.get("enabled", True))
        engine.auto_enabled = bool(reflection_cfg.get("auto_enabled", True))
        if not engine.enabled or not engine.auto_enabled:
            return False
        engine.daily_enabled = bool(reflection_cfg.get("daily_enabled", True))
        engine.memory_affect_anchor_enabled = bool(
            reflection_cfg.get("memory_affect_anchor_enabled", False)
        )
        engine.relationship_weather_affect_anchor_enabled = bool(
            reflection_cfg.get(
                "relationship_weather_affect_anchor_enabled", False
            )
        )
        engine.daily_min_memory_items = _int_between(
            reflection_cfg.get("daily_min_memory_items"), 5, 0, 100
        )
        engine.daily_conversation_turn_limit = _int_between(
            reflection_cfg.get("daily_conversation_turn_limit"), 12, 0, 80
        )
        engine.daily_activity_summary_enabled = bool(
            reflection_cfg.get("daily_activity_summary_enabled", True)
        )
        engine.daily_activity_summary_turn_limit = _int_between(
            reflection_cfg.get("daily_activity_summary_turn_limit"),
            getattr(engine, "daily_activity_summary_turn_limit", 0),
            0,
            10_000,
        )
        engine.daily_activity_summary_max_tokens = _int_between(
            reflection_cfg.get("daily_activity_summary_max_tokens"),
            getattr(engine, "daily_activity_summary_max_tokens", 320),
            80,
            1000,
        )
        mode = str(
            reflection_cfg.get("daily_chat_memory_mode") or "review"
        ).strip().lower()
        engine.daily_chat_memory_mode = (
            mode if mode in {"auto", "review", "off"} else "review"
        )
        engine.daily_chat_memory_hour = _int_between(
            reflection_cfg.get("daily_chat_memory_hour"), 0, 0, 23
        )
        engine.daily_chat_memory_turn_limit = _int_between(
            reflection_cfg.get("daily_chat_memory_turn_limit"), 0, 0, 10_000
        )
        engine.daily_chat_memory_max_per_day = _int_between(
            reflection_cfg.get("daily_chat_memory_max_per_day"), 3, 0, 10
        )
        return True

    async def run_reflection_once(self) -> list[dict[str, Any]]:
        if not self._apply_reflection_scheduler_config():
            return []

        runtime = self.collaborators
        engine = runtime.reflection_engine
        raw_results = await engine.run_due(
            runtime.bucket_mgr,
            runtime.persona_engine,
            runtime.embedding_engine,
            runtime.gateway_state_store,
            runtime.raw_event_store,
        )
        results = list(raw_results or [])
        now_local = engine._local_now()
        if (
            getattr(engine, "daily_activity_summary_enabled", True)
            and now_local.hour >= engine.daily_chat_memory_hour
        ):
            activity_date = (now_local - timedelta(days=1)).date().isoformat()
            timeline_id = f"daily_activity_summary:{activity_date}"
            if not runtime.portrait_engine.has_recent_timeline_item(
                date_key=activity_date,
                source="daily_activity_summary",
                timeline_id=timeline_id,
            ):
                activity_candidates, activity_daily_impressions = (
                    _daily_activity_materials_from_reflection_results(results)
                )
                if not activity_daily_impressions:
                    existing_daily_impression = (
                        await _daily_impression_material_for_date(
                            activity_date,
                            runtime.bucket_mgr,
                        )
                    )
                    if existing_daily_impression:
                        activity_daily_impressions.append(existing_daily_impression)
                activity_result = await engine.run_daily_activity_summary(
                    conversation_turn_store=runtime.gateway_state_store,
                    raw_event_store=runtime.raw_event_store,
                    persona_engine=runtime.persona_engine,
                    daily_chat_memory_candidates=activity_candidates,
                    daily_impressions=activity_daily_impressions,
                    key=activity_date,
                )
                stored_activity = _store_daily_activity_summary_result(
                    activity_result,
                    runtime.portrait_engine,
                    self.logger,
                )
                if stored_activity.get("status") not in {"disabled", "skipped"}:
                    results.append(stored_activity)

        if results:
            self.logger.info(
                "Reflection run-due results / 反思定时结果: %s", results
            )

        reflection_cfg = self._reflection_config()
        if reflection_cfg.get("enrich_backfill_enabled", True):
            backfill_result = await _backfill_memory_enrichment(
                runtime,
                limit=reflection_cfg.get("enrich_backfill_limit"),
            )
            if backfill_result.get("processed"):
                self.logger.info(
                    "Memory enrichment backfill / 记忆 enrich 补跑: %s",
                    backfill_result,
                )
        return results

    async def run_portrait_once(self) -> list[dict[str, Any]]:
        runtime = self.collaborators
        results = await runtime.portrait_engine.run_due(
            runtime.bucket_mgr,
            runtime.persona_engine,
        )
        results = list(results or [])
        if results:
            self.logger.info(
                "Portrait run-due results / 画像定时结果: %s", results
            )
        return results

    async def run_word_map_once(self) -> dict[str, Any] | None:
        settings = _word_map_daily_rebuild_settings(self.collaborators.config)
        if self._word_map_last_run_date is None:
            initial_now = self._now()
            self._word_map_last_run_date = (
                initial_now.date().isoformat()
                if _word_map_should_run_daily_rebuild(initial_now, "", settings)
                else ""
            )
            settings = _word_map_daily_rebuild_settings(
                self.collaborators.config
            )
        now = self._now()
        if not _word_map_should_run_daily_rebuild(
            now,
            self._word_map_last_run_date,
            settings,
        ):
            return None
        result = await _rebuild_word_map_index(
            self.collaborators,
            include_archive=bool(settings.get("include_archive")),
        )
        self._word_map_last_run_date = now.date().isoformat()
        self.logger.info(
            "Word Map daily rebuild result / 词图每日重建结果: %s", result
        )
        return result

    async def run_dream_once(self) -> dict[str, Any]:
        runtime = self.collaborators
        dream_engine = self._dream_engine_factory(runtime.config)
        embedding_engine = self._embedding_engine_factory(runtime.config)
        self._dream_interval_seconds = self._engine_interval(dream_engine)
        try:
            result = await dream_engine.run_due(
                runtime.bucket_mgr,
                embedding_engine,
                raw_event_store=runtime.raw_event_store,
            )
        finally:
            close = getattr(embedding_engine, "aclose", None)
            if callable(close):
                close_result = close()
                if inspect.isawaitable(close_result):
                    await close_result
        if result and result.get("status") == "created":
            self.logger.info(
                "Dream run-due result / 夜梦定时结果: %s", result
            )
        return result

    def _dream_interval(self) -> float:
        return self._dream_interval_seconds

    @staticmethod
    def _engine_interval(engine: Any) -> float:
        try:
            return max(0.0, float(engine.check_interval_minutes) * 60.0)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return 3600.0

    def _word_map_interval(self) -> float:
        settings = _word_map_daily_rebuild_settings(self.collaborators.config)
        return float(settings.get("check_interval_seconds") or 900)

    async def _periodic_loop(
        self,
        *,
        initial_delay: float,
        cycle: CycleCallable,
        interval: IntervalCallable,
        failure_message: str,
        log_traceback: bool = False,
    ) -> None:
        await self._sleep(max(0.0, initial_delay))
        while True:
            try:
                await cycle()
            except Exception as exc:
                if log_traceback:
                    self.logger.warning(failure_message, exc, exc_info=True)
                else:
                    self.logger.warning(failure_message, exc)
            await self._sleep(max(0.0, interval()))

    async def _reflection_loop(self) -> None:
        await self._periodic_loop(
            initial_delay=self._initial_delays.reflection,
            cycle=self.run_reflection_once,
            interval=lambda: self._engine_interval(
                self.collaborators.reflection_engine
            ),
            failure_message="Reflection scheduler failed / 反思定时器失败: %s",
        )

    async def _portrait_loop(self) -> None:
        await self._periodic_loop(
            initial_delay=self._initial_delays.portrait,
            cycle=self.run_portrait_once,
            interval=lambda: self._engine_interval(
                self.collaborators.portrait_engine
            ),
            failure_message="Portrait scheduler failed / 画像定时器失败: %s",
        )

    async def _word_map_loop(self) -> None:
        await self._periodic_loop(
            initial_delay=self._initial_delays.word_map,
            cycle=self.run_word_map_once,
            interval=self._word_map_interval,
            failure_message="Word Map daily rebuild failed / 词图每日重建失败: %s",
            log_traceback=True,
        )

    async def _dream_loop(self) -> None:
        await self._periodic_loop(
            initial_delay=self._initial_delays.dream,
            cycle=self.run_dream_once,
            interval=self._dream_interval,
            failure_message="Dream scheduler failed / 夜梦定时器失败: %s",
        )

    def _start_task(
        self,
        name: str,
        cycle: Coroutine[Any, Any, None],
    ) -> None:
        self._tasks[name] = asyncio.create_task(cycle, name=name)

    async def start(self) -> None:
        if self._tasks:
            return

        reflection_engine = self.collaborators.reflection_engine
        if reflection_engine.enabled and reflection_engine.auto_enabled:
            self._start_task(
                "ombre-reflection-scheduler", self._reflection_loop()
            )
            self.logger.info(
                "Reflection scheduler enabled / 反思定时器已启用"
            )

        portrait_engine = self.collaborators.portrait_engine
        if portrait_engine.enabled and portrait_engine.auto_enabled:
            self._start_task("ombre-portrait-scheduler", self._portrait_loop())
            self.logger.info("Portrait scheduler enabled / 画像定时器已启用")

        word_map_cfg = _mapping_section(self.collaborators.config, "word_map")
        if _bool_value(word_map_cfg.get("daily_rebuild_enabled"), True):
            self._start_task("ombre-word-map-scheduler", self._word_map_loop())
            self.logger.info(
                "Word Map daily rebuild scheduler started / "
                "词图每日重建定时器已启动"
            )

        self._start_task("ombre-dream-scheduler", self._dream_loop())
        self.logger.info("Dream scheduler loop started / 夜梦定时器循环已启动")

    async def stop(self) -> None:
        if not self._tasks:
            return
        tasks = tuple(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["CurrentSchedulers", "SchedulerInitialDelays"]
