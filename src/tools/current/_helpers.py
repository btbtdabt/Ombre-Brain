"""Shared helpers for the current-production compatibility tools."""

from __future__ import annotations

import inspect
import math
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any

from identity import identity_names
from memory_metadata import normalize_memory_metadata
from self_anchor import is_self_anchor_bucket
from utils import bucket_text_for_embedding, strip_wikilinks

from .. import _runtime as rt


MEMORY_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*$")
_FIRST_PERSON_RE = re.compile(r"(?i)\b(?:i|me|my|mine|myself)\b")
_FORBIDDEN_HEADINGS = {
    "followup",
    "followups",
    "followuplog",
    "followupslog",
    "todo",
    "todolog",
    "next",
    "后续",
    "后续待办",
    "后续记录",
    "待办",
    "待办事项",
    "待办记录",
    "affectanchor",
}
_REFLECTION_HEADINGS = {"reflection", "assistantreflection", "havenreflection"}


def require_runtime(name: str) -> Any:
    value = getattr(rt, name, None)
    if value is None:
        raise RuntimeError(f"tools runtime slot is not initialized: {name}")
    return value


async def call_async(func: Callable[..., object], /, *args: Any, **kwargs: Any) -> Any:
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def mapping_or_empty(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def dict_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, dict)]


def runtime_config() -> dict:
    return rt.config if isinstance(rt.config, dict) else {}


def identity() -> dict:
    return identity_names(runtime_config())


def ai_author_name() -> str:
    return identity()["ai_name"]


def coerce_id(value: Any) -> str:
    return "" if value is None else str(value).strip()


def valid_id(value: Any) -> bool:
    return bool(MEMORY_ID_RE.fullmatch(coerce_id(value)))


def bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def int_between(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def float_between(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return max(minimum, min(maximum, parsed))


def split_csv(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = (str(item) for item in value)
    else:
        raw = str(value or "").split(",")
    return list(dict.fromkeys(item.strip() for item in raw if item.strip()))


def uses_first_person_voice(text: Any) -> bool:
    value = str(text or "").strip()
    return bool("我" in value or _FIRST_PERSON_RE.search(value))


def _normalized_heading(value: str) -> str:
    return re.sub(r"[\s_\-:：]+", "", str(value or "").strip().lower())


def memory_write_contract_error(content: Any, *, feel_only: bool = False) -> str:
    """Reject current-invalid memory shapes before they reach Markdown storage."""
    text = strip_wikilinks(str(content or "")).strip()
    matches = list(_HEADING_RE.finditer(text))
    if feel_only:
        if matches:
            return "feel 只写第一人称正文，不写标题、Markdown 分段或 ### section。"
        if not uses_first_person_voice(text):
            return "feel 必须改成第一人称正文，用“我……”表达模型自己的感受。"
        return ""

    for index, match in enumerate(matches):
        heading = _normalized_heading(match.group(1))
        if heading in _FORBIDDEN_HEADINGS:
            if heading == "affectanchor":
                return "新记忆不接受 ### affect_anchor；它不是模型可写的 content section。"
            return (
                "新记忆不接受 ### followup / todo。需要长期保留的回应变化请改写进第一人称 "
                "### reflection；需要到时提醒的事项请用 reminder_create。"
            )
        if heading not in _REFLECTION_HEADINGS:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if not uses_first_person_voice(text[match.end() : end]):
            return "### reflection 必须用模型第一人称写，用“我记得 / 我明白 / 我以后 / 我会”等表达。"
    return ""


async def ensure_decay_started() -> None:
    engine = require_runtime("decay_engine")
    starter = getattr(engine, "ensure_started", None)
    if callable(starter):
        await call_async(starter)


def score_bucket(bucket: dict) -> float:
    engine = getattr(rt, "decay_engine", None)
    calculate = getattr(engine, "calculate_score", None)
    if not callable(calculate):
        return 0.0
    try:
        return float_between(calculate(bucket.get("metadata", {})), 0.0, float("-inf"), float("inf"))
    except Exception:
        return 0.0


def bucket_read_payload(bucket: dict) -> dict:
    meta = bucket.get("metadata", {}) if isinstance(bucket, dict) else {}
    metadata_view = normalize_memory_metadata(bucket)
    fields = (
        "id",
        "name",
        "type",
        "domain",
        "tags",
        "facets",
        "importance",
        "valence",
        "arousal",
        "model_valence",
        "pinned",
        "protected",
        "resolved",
        "digested",
        "anchor",
        "status",
        "weight",
        "dont_surface",
        "why_remembered",
        "meaning",
        "media",
        "provenance",
        "source_tool",
        "grow_batch_id",
        "triggered_by",
        "source",
        "confidence",
        "period",
        "date",
        "event_date",
        "created",
        "updated_at",
        "last_active",
        "activation_count",
        "comment_count",
        "comments",
        "profile_kind",
        "subject",
        "predicate",
        "object",
        "evidence",
        "source_bucket_ids",
        "source_persona_event_ids",
        "source_conversation_turn_ids",
        "source_raw_event_ids",
        "active",
        "deprecated",
    )
    return {
        "id": bucket.get("id", ""),
        "metadata": {key: meta.get(key) for key in fields if key in meta},
        "metadata_view": metadata_view,
        **metadata_view,
        "content": strip_wikilinks(bucket.get("content", "")),
        "score": score_bucket(bucket),
    }


def _metadata_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def bucket_light_payload(bucket: dict) -> dict:
    meta = bucket.get("metadata", {}) if isinstance(bucket, dict) else {}
    metadata_view = normalize_memory_metadata(bucket)
    return {
        "id": bucket.get("id", ""),
        "bucket_id": bucket.get("id", ""),
        "name": meta.get("name", bucket.get("id", "")),
        "type": meta.get("type", "dynamic"),
        "domain": meta.get("domain", []),
        "tags": meta.get("tags", []),
        "facets": meta.get("facets", []),
        "source": meta.get("source", ""),
        "importance": meta.get("importance", 5),
        "confidence": meta.get("confidence", 0.5),
        "created": _metadata_text(meta.get("created")),
        "updated_at": _metadata_text(meta.get("updated_at")),
        "last_active": _metadata_text(meta.get("last_active")),
        "resolved": bool(meta.get("resolved", False)),
        "digested": bool(meta.get("digested", False)),
        "pinned": bool(meta.get("pinned", False)),
        "protected": bool(meta.get("protected", False)),
        "anchor": bool(meta.get("anchor", False)),
        "self_anchor": is_self_anchor_bucket(bucket),
        "metadata_view": metadata_view,
        **metadata_view,
    }


def bucket_text(bucket: dict) -> str:
    try:
        return bucket_text_for_embedding(bucket)
    except Exception:
        return str(bucket.get("content") or "")


async def queue_embedding_refresh(bucket_id: str) -> bool:
    callback = getattr(rt, "queue_embedding_refresh", None)
    if callable(callback):
        return bool(await call_async(callback, bucket_id))

    manager = require_runtime("bucket_mgr")
    bucket = await manager.get(bucket_id)
    if not bucket:
        return False
    outbox = getattr(rt, "embedding_outbox", None) or getattr(manager, "embedding_outbox", None)
    enqueue = getattr(outbox, "enqueue", None)
    if callable(enqueue):
        try:
            queued = bool(enqueue(bucket_id, bucket_text(bucket), reset_retry=True))
            ensure_started = getattr(outbox, "ensure_started", None)
            if queued and callable(ensure_started):
                ensure_started()
            return queued
        except Exception as exc:
            logger = getattr(rt, "logger", None)
            warning = getattr(logger, "warning", None)
            if callable(warning):
                warning("Current tool embedding refresh failed: %s", exc)
            return False
    return False


def refresh_bucket_indexes(bucket: dict) -> None:
    if not bucket:
        return
    callback = getattr(rt, "refresh_bucket_indexes", None)
    if callable(callback):
        callback(bucket)
        return

    logger = getattr(rt, "logger", None)
    moment_store = getattr(rt, "memory_moment_store", None)
    upsert = getattr(moment_store, "upsert_bucket", None)
    if callable(upsert):
        try:
            upsert(bucket)
        except Exception as exc:
            warning = getattr(logger, "warning", None)
            if callable(warning):
                warning("Current tool moment refresh failed: %s", exc)

    node_store = getattr(rt, "memory_node_store", None)
    upsert_node = getattr(node_store, "upsert_bucket", None)
    if callable(upsert_node):
        try:
            upsert_node(bucket)
        except Exception as exc:
            warning = getattr(logger, "warning", None)
            if callable(warning):
                warning("Current tool node refresh failed: %s", exc)

    entity_store = getattr(rt, "entity_edge_store", None)
    replace = getattr(entity_store, "replace_bucket_edges", None)
    if callable(replace):
        try:
            from entity_edges import extract_entity_edges_from_bucket

            replace(
                str(bucket.get("id") or ""),
                extract_entity_edges_from_bucket(bucket, identity()),
            )
        except Exception as exc:
            warning = getattr(logger, "warning", None)
            if callable(warning):
                warning("Current tool entity refresh failed: %s", exc)


async def analyze_content(content: str) -> dict:
    dehydrator = require_runtime("dehydrator")
    try:
        analysis = mapping_or_empty(await call_async(dehydrator.analyze, content))
    except Exception as exc:
        logger = getattr(rt, "logger", None)
        warning = getattr(logger, "warning", None)
        if callable(warning):
            warning("Current tool metadata analysis failed; using defaults: %s", exc)
        default_analysis = getattr(dehydrator, "_default_analysis", None)
        analysis = mapping_or_empty(default_analysis() if callable(default_analysis) else {})
    return {
        "domain": split_csv(analysis.get("domain") or ["general"]),
        "valence": float_between(analysis.get("valence"), 0.5, 0.0, 1.0),
        "arousal": float_between(analysis.get("arousal"), 0.3, 0.0, 1.0),
        "importance": int_between(analysis.get("importance"), 5, 1, 10),
        "tags": split_csv(analysis.get("tags")),
        "suggested_name": str(analysis.get("suggested_name") or "").strip(),
    }


def date_key(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return ""
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return ""


def bucket_created_date(bucket: dict) -> str:
    meta = bucket.get("metadata", {})
    value = str(meta.get("created") or "")[:10]
    return date_key(value)
