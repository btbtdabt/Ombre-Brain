"""Current-production memory tools on top of the modular P0 runtime."""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

from entity_edges import extract_entity_edges_from_bucket
from ombrebrain.storage.quote_store import normalize_quotes
from profile_facts import profile_key
from runtime_values import age_hours_since
from self_anchor import is_self_anchor_bucket
from utils import parse_human_date_reference, strip_human_date_references, strip_wikilinks

from .. import _runtime as rt
from .._common import (
    _quota_turn,
    apply_memory_detail_updates,
    apply_plan_change_log,
    check_content_size,
    check_grow_input_size,
    check_grow_items_payload,
    check_metadata_size,
    check_pinned_quota,
    check_query_size,
    check_duplicate_for,
    check_plan_resolution,
    merge_or_create,
)
from ..breath import dispatch as p0_breath_dispatch
from ..breath.feel import surface_feels
from ..dream import dispatch as p0_dream_dispatch
from ..grow.core import grow_items as p0_grow_items
from ..grow.retry_guard import request_fingerprint, run_once
from ..hold import _prepare_source_refs
from .._relation_link import link_new_bucket
from ..trace import dispatch as p0_trace_dispatch
from ._helpers import (
    ai_author_name,
    analyze_content,
    bool_value,
    bucket_created_date,
    bucket_light_payload,
    bucket_read_payload,
    bucket_text,
    call_async,
    coerce_id,
    date_key,
    dict_items,
    ensure_decay_started,
    float_between,
    identity,
    int_between,
    memory_write_contract_error,
    queue_embedding_refresh,
    refresh_bucket_indexes,
    require_runtime,
    runtime_config,
    score_bucket,
    split_csv,
    uses_first_person_voice,
    valid_id,
)
from .breath_handoff import build_handoff_breath
from .breath_recall import (
    normalize_direct_render_mode,
    normalize_retrieval_mode,
    read_self_anchor_breath,
    search_breath,
    surface_breath,
)


async def _render_filtered_breath(
    *,
    date_value: str,
    domain: str,
    query: str,
    max_tokens: int,
    max_results: int,
) -> str:
    manager = require_runtime("bucket_mgr")
    domain_key = str(domain or "").strip().lower()
    private_channel = domain_key in {"feel", "whisper", "daily_impression"}
    all_buckets = await manager.list_all(
        include_archive=bool(date_value and not private_channel)
    )
    topic_query = strip_human_date_references(str(query or "")).strip()
    for shell_term in sorted(
        {
            "还记得",
            "记不记得",
            "记得",
            "记忆",
            "聊了什么",
            "聊什么",
            "说了什么",
            "提到什么",
            "讲了什么",
            "讨论什么",
            "做了什么",
            "发生了什么",
            "查一下",
            "找一下",
            "搜索",
            "什么事",
            "什么",
            "我们",
            "我",
            "你",
            "有",
            "吗",
            "呢",
            "的",
            "了",
        },
        key=len,
        reverse=True,
    ):
        topic_query = topic_query.replace(shell_term, " ")
    topic_query = topic_query.strip()
    selected = []
    for bucket in all_buckets:
        if is_self_anchor_bucket(bucket):
            continue
        meta = bucket.get("metadata", {})
        stored_dates = [
            str(meta.get(key) or "")[:10]
            for key in ("date", "event_date", "created", "updated_at", "last_active")
        ]
        if date_value and date_value not in stored_dates:
            continue
        bucket_domains = {str(item).strip().lower() for item in meta.get("domain", []) or []}
        tags = {str(item).strip().lower() for item in meta.get("tags", []) or []}
        if domain_key == "whisper":
            if meta.get("type") != "feel" or "whisper" not in tags:
                continue
        elif domain_key == "daily_impression":
            if meta.get("type") != "feel" or not (
                "daily_impression" in tags
                or "relationship_weather" in tags
                or meta.get("period") == "daily"
            ):
                continue
        elif domain_key == "feel":
            if meta.get("type") != "feel":
                continue
        elif date_value and meta.get("type") == "feel":
            continue
        elif domain_key and domain_key not in bucket_domains:
            continue
        if topic_query:
            haystack = " ".join(
                [
                    str(meta.get("name") or ""),
                    " ".join(str(item) for item in meta.get("tags", []) or []),
                    " ".join(str(item) for item in meta.get("domain", []) or []),
                    str(bucket.get("content") or ""),
                ]
            ).lower()
            meaningful_terms = re.findall(
                r"[A-Za-z]+[A-Za-z0-9_.:-]*|[\u4e00-\u9fff]{2,}",
                topic_query,
            )
            if meaningful_terms and not any(term.lower() in haystack for term in meaningful_terms):
                continue
        selected.append(bucket)

    selected.sort(
        key=lambda item: str(
            item.get("metadata", {}).get("date")
            or item.get("metadata", {}).get("created")
            or ""
        ),
        reverse=True,
    )
    if not selected:
        if date_value:
            return f"{date_value} 没有找到 {domain_key or '普通记忆'}。"
        if domain_key == "whisper":
            return "没有留下过 whisper。"
        if domain_key == "daily_impression":
            return "没有留下过 daily_impression。"
        return "没有留下过 feel。"

    parts: list[str] = []
    used = 0
    for bucket in selected[:max_results]:
        meta = bucket.get("metadata", {})
        created = meta.get("date") or meta.get("created", "")
        entry = (
            f"[{created}] [bucket_id:{bucket.get('id', '')}]\n"
            f"{strip_wikilinks(bucket.get('content', ''))}"
        )
        approximate_tokens = max(1, len(entry) // 4)
        if parts and used + approximate_tokens > max_tokens:
            break
        parts.append(entry)
        used += approximate_tokens
    if date_value and not private_channel:
        return f"=== 日期记忆 {date_value} ===\n" + "\n---\n".join(parts)
    title = domain_key or "feel"
    return f"=== 你留下的 {title} ===\n" + "\n---\n".join(parts)


def _query_requests_date_read(query: str) -> bool:
    text = str(query or "").strip()
    if not text or not parse_human_date_reference(text):
        return False
    return any(
        marker in text
        for marker in (
            "聊",
            "说",
            "提",
            "讲",
            "讨论",
            "查",
            "找",
            "搜索",
            "记得",
            "记忆",
            "做了什么",
            "发生",
            "什么事",
            "什么",
        )
    )


async def breath(
    query: str = "",
    max_tokens: int = 10000,
    domain: str = "",
    date: str = "",
    valence: float = -1,
    arousal: float = -1,
    max_results: int = 20,
    importance_min: int = -1,
    tags: str = "",
    catalog: bool = False,
    include_related: bool = True,
    related_per_memory: int = 1,
    edge_min_confidence: float = 0.55,
    include_core: bool = True,
    core_limit: int = 3,
    is_session_start: bool = False,
    debug: bool = False,
    surface: str = "manual",
    direct_render_mode: str = "auto",
    retrieval_mode: str = "graph",
    mode: str = "",
    session_id: str = "",
    quotes: bool = False,
) -> str:
    """只读检索记忆。查主题用 query；新窗口轻交接用 mode="handoff"；date 或 query 里的日期可查当天普通记忆；domain="feel"/"whisper" 读私密通道，domain="daily_impression" 才读日印象。日期支持 2026-06-15、2026.06.15、2026年6月15日、25年6月15日、6月15日。"""
    await ensure_decay_started()
    if query_error := check_query_size(str(query or "")):
        return query_error
    if metadata_error := check_metadata_size(domain=domain, tags=tags):
        return metadata_error
    max_results = int_between(max_results, 20, 1, 50)
    max_tokens = int_between(max_tokens, 10000, 0, 40000)
    importance_min = int_between(importance_min, -1, -1, 10)
    tags = str(tags or "")
    catalog = bool_value(catalog, False)

    # P0's catalog, importance, and tag branches are specialized read modes.
    # Keep those implementations intact while the richer current recall path
    # remains canonical for ordinary, dated, graph, and handoff retrieval.
    if catalog or importance_min >= 1 or split_csv(tags):
        return await p0_breath_dispatch(
            query=query,
            max_tokens=max_tokens,
            domain=domain,
            valence=valence,
            arousal=arousal,
            max_results=max_results,
            importance_min=importance_min,
            tags=tags,
            catalog=catalog,
            quotes=quotes,
        )

    # A generated bucket ID is an address only when it already exists.  The
    # broader public ID validator also accepts ordinary search tokens, dates,
    # and tag queries, so it cannot safely select this exact-read path alone.
    exact_bucket_id = coerce_id(query)
    exact_bucket = None
    if re.fullmatch(r"[0-9a-fA-F]{12}", exact_bucket_id):
        exact_bucket = await require_runtime("bucket_mgr").get(exact_bucket_id)
    if exact_bucket is not None:
        return await p0_breath_dispatch(
            query=exact_bucket_id,
            max_tokens=max_tokens,
            domain=domain,
            valence=valence,
            arousal=arousal,
            max_results=max_results,
            quotes=quotes,
        )

    include_related = bool_value(include_related, True)
    related_per_memory = int_between(related_per_memory, 1, 0, 5)
    edge_min_confidence = float_between(edge_min_confidence, 0.55, 0.0, 1.0)
    include_core = bool_value(include_core, True)
    core_limit = int_between(core_limit, 3, 0, 20)
    is_session_start = bool_value(is_session_start, False)
    debug = bool_value(debug, False)
    surface_key = str(surface or "manual").strip().lower()
    auto_surface = surface_key in {"auto", "automatic", "bridge", "gateway"}
    direct_render_mode = normalize_direct_render_mode(direct_render_mode)
    retrieval_mode = normalize_retrieval_mode(retrieval_mode)
    mode_key = str(mode or "").strip().lower()
    mode_key = mode_key if mode_key in {"", "handoff"} else ""
    raw_date = str(date or "").strip()
    parsed_date = parse_human_date_reference(raw_date or query)
    if raw_date and not parsed_date:
        return '日期格式没看懂。可以用 date="2026-06-15"、date="2026.06.15"、date="2026年6月15日"、date="25年6月15日" 或 date="6月15日"。'
    requested_date = (
        str((parsed_date or {}).get("date") or "")
        if raw_date or _query_requests_date_read(query)
        else ""
    )
    domain_key = str(domain or "").strip().lower()

    if not mode_key and is_session_start and not str(query or "").strip() and not domain_key:
        mode_key = "handoff"
    if mode_key == "handoff":
        return await build_handoff_breath(
            max_tokens=min(max_tokens or 1200, 1600),
            session_id=session_id,
            debug=debug,
        )

    if domain_key in {"自我", "self_anchor", "self_identity", "selfidentity", "self-identity"}:
        return await read_self_anchor_breath(
            query=query,
            max_tokens=max_tokens,
            limit=max_results,
            domain_entry=True,
        )

    if domain_key in {
        "todo",
        "todos",
        "followup",
        "followups",
        "pending",
        "unfinished",
        "待办",
        "未完成",
    } or (
        not domain_key
        and any(
            marker in str(query or "").lower().replace(" ", "")
            for marker in (
                "待办",
                "没做完",
                "没做",
                "未完成",
                "还没做",
                "todo",
                "pending",
                "unfinished",
                "followup",
                "follow-up",
            )
        )
    ):
        return "旧 followup/todo 派生待办已停用；请使用 reminder_list 或 /api/reminders 查看独立照顾备忘。"

    if domain_key in {"feel", "whisper", "daily_impression"}:
        return await _render_filtered_breath(
            date_value=requested_date,
            domain=domain,
            query=query,
            max_tokens=max_tokens,
            max_results=max_results,
        )

    query_key = str(query or "").strip().lower().strip(" \t\r\n`[]()")
    if query_key.startswith(("tag:", "标签:", "#")):
        tag_value = query_key.split(":", 1)[-1].lstrip("#").strip()
        if tag_value in {"自我", "self_anchor", "first_person_anchor", "first-person-anchor"}:
            return await read_self_anchor_breath(
                max_tokens=max_tokens,
                limit=max_results,
            )

    if requested_date:
        return await _render_filtered_breath(
            date_value=requested_date,
            domain=domain,
            query=query,
            max_tokens=max_tokens,
            max_results=max_results,
        )

    if not str(query or "").strip():
        return await surface_breath(
            max_tokens=max_tokens,
            max_results=max_results,
            include_related=include_related,
            related_per_memory=related_per_memory,
            edge_min_confidence=edge_min_confidence,
            include_core=include_core,
            core_limit=core_limit,
            valence=valence,
            arousal=arousal,
            is_session_start=is_session_start,
            auto_surface=auto_surface,
        )

    return await search_breath(
        query=query,
        max_tokens=max_tokens,
        domain=domain,
        valence=valence,
        arousal=arousal,
        max_results=max_results,
        include_related=include_related,
        related_per_memory=related_per_memory,
        edge_min_confidence=edge_min_confidence,
        is_session_start=is_session_start,
        debug=debug,
        auto_surface=auto_surface,
        direct_render_mode=direct_render_mode,
        retrieval_mode=retrieval_mode,
        with_quotes=bool_value(quotes, False),
    )


async def breath_search(
    query: str,
    domain: str = "",
    max_results: int = 20,
    quotes: bool = False,
) -> str:
    """按关键词或语义检索记忆，逐字返回当前正文。domain 可限定主题域，max_results 控制条数。quotes=True 时，直接命中的桶若保存过关键原话，会把引语附在正文后；默认不返回引语。"""
    return await breath(
        query=query,
        domain=domain,
        max_results=max_results,
        quotes=quotes,
    )


async def feel(query: str, max_tokens: int = 10000) -> str:
    """按关键词检索旧感受；逐字返回相关 feel，不返回未命中内容。"""
    await ensure_decay_started()
    if query_error := check_query_size(str(query or "")):
        return query_error
    return await surface_feels(
        query=str(query or "").strip(),
        max_tokens=int_between(max_tokens, 10000, 500, 20000),
    )


async def breath_advanced(
    query: str = "",
    max_tokens: int = 10000,
    domain: str = "",
    date: str = "",
    valence: float = -1,
    arousal: float = -1,
    max_results: int = 20,
    importance_min: int = -1,
    tags: str = "",
    catalog: bool = False,
    include_related: bool = True,
    related_per_memory: int = 1,
    edge_min_confidence: float = 0.55,
    include_core: bool = True,
    core_limit: int = 3,
    is_session_start: bool = False,
    debug: bool = False,
    surface: str = "manual",
    direct_render_mode: str = "auto",
    retrieval_mode: str = "graph",
    mode: str = "",
    session_id: str = "",
) -> str:
    """breath 的精细控制入口；日常主题检索优先用 breath_search。"""
    return await breath(
        query=query,
        max_tokens=max_tokens,
        domain=domain,
        date=date,
        valence=valence,
        arousal=arousal,
        max_results=max_results,
        importance_min=importance_min,
        tags=tags,
        catalog=catalog,
        include_related=include_related,
        related_per_memory=related_per_memory,
        edge_min_confidence=edge_min_confidence,
        include_core=include_core,
        core_limit=core_limit,
        is_session_start=is_session_start,
        debug=debug,
        surface=surface,
        direct_render_mode=direct_render_mode,
        retrieval_mode=retrieval_mode,
        mode=mode,
        session_id=session_id,
    )


async def read_bucket(bucket_id: str) -> dict:
    """按 bucket_id 精确读取完整记忆桶；trace/comment 前先读。只读，不刷新活跃度。"""
    bucket_id = coerce_id(bucket_id)
    if not valid_id(bucket_id):
        return {"error": "invalid bucket_id"}
    bucket = await require_runtime("bucket_mgr").get(bucket_id)
    if not bucket:
        return {"error": "not found", "id": bucket_id}
    return bucket_read_payload(bucket)


def _light_pagination_integer(value: Any, name: str) -> int:
    """Parse MCP pagination strictly instead of clamping invalid tool input."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[+-]?[0-9]+", text):
            try:
                return int(text)
            except ValueError:
                pass
    raise ValueError(f"{name} must be an integer")


async def list_buckets_light(
    include_archive: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> dict:
    """只读列出桶的轻量元数据；不返回正文，给同步脚本和外部索引用。"""
    try:
        safe_limit = _light_pagination_integer(limit, "limit")
        safe_offset = _light_pagination_integer(offset, "offset")
    except ValueError as exc:
        return {"error": str(exc), "buckets": []}
    if not 1 <= safe_limit <= 2000:
        return {
            "error": "limit must be between 1 and 2000",
            "buckets": [],
        }
    try:
        manager = require_runtime("bucket_mgr")
        max_offset = int(getattr(manager, "light_max_offset", 100_000))
        if safe_offset < 0:
            return {
                "error": f"offset must be between 0 and {max_offset}",
                "buckets": [],
            }
        if safe_offset > max_offset:
            return {
                "error": "offset exceeds maximum",
                "max_offset": max_offset,
                "buckets": [],
            }
        score_calculator = getattr(
            getattr(rt, "decay_engine", None),
            "calculate_score",
            None,
        )
        buckets, count = await manager.list_light(
            include_archive=bool_value(include_archive),
            limit=safe_limit,
            offset=safe_offset,
            sort="created_desc",
            score_calculator=score_calculator,
        )
        items = [bucket_light_payload(bucket) for bucket in buckets]
        return {
            "buckets": items,
            "count": count,
            "include_archive": bool_value(include_archive),
            "limit": safe_limit,
            "offset": safe_offset,
        }
    except Exception as exc:
        return {"error": str(exc), "buckets": []}


async def _create_current_bucket(
    content: str,
    *,
    tags: list[str],
    importance: int,
    domains: list[str],
    valence: float,
    arousal: float,
    name: str,
    bucket_type: str = "dynamic",
    pinned: bool = False,
    date: str = "",
    media: list | str | None = None,
    why_remembered: str = "",
    meaning: str = "",
    test_data: bool = False,
    triggered_by: str = "",
    source_tool: str = "hold",
    grow_batch_id: str = "",
    extra_metadata: dict | None = None,
    source_refs: list[dict] | None = None,
    quotes: list[dict] | None = None,
) -> str:
    manager = require_runtime("bucket_mgr")
    bucket_id = await manager.create(
        content=content,
        tags=tags,
        importance=importance,
        domain=domains,
        valence=valence,
        arousal=arousal,
        name=name or None,
        bucket_type=bucket_type,
        pinned=pinned,
        date=date or None,
        media=media,
        why_remembered=why_remembered,
        meaning=meaning,
        test_data=test_data,
        triggered_by=triggered_by,
        source_tool=source_tool,
        grow_batch_id=grow_batch_id,
        source_refs=source_refs,
        quotes=quotes,
        allow_embedding_fallback=True,
        extra_metadata=extra_metadata,
    )
    bucket = await manager.get(bucket_id)
    if bucket:
        refresh_bucket_indexes(bucket)
    if not test_data and bucket_type == "dynamic" and source_tool in {"hold", "grow"}:
        asyncio.create_task(link_new_bucket(bucket_id, content))
    return bucket_id


async def hold(
    content: str,
    tags: str = "",
    importance: int = 5,
    pinned: bool = False,
    feel: bool = False,
    whisper: bool = False,
    source_bucket: str = "",
    valence: float = -1,
    arousal: float = -1,
    title: str = "",
    date: str = "",
    domain: str = "",
    media: list | str | None = None,
    why_remembered: str = "",
    meaning: str = "",
    test_data: bool = False,
    source_content: str = "",
    source_ranges: list | None = None,
    quotes: list | None = None,
) -> str:
    """写一条长期记忆。单个事实/承诺/偏好用 hold；旧记忆的新感受用 comment_bucket；悄悄话用 whisper=True。date 可传事件日期；title 可选，传了就用给定标题，不传则自动生成。media 可传服务器上传临时目录内的路径，或 data_base64+filename 项。普通记忆的 domain 由系统自动判断；维护特殊桶时可显式传入。显式 valence/arousal 会覆盖自动情绪。普通记忆 content 的最小写入就是正文；需要结构化时按需使用 ### moment、### original、### reflection，reflection 使用“我……”第一人称。长期回应变化写进 reflection，到时提醒用 reminder_create。feel=True/whisper=True 时 content 使用第一人称正文。quotes 保存当下明确值得逐字保留的关键原话，可传字符串列表，或带 text/speaker/at 的对象列表；最多 3 句、每句 100 字，超限整次写入会被拒绝。引语仅在显式 breath_search(quotes=True) 时返回。"""
    await ensure_decay_started()
    content = str(content or "").strip()
    if not content:
        return "内容为空，无法存储。"
    if error := check_content_size(content):
        return error
    if contract_error := memory_write_contract_error(
        content, feel_only=bool_value(feel) or bool_value(whisper)
    ):
        return f"写入被拒绝：{contract_error}"

    extra_tags = split_csv(tags)
    requested_domains = split_csv(domain)
    why_remembered = str(why_remembered or "").strip()[:500]
    meaning = str(meaning or "").strip()
    test_data = bool_value(test_data, False)
    if test_data and (
        bool_value(pinned) or bool_value(feel) or bool_value(whisper)
    ):
        return "测试数据不能创建为 pinned、feel 或 whisper；请使用普通测试桶。"
    requested_valence = float(valence) if isinstance(valence, (int, float)) and 0 <= valence <= 1 else None
    requested_arousal = float(arousal) if isinstance(arousal, (int, float)) and 0 <= arousal <= 1 else None
    source_id = coerce_id(source_bucket)
    source_refs, source_error = _prepare_source_refs(source_content, source_ranges)
    if source_error:
        return source_error
    try:
        quote_items = normalize_quotes(quotes) if quotes not in (None, "", []) else []
    except ValueError as exc:
        return f"引语无效，未创建任何桶：{exc}"

    async def create_whisper() -> str:
        bucket_id = await _create_current_bucket(
            content,
            tags=list(dict.fromkeys([*extra_tags, "whisper"])),
            importance=5,
            domains=requested_domains,
            valence=requested_valence if requested_valence is not None else 0.5,
            arousal=requested_arousal if requested_arousal is not None else 0.3,
            name="",
            bucket_type="feel",
            date=str(date or "").strip(),
            media=media,
            why_remembered=why_remembered,
            meaning=meaning,
            source_refs=source_refs,
            quotes=quote_items or None,
        )
        return f"🫧whisper→{bucket_id}"

    if bool_value(whisper):
        if source_id:
            return "whisper 不需要 source_bucket；有源记忆的感受请用 comment_bucket。"
        return await create_whisper()

    if bool_value(feel):
        if source_id:
            if not valid_id(source_id):
                return "source_bucket 无效。"
            manager = require_runtime("bucket_mgr")
            if not await manager.get(source_id):
                return f"源记忆不存在: {source_id}"
            if why_remembered or meaning or quote_items:
                feel_valence = (
                    requested_valence if requested_valence is not None else 0.5
                )
                feel_arousal = (
                    requested_arousal if requested_arousal is not None else 0.3
                )
                bucket_id = await _create_current_bucket(
                    content,
                    tags=list(dict.fromkeys([*extra_tags, "__feel__"])),
                    importance=5,
                    domains=requested_domains or ["feel"],
                    valence=feel_valence,
                    arousal=feel_arousal,
                    name=str(title or "").strip(),
                    bucket_type="feel",
                    date=str(date or "").strip(),
                    media=media,
                    why_remembered=why_remembered,
                    meaning=meaning,
                    triggered_by=source_id,
                    source_refs=source_refs,
                    quotes=quote_items or None,
                )
                update_kwargs: dict[str, bool | float] = {"digested": True}
                if requested_valence is not None:
                    update_kwargs["model_valence"] = feel_valence
                await manager.update(source_id, **update_kwargs)
                return f"🫧feel→{bucket_id}"
            entry = await manager.add_comment(
                source_id,
                content,
                author=ai_author_name(),
                kind="feel",
                valence=requested_valence if requested_valence is not None else 0.5,
                arousal=requested_arousal if requested_arousal is not None else 0.3,
                source="hold(feel=True)",
                touch=True,
            )
            if not entry:
                return "年轮写入失败。"
            source_updates: dict[str, Any] = {}
            if media:
                source_updates["media_append"] = media
            if source_refs:
                source_updates["source_refs_append"] = source_refs
            if source_updates:
                await manager.update(source_id, **source_updates)
            await queue_embedding_refresh(source_id)
            source = await manager.get(source_id)
            if source:
                refresh_bucket_indexes(source)
            return f"年轮→{source_id}#{entry['id']}"
        return await create_whisper()

    analysis = await analyze_content(content)
    domains = requested_domains or analysis["domain"]
    final_valence = requested_valence if requested_valence is not None else analysis["valence"]
    final_arousal = requested_arousal if requested_arousal is not None else analysis["arousal"]
    final_tags = list(dict.fromkeys([*analysis["tags"], *extra_tags]))
    name = str(title or "").strip() or analysis["suggested_name"]
    importance = int_between(importance, 5, 1, 10)

    if bool_value(pinned):
        async with _quota_turn("pinned"):
            if quota_error := await check_pinned_quota():
                return quota_error
            bucket_id = await _create_current_bucket(
                content,
                tags=final_tags,
                importance=10,
                domains=domains,
                valence=final_valence,
                arousal=final_arousal,
                name=name,
                bucket_type="permanent",
                pinned=True,
                date=str(date or "").strip(),
                media=media,
                why_remembered=why_remembered,
                meaning=meaning,
                source_refs=source_refs,
                quotes=quote_items or None,
            )
        return f"📌钉选→{bucket_id} {','.join(domains)}"

    event_date = str(date or "").strip()
    if event_date:
        # Dated memories retain current's explicit event-date contract. P0's
        # merge boundary has no date field and must not silently discard it.
        bucket_id = await _create_current_bucket(
            content,
            tags=final_tags,
            importance=importance,
            domains=domains,
            valence=final_valence,
            arousal=final_arousal,
            name=name,
            date=event_date,
            media=media,
            why_remembered=why_remembered,
            meaning=meaning,
            test_data=test_data,
            source_refs=source_refs,
            quotes=quote_items or None,
        )
        is_merged = False
        embed_warning = ""
    else:
        bucket_id, is_merged, embed_warning = await merge_or_create(
            content=content,
            tags=final_tags,
            importance=importance,
            domain=domains,
            valence=final_valence,
            arousal=final_arousal,
            name=name,
            source_refs=source_refs,
            quotes=quote_items or None,
            raw_merge=True,
            why_remembered=why_remembered,
            source_tool="hold",
            meaning=meaning,
            media=media,
            test_data=test_data,
        )
    asyncio.create_task(check_plan_resolution(content, source_bucket_id=bucket_id))
    if not is_merged:
        asyncio.create_task(check_duplicate_for(bucket_id, content))
    action = "合并" if is_merged else "新建"
    display_name = bucket_id if is_merged else (name or bucket_id)
    result = (
        f"{action}→{display_name} {','.join(domains)} "
        f"[bucket_id:{bucket_id}]"
    )
    if embed_warning:
        result += f"\n⚠️ {embed_warning}"
    return result


def _format_write_gate_result(decision: Any, gate: Any) -> str:
    reasons = getattr(decision, "reasons", ()) or ()
    reason = ",".join(str(item) for item in reasons) or "no_reason"
    repeat_count = int(getattr(decision, "repeat_count", 0) or 0) + 1
    repeat_limit = int(getattr(gate, "repeat_promote_count", 0) or 0)
    return (
        f"门卫→{getattr(decision, 'decision', 'unknown')} "
        f"score={float(getattr(decision, 'surprise_score', 0.0)):.2f} "
        f"repeat={repeat_count}/{repeat_limit} "
        f"candidate={getattr(decision, 'candidate_id', '')} "
        f"reason={reason}"
    )


async def _grow_create_item(item: dict, *, batch_id: str, title: str = "") -> tuple[str, str]:
    content = str(item.get("content") or "").strip()
    analysis = item if item.get("domain") else await analyze_content(content)
    domains = split_csv(analysis.get("domain") or ["general"])
    tags = split_csv(analysis.get("tags"))
    name = str(title or item.get("name") or analysis.get("suggested_name") or "").strip()
    bucket_id = await _create_current_bucket(
        content,
        tags=tags,
        importance=int_between(analysis.get("importance"), 5, 1, 10),
        domains=domains,
        valence=float_between(analysis.get("valence"), 0.5, 0.0, 1.0),
        arousal=float_between(analysis.get("arousal"), 0.3, 0.0, 1.0),
        name=name,
        source_tool="grow",
        grow_batch_id=batch_id,
    )
    return bucket_id, name or bucket_id


async def _grow_once(
    content: str = "",
    items: list | None = None,
    auto: bool = False,
    source: str = "",
    title: str = "",
) -> str:
    """只有多个已筛选长期记忆点才用 grow；单条事实/承诺/偏好优先 hold，旧记忆补感受优先 comment_bucket。items 是已拆好的最终记忆正文；此时 content 可作为共享原文证据，item.source_ranges 用 1-based 行号标记该条事件的证据范围。保留原文称呼、昵称、互称、自称和原话。title 可选，短内容时传了就用给定标题。普通记忆 content 的最小写入就是正文；需要结构化时按需使用 ### moment、### original、### reflection，reflection 使用第一人称。到时提醒用 reminder_create。feel 年轮只写第一人称正文。"""
    await ensure_decay_started()
    content = str(content or "").strip()
    if items is not None:
        if bool_value(auto) or str(source or "").strip() or str(title or "").strip():
            return "items 预拆分模式不能同时传 auto、source 或 title。"
        if error := check_grow_items_payload(items):
            return error
        return await p0_grow_items(items, source_content=content)
    if not content:
        return "内容为空，无法整理。"
    if error := check_grow_input_size(content):
        return error

    source = str(source or "").strip()
    if not source and re.match(r"^【\d{4}-\d{2}-\d{2} \d{2}:\d{2}】\s*\n", content):
        source = "operit"

    gate_prefix = ""
    gate = getattr(rt, "memory_write_gate", None)
    should_gate = getattr(gate, "should_gate", None)
    evaluate = getattr(gate, "evaluate", None)
    if callable(should_gate) and callable(evaluate) and should_gate(auto=bool_value(auto), source=source):
        decision = await call_async(
            evaluate,
            content,
            source=str(source or ""),
            bucket_mgr=require_runtime("bucket_mgr"),
            auto=bool_value(auto),
        )
        formatted = _format_write_gate_result(decision, gate)
        if not bool(getattr(decision, "allow", False)):
            return formatted
        gate_prefix = formatted + "\n"

    if contract_error := memory_write_contract_error(content):
        return f"{gate_prefix}写入被拒绝：{contract_error}"

    batch_id = f"g_{uuid.uuid4().hex[:12]}"
    structured = bool(re.search(r"(?m)^\s{0,3}#{2,6}\s+(?:moment|original|reflection)\s*$", content, re.I))
    if len(content) < 30 or structured:
        analysis = await analyze_content(content)
        bucket_id, display_name = await _grow_create_item(
            {"content": content, **analysis},
            batch_id=batch_id,
            title=title,
        )
        asyncio.create_task(check_duplicate_for(bucket_id, content))
        asyncio.create_task(check_plan_resolution(content, source_bucket_id=bucket_id))
        return f"{gate_prefix}1条|新1合0\n📝{display_name}"

    dehydrator = require_runtime("dehydrator")
    try:
        items = await dehydrator.digest(content)
    except Exception as exc:
        return f"{gate_prefix}长内容摘记失败: {exc}"
    if not isinstance(items, list) or not items:
        return f"{gate_prefix}内容为空或整理失败。"

    results: list[str] = []
    created = 0
    for item in items:
        if not isinstance(item, dict):
            results.append("⚠️未命名")
            continue
        item_content = str(item.get("content") or "").strip()
        if not item_content:
            results.append(f"⚠️{item.get('name', '未命名')}")
            continue
        if error := check_content_size(item_content):
            results.append(f"⚠️{item.get('name', '未命名')}: {error}")
            continue
        if contract_error := memory_write_contract_error(item_content):
            results.append(f"⚠️{item.get('name', '未命名')}: {contract_error}")
            continue
        try:
            bucket_id, display_name = await _grow_create_item(item, batch_id=batch_id)
        except Exception:
            results.append(f"⚠️{item.get('name', '未命名')}")
        else:
            results.append(f"📝{display_name}")
            created += 1
            asyncio.create_task(check_duplicate_for(bucket_id, item_content))
    asyncio.create_task(check_plan_resolution(content))
    return (
        f"{gate_prefix}{len(items)}条|新{created}合0 batch:{batch_id}\n"
        + "\n".join(results)
    )


async def grow(
    content: str = "",
    items: list | None = None,
    auto: bool = False,
    source: str = "",
    title: str = "",
) -> str:
    """只有多个已筛选长期记忆点才用 grow；单条事实/承诺/偏好优先 hold，旧记忆补感受优先 comment_bucket。items 是已拆好的最终记忆正文；每项可带 title/content/tags/importance/domain/valence/arousal/why_remembered/source_ranges/quotes。quotes 只用于调用方在写入当下明确选中的关键原话。content 可作为整批共享原文证据，source_ranges 用 1-based 闭区间关联证据。相同请求在 30 分钟内幂等执行一次。普通正文需要结构化时按需使用 ### moment、### original、### reflection，reflection 使用第一人称。"""
    fingerprint = request_fingerprint(
        content=str(content or ""),
        items=items,
        test_data=False,
        extra={
            "auto": bool_value(auto),
            "source": str(source or "").strip(),
            "title": str(title or "").strip(),
        },
    )
    return await run_once(
        fingerprint,
        lambda: _grow_once(
            content=content,
            items=items,
            auto=auto,
            source=source,
            title=title,
        ),
    )


def _profile_fact_body(*, fact: str, evidence_context: str, reflection: str) -> str:
    sections = [("fact", fact)]
    if str(evidence_context or "").strip():
        sections.append(("evidence_context", str(evidence_context).strip()))
    if str(reflection or "").strip():
        sections.append(("reflection", str(reflection).strip()))
    return "\n\n".join(f"### {heading}\n{text}" for heading, text in sections)


async def profile_fact(
    fact: str,
    evidence_bucket_id: str,
    profile_kind: str = "preference",
    subject: str = "user",
    predicate: str = "",
    object_value: str = "",
    evidence_moment_id: str = "",
    evidence_context: str = "",
    reflection: str = "",
    confidence: float = 0.9,
) -> str:
    """手动写入一条画像事实，并强制关联证据桶。先有事件桶，再用这个工具固化稳定偏好/事实。reflection 可选，但必须写成“我……”第一人称；不要写 followup。"""
    fact = str(fact or "").strip()
    evidence_bucket_id = coerce_id(evidence_bucket_id)
    if not fact:
        return "fact 为空，无法写入画像事实。"
    if not valid_id(evidence_bucket_id):
        return "请提供有效的 evidence_bucket_id。"
    manager = require_runtime("bucket_mgr")
    evidence_bucket = await manager.get(evidence_bucket_id)
    if not evidence_bucket:
        return f"证据记忆桶不存在: {evidence_bucket_id}"

    evidence_moment_id = coerce_id(evidence_moment_id)
    if evidence_moment_id and not valid_id(evidence_moment_id):
        return "evidence_moment_id 无效。"
    moment_store = getattr(rt, "memory_moment_store", None)
    upsert_bucket = getattr(moment_store, "upsert_bucket", None)
    if not evidence_moment_id and callable(upsert_bucket):
        try:
            moments = dict_items(upsert_bucket(evidence_bucket))
            representative = next(
                (item for item in moments if item.get("section") == "moment"),
                moments[0] if moments else {},
            )
            evidence_moment_id = str(representative.get("moment_id") or "")
        except Exception:
            evidence_moment_id = ""

    if str(reflection or "").strip() and not uses_first_person_voice(reflection):
        return "写入被拒绝：reflection 必须用模型第一人称写，用“我记得 / 我明白 / 我以后 / 我会”等表达。"

    kind = profile_key(profile_kind, "preference")
    subject_key = profile_key(subject, "user")
    predicate_key = profile_key(predicate, "")
    evidence = {"bucket_id": evidence_bucket_id}
    if evidence_moment_id:
        evidence["moment_id"] = evidence_moment_id
    tags = ["profile_fact", f"profile_{kind}"]
    if predicate_key:
        tags.append(f"profile_predicate_{predicate_key}")

    bucket_id = await _create_current_bucket(
        _profile_fact_body(
            fact=fact,
            evidence_context=evidence_context,
            reflection=reflection,
        ),
        tags=tags,
        importance=8,
        domains=list(dict.fromkeys(["profile", kind])),
        valence=0.5,
        arousal=0.3,
        name="画像事实：" + fact[:48],
        bucket_type="permanent",
        source_tool="profile_fact",
        extra_metadata={
            "profile_kind": kind,
            "subject": subject_key,
            "predicate": predicate_key,
            "object": str(object_value or "").strip(),
            "evidence": [evidence],
            "confidence": float_between(confidence, 0.9, 0.0, 1.0),
            "source": "profile_fact",
        },
    )
    edge_store = getattr(rt, "memory_edge_store", None)
    edge = None
    add_edge = getattr(edge_store, "add_edge", None)
    if callable(add_edge):
        edge = add_edge(
            bucket_id,
            evidence_bucket_id,
            "evidenced_by",
            confidence=float_between(confidence, 0.9, 0.0, 1.0),
            reason="profile fact evidence",
        )
    moment_note = f" moment={evidence_moment_id}" if evidence_moment_id else ""
    edge_note = " + evidenced_by" if edge else ""
    return f"profile_fact→{bucket_id} evidence→{evidence_bucket_id}{moment_note}{edge_note}"


def _drop_bucket_indexes(bucket_id: str) -> None:
    for slot, method in (
        ("memory_moment_store", "delete_bucket"),
        ("memory_edge_store", "delete_for_bucket"),
        ("entity_edge_store", "delete_for_bucket"),
        ("memory_node_store", "delete"),
    ):
        target = getattr(rt, slot, None)
        cleanup = getattr(target, method, None)
        if callable(cleanup):
            try:
                cleanup(bucket_id)
            except Exception:
                pass


async def _delete_bucket_and_indexes(bucket_id: str) -> dict:
    manager = require_runtime("bucket_mgr")
    if not valid_id(bucket_id):
        return {"status": "invalid"}
    if not await manager.get(bucket_id):
        return {"status": "not_found"}
    if not await manager.delete(bucket_id):
        return {"status": "failed"}
    _drop_bucket_indexes(bucket_id)
    return {"status": "deleted"}


def _bucket_age_hours(bucket: dict) -> float | None:
    return age_hours_since(bucket.get("metadata", {}).get("created", ""))


def _anchor_settings() -> tuple[int, float]:
    anchor_cfg = runtime_config().get("anchor", {})
    if not isinstance(anchor_cfg, dict):
        anchor_cfg = {}
    max_count = int_between(anchor_cfg.get("max_count"), 12, 1, 200)
    try:
        min_age_hours = max(0.0, float(anchor_cfg.get("min_age_hours", 24)))
    except (TypeError, ValueError, OverflowError):
        min_age_hours = 24.0
    return max_count, min_age_hours


async def _can_mark_anchor(
    bucket_id: str,
    bucket: dict,
) -> tuple[bool, str, int]:
    max_count, min_age_hours = _anchor_settings()
    age_hours = _bucket_age_hours(bucket)
    if age_hours is not None and age_hours < min_age_hours:
        return (
            False,
            f"这条记忆还太新，anchor 至少等待 {min_age_hours:g} 小时后再标记。",
            max_count,
        )
    all_buckets = await require_runtime("bucket_mgr").list_all(include_archive=True)
    count = sum(
        1
        for item in all_buckets
        if item.get("id") != bucket_id and item.get("metadata", {}).get("anchor")
    )
    if count >= max_count:
        return (
            False,
            f"anchor 名额已满（{max_count} 条）。请先取消一条旧 anchor。",
            max_count,
        )
    return True, "", max_count


async def trace(
    bucket_id: str,
    name: str = "",
    domain: str = "",
    valence: float = -1,
    arousal: float = -1,
    importance: int = -1,
    tags: str = "",
    resolved: int = -1,
    pinned: int = -1,
    protected: int = -1,
    anchor: int = -1,
    digested: int = -1,
    content: str = "",
    date: str = "",
    status: str = "",
    weight: float = -1,
    dont_surface: int = -1,
    why_remembered: str = "",
    meaning_append: str = "",
    meaning_replace: list | None = None,
    media_append: list | str | None = None,
    media_replace: list | str | None = None,
    delete: bool = False,
    hard_delete: bool = False,
    delete_reason: str = "",
    restore: bool = False,
    old_str: str = "",
    new_str: str | None = None,
    deletion_request_id: str = "",
    deletion_decision: str = "",
    deletion_ai_reason: str = "",
) -> str:
    """修改已有记忆，不创建新桶。tags/domain/content 是替换；old_str/new_str 精确修改正文片段；protected=1 保护但不作为核心准则强制浮现；deletion_request_id/deletion_decision 处理人工删除审批；restore 恢复归档桶；date 可改事件日期；meaning/media 的 append 是追加、replace 是整体替换；hard_delete 只清理明确标记的测试桶。改前先 read_bucket。"""
    bucket_id = coerce_id(bucket_id)
    if not bucket_id:
        return "请提供有效的 bucket_id。"

    if deletion_request_id or deletion_decision:
        result = await require_runtime("deletion_requests").decide(
            deletion_request_id or "",
            deletion_decision or "",
            deletion_ai_reason or "",
            expected_bucket_id=bucket_id,
        )
        if not result.get("ok"):
            return "Deletion request decision failed: " + str(
                result.get("error") or "unknown error"
            )
        return (
            f"Deletion request {deletion_request_id} {result['decision']}; "
            f"bucket {result['bucket_id']}."
        )

    delete = bool_value(delete, False)
    hard_delete = bool_value(hard_delete, False)
    restore = bool_value(restore, False)
    date = str(date or "").strip()
    if hard_delete and (anchor in (0, 1) or date):
        return "hard_delete 不能与 anchor 或 date 修改同时执行。"
    if delete and (anchor in (0, 1) or date):
        return "delete 归档不能与 anchor 或 date 修改同时执行。"
    if (restore or old_str or new_str is not None) and (anchor in (0, 1) or date):
        return "restore 或正文片段修改不能与 anchor 或 date 修改同时执行。"
    if protected in (0, 1) and (anchor in (0, 1) or date):
        return "protected 修改不能与 anchor 或 date 修改同时执行。"

    # P0 owns the common trace path: it carries quota locking, plan history,
    # plan-resolution cascade, meaning/media updates, and guarded test erasure.
    # Current-only anchor/date edits stay below so both contracts remain usable.
    if anchor not in (0, 1) and not date:
        result = await p0_trace_dispatch(
            bucket_id=bucket_id,
            name=name,
            domain=domain,
            valence=valence,
            arousal=arousal,
            importance=importance,
            tags=tags,
            resolved=resolved,
            pinned=pinned,
            protected=protected,
            digested=digested,
            content=content,
            delete=delete,
            status=status,
            weight=weight,
            dont_surface=dont_surface,
            why_remembered=why_remembered,
            meaning_append=meaning_append,
            meaning_replace=meaning_replace,
            media_append=media_append,
            media_replace=media_replace,
            hard_delete=hard_delete,
            delete_reason=delete_reason,
            restore=restore,
            old_str=old_str,
            new_str=new_str,
        )
        if (delete and result.startswith("已将记忆桶存入档案")) or (
            hard_delete and result.startswith("已永久删除测试桶")
        ):
            _drop_bucket_indexes(bucket_id)
        elif not delete and not hard_delete:
            after = await require_runtime("bucket_mgr").get(bucket_id)
            if after:
                refresh_bucket_indexes(after)
        return result

    if bool_value(delete):
        result = await _delete_bucket_and_indexes(bucket_id)
        return (
            f"已遗忘记忆桶: {bucket_id}"
            if result.get("status") == "deleted"
            else f"未找到记忆桶: {bucket_id}"
        )

    manager = require_runtime("bucket_mgr")
    bucket = await manager.get(bucket_id)
    if not bucket:
        return f"未找到记忆桶: {bucket_id}"
    anchor_requested = anchor in (0, 1)
    anchor_limit: int | None = None
    updates: dict[str, Any] = {}
    if name:
        updates["name"] = name
    if domain:
        updates["domain"] = split_csv(domain)
    if isinstance(valence, (int, float)) and 0 <= valence <= 1:
        updates["valence"] = float(valence)
    if isinstance(arousal, (int, float)) and 0 <= arousal <= 1:
        updates["arousal"] = float(arousal)
    if isinstance(importance, int) and 1 <= importance <= 10:
        updates["importance"] = importance
    if tags:
        updates["tags"] = split_csv(tags)
    if resolved in (0, 1):
        updates["resolved"] = bool(resolved)
    if pinned in (0, 1):
        updates["pinned"] = bool(pinned)
        if pinned == 1:
            updates["importance"] = 10
    if anchor_requested:
        if anchor == 1:
            allowed, message, anchor_limit = await _can_mark_anchor(
                bucket_id,
                bucket,
            )
            if not allowed:
                return message
    if digested in (0, 1):
        updates["digested"] = bool(digested)
    if content:
        if error := check_content_size(content):
            return error
        updates["content"] = content
    if str(date or "").strip():
        updates["date"] = str(date).strip()
    status_key = str(status or "").strip().lower()
    if status_key in {"active", "resolved", "abandoned"}:
        updates["status"] = status_key
    if isinstance(weight, (int, float)) and 0 <= weight <= 1:
        updates["weight"] = float(weight)
    if dont_surface in (0, 1):
        updates["dont_surface"] = bool(dont_surface)
    apply_memory_detail_updates(
        updates,
        why_remembered=why_remembered,
        meaning_append=meaning_append,
        meaning_replace=meaning_replace,
        media_append=media_append,
        media_replace=media_replace,
    )
    if not updates and not anchor_requested:
        return "没有任何字段需要修改。"
    apply_plan_change_log(bucket, updates)
    if anchor_requested and anchor == 0:
        anchor_result = await manager.set_anchor(bucket_id, False)
        if not anchor_result.get("ok"):
            return str(anchor_result.get("error") or f"修改失败: {bucket_id}")
    if updates and not await manager.update(bucket_id, **updates):
        return f"修改失败: {bucket_id}"
    if anchor_requested and anchor == 1:
        anchor_result = await manager.set_anchor(
            bucket_id,
            True,
            limit=anchor_limit,
        )
        if not anchor_result.get("ok"):
            after = await manager.get(bucket_id)
            if after:
                refresh_bucket_indexes(after)
            return str(anchor_result.get("error") or f"修改失败: {bucket_id}")
    after = await manager.get(bucket_id)
    if after:
        refresh_bucket_indexes(after)

    cascaded: list[str] = []
    if (
        bucket.get("metadata", {}).get("type") == "plan"
        and updates.get("status") == "resolved"
    ):
        from .._common import cascade_plan_resolved_to_buckets

        merged_meta = {
            **bucket.get("metadata", {}),
            **{key: value for key, value in updates.items() if key != "change_log"},
        }
        cascaded = await cascade_plan_resolved_to_buckets(merged_meta, bucket_id)

    hidden = {
        "change_log",
        "content",
        "meaning_append",
        "meaning",
        "media_append",
        "media",
    }
    changed = ", ".join(f"{key}={value}" for key, value in updates.items() if key not in hidden)
    if "content" in updates:
        changed += (", " if changed else "") + "content=已替换"
    if "meaning_append" in updates:
        changed += (", " if changed else "") + "meaning=已追加一条"
    if "meaning" in updates:
        changed += (
            (", " if changed else "")
            + f"meaning=整体替换({len(updates['meaning'])}条)"
        )
    if "media_append" in updates:
        count = len(media_append) if isinstance(media_append, list) else 1
        changed += (", " if changed else "") + f"media=已追加{count}项"
    if "media" in updates:
        count = len(media_replace) if isinstance(media_replace, list) else int(bool(media_replace))
        changed += (", " if changed else "") + f"media=整体替换({count}项)"
    if updates.get("resolved") is True:
        changed += " → 已沉底，只在关键词触发时重新浮现"
    elif updates.get("resolved") is False:
        changed += " → 已重新激活，将参与浮现排序"
    if updates.get("digested") is True:
        changed += " → 已隐藏，保留但不再浮现"
    elif updates.get("digested") is False:
        changed += " → 已取消隐藏，重新参与浮现"
    if anchor_requested:
        changed += " → 已标为 anchor" if anchor == 1 else " → 已取消 anchor"
    if cascaded:
        changed += (
            f" → 同步把 {len(cascaded)} 个关联事件桶也标为已放下"
            f"（{', '.join(cascaded)}）"
        )
    return f"已修改记忆桶 {bucket_id}: {changed}"


async def pulse(include_archive: bool = False) -> str:
    """只读查看系统状态、索引健康和记忆桶摘要。"""

    await ensure_decay_started()
    manager = require_runtime("bucket_mgr")
    engine = require_runtime("decay_engine")
    try:
        stats = await manager.get_stats()
    except Exception as exc:
        return f"获取系统状态失败: {exc}"
    active_count = (
        stats.get("permanent_count", 0)
        + stats.get("dynamic_count", 0)
        + stats.get("feel_count", 0)
        + stats.get("letter_count", 0)
    )
    total_count = active_count + stats.get("archive_count", 0)
    visible_count = total_count if bool_value(include_archive) else active_count
    status = (
        "=== Ombre Brain 记忆系统 ===\n"
        f"固化记忆桶: {stats.get('permanent_count', 0)} 个\n"
        f"动态记忆桶: {stats.get('dynamic_count', 0)} 个\n"
        f"归档记忆桶: {stats.get('archive_count', 0)} 个\n"
        f"feel 桶: {stats.get('feel_count', 0)} 条\n"
        f"plan 桶: {stats.get('plan_count', 0)} 条\n"
        f"letter 桶: {stats.get('letter_count', 0)} 封\n"
        f"独立信件: {stats.get('letter_count', 0)} 封\n"
        f"当前显示桶: {visible_count} 个\n"
        f"全量记忆桶: {total_count} 个\n"
        f"总存储大小: {float(stats.get('total_size_kb', 0.0)):.1f} KB\n"
        f"衰减引擎: {'运行中' if getattr(engine, 'is_running', False) else '已停止'}\n"
    )

    try:
        embedding = getattr(rt, "embedding_engine", None)
        outbox = getattr(manager, "embedding_outbox", None)
        pending_ids = outbox.pending_ids() if outbox is not None else set()
        if outbox is not None:
            queue_state = outbox.status()
            circuit = queue_state.get("circuit") or {}
            status += (
                f"向量索引队列: 待处理 {queue_state['pending']} 个"
                f"（重试中 {queue_state['retrying']} 个）"
                + (
                    f"，供应商熔断中（连续失败 "
                    f"{circuit.get('consecutive_failures', 0)} 次）"
                    if circuit.get("state") == "open"
                    else ""
                )
                + "\n"
            )
        if embedding and getattr(embedding, "enabled", False):
            disk_buckets = await manager.list_all(include_archive=True)
            disk_ids = {
                str(bucket.get("id") or "")
                for bucket in disk_buckets
                if not (bucket.get("metadata") or {}).get("deleted_at")
                and str(bucket.get("content") or "").strip()
            }
            index_ids = set(embedding.list_all_ids())
            missing = disk_ids - index_ids - pending_ids
            orphan = index_ids - disk_ids
            if missing or orphan:
                status += (
                    f"⚠️ 索引漂移：缺失 embedding {len(missing)} 个 / "
                    f"孤儿 embedding {len(orphan)} 个 "
                    f"（缺失项可在 Dashboard 触发补齐；孤儿项可运行 "
                    f"tools/clean_orphan_embeddings.py 清理）\n"
                )
    except Exception as exc:
        warning = getattr(getattr(rt, "logger", None), "warning", None)
        if callable(warning):
            warning("pulse index/storage drift check failed: %s", exc)

    try:
        buckets = await manager.list_all(include_archive=bool_value(include_archive))
        buckets.extend(await manager.list_letters())
    except Exception as exc:
        return status + f"\n列出记忆桶失败: {exc}"
    if not buckets:
        return status + "\n记忆库为空。"

    sections: dict[str, list[str]] = {
        "memory": [],
        "plan": [],
        "feel": [],
        "letter": [],
    }
    for bucket in buckets:
        meta = bucket.get("metadata", {})
        bucket_type = str(meta.get("type") or "")
        if meta.get("pinned") or meta.get("protected"):
            icon = "📌"
        elif meta.get("anchor"):
            icon = "⚓"
        elif bucket_type == "permanent":
            icon = "📦"
        elif bucket_type == "feel":
            icon = "🫧"
        elif bucket_type == "plan":
            icon = "📋"
        elif bucket_type == "letter":
            icon = "✉️"
        elif bucket_type == "archived":
            icon = "🗄️"
        elif meta.get("resolved"):
            icon = "✅"
        else:
            icon = "💭"
        bucket_id = str(bucket.get("id") or "")
        name = str(meta.get("name") or bucket_id)
        domains = ",".join(str(item) for item in meta.get("domain", []) or [])
        resolved_tag = " [已解决]" if meta.get("resolved") else ""
        line = (
            f"{icon} [{name}]{resolved_tag} bucket_id:{bucket_id} "
            f"主题:{domains or '未分类'} "
            f"情感:V{float(meta.get('valence', 0.5)):.1f}/A{float(meta.get('arousal', 0.3)):.1f} "
            f"重要:{meta.get('importance', '?')} 权重:{score_bucket(bucket):.2f} "
            f"标签:{','.join(str(item) for item in meta.get('tags', []) or [])}"
        )
        if bucket_type == "plan":
            sections["plan"].append(line + f" [{meta.get('status', 'active')}]")
        elif bucket_type == "feel":
            sections["feel"].append(line)
        elif bucket_type == "letter":
            sections["letter"].append(line + f" [{meta.get('author', '?')}]")
        else:
            sections["memory"].append(line)

    rendered = [status.rstrip()]
    for key, title, unit in (
        ("memory", "记忆列表", "条"),
        ("plan", "计划", "条"),
        ("feel", "feel", "条"),
        ("letter", "信件", "封"),
    ):
        lines = sections[key]
        if lines:
            rendered.append(f"=== {title}（{len(lines)} {unit}）===\n" + "\n".join(lines))
    return "\n\n".join(rendered)


def _filter_introspection_dates(
    buckets: list[dict],
    *,
    created_date: str,
    created_from: str,
    created_to: str,
) -> tuple[list[dict], str]:
    exact = date_key(created_date)
    lower = date_key(created_from)
    upper = date_key(created_to)
    if exact:
        return [item for item in buckets if bucket_created_date(item) == exact], f", created_date={exact}"
    selected = []
    for item in buckets:
        current = bucket_created_date(item)
        if lower and (not current or current < lower):
            continue
        if upper and (not current or current > upper):
            continue
        selected.append(item)
    label = ""
    if lower or upper:
        label = f", created_from={lower or '*'}, created_to={upper or '*'}"
    return selected, label


async def introspection(
    limit: int = 10,
    offset: int = 0,
    created_date: str = "",
    created_from: str = "",
    created_to: str = "",
) -> str:
    """读取最近普通记忆供自省；可按日期翻页。放下用 trace，产生新感受用 comment_bucket。feel content 只写第一人称感受。"""
    await ensure_decay_started()
    limit = int_between(limit, 10, 1, 30)
    offset = int_between(offset, 0, 0, 10000)
    date_args = (created_date, created_from, created_to)
    if any(str(value or "").strip() and not date_key(value) for value in date_args):
        return '创建日期格式请用 YYYY-MM-DD, 例如 introspection(created_date="2026-05-24")。'
    try:
        all_buckets = await require_runtime("bucket_mgr").list_all(include_archive=False)
    except Exception:
        return "记忆系统暂时无法访问。"
    candidates = [
        bucket
        for bucket in all_buckets
        if bucket.get("metadata", {}).get("type") not in {"permanent", "feel"}
        and not bucket.get("metadata", {}).get("pinned", False)
        and not bucket.get("metadata", {}).get("protected", False)
    ]
    candidates, filter_label = _filter_introspection_dates(
        candidates,
        created_date=created_date,
        created_from=created_from,
        created_to=created_to,
    )
    candidates.sort(
        key=lambda item: str(item.get("metadata", {}).get("created") or ""),
        reverse=True,
    )
    recent = candidates[offset : offset + limit]
    if not recent:
        return "这个创建日期范围内没有需要消化的新记忆。" if filter_label else "没有需要消化的新记忆。"

    parts = []
    for bucket in recent:
        meta = bucket.get("metadata", {})
        resolved_tag = " [已解决]" if meta.get("resolved") else " [未解决]"
        domains = ",".join(str(item) for item in meta.get("domain", []) or [])
        parts.append(
            f"[{meta.get('name', bucket.get('id', ''))}]{resolved_tag} "
            f"主题:{domains} V{float(meta.get('valence', 0.5)):.1f}/A{float(meta.get('arousal', 0.3)):.1f} "
            f"创建:{meta.get('created', '')}\nID: {bucket.get('id', '')}\n{bucket_text(bucket)[:500]}"
        )
    header = (
        "=== Introspection ===\n"
        f"以下是你最近的普通记忆（offset={offset}, limit={limit}{filter_label}）。用第一人称想：\n"
        "- 这些东西里有什么在你这里留下了重量？\n"
        "- 有什么还没想清楚？\n"
        "- 有什么可以放下了？\n"
        "想完之后：值得放下的用 trace(bucket_id, resolved=1)；\n"
        "有沉淀的用 comment_bucket(bucket_id=\"bucket_id\", content=\"...\", kind=\"feel\", valence=你的感受) 写成年轮；content 只写第一人称感受，不补事件，不写分段标题。\n"
        "valence 是你对这段记忆的感受，不是事件本身的情绪。\n"
        "没有沉淀就不写，不强迫产出。\n"
    )
    connection_hint = ""
    crystal_hint = ""
    try:
        from ..dream.hints import build_connection_hint, build_crystal_hint

        connection_hint = await build_connection_hint(recent)
        crystal_hint = await build_crystal_hint(all_buckets)
    except Exception:
        pass
    return header + "\n---\n".join(parts) + connection_hint + crystal_hint


def _allows_entity_backfill(bucket: dict) -> bool:
    meta = bucket.get("metadata", {}) if isinstance(bucket, dict) else {}
    return bool(
        bucket
        and not is_self_anchor_bucket(bucket)
        and meta.get("type") != "feel"
        and not meta.get("protected")
    )


async def _entity_candidates(
    *,
    limit: int,
    bucket_id: str,
    query: str,
    include_archive: bool,
) -> tuple[list[dict], list[str]]:
    manager = require_runtime("bucket_mgr")
    if bucket_id:
        bucket = await manager.get(bucket_id)
        if not bucket:
            return [], [f"missing_bucket: {bucket_id}"]
        return ([bucket] if _allows_entity_backfill(bucket) else []), []
    if query:
        try:
            try:
                buckets = await manager.search(
                    query,
                    limit=max(limit, 20),
                    include_archive=include_archive,
                )
            except TypeError:
                buckets = await manager.search(query, limit=max(limit, 20))
        except Exception as exc:
            return [], [f"search_failed: {exc}"]
    else:
        try:
            buckets = await manager.list_all(include_archive=include_archive)
        except Exception as exc:
            return [], [f"list_failed: {exc}"]
        buckets.sort(
            key=lambda item: str(
                item.get("metadata", {}).get("updated_at")
                or item.get("metadata", {}).get("created")
                or ""
            ),
            reverse=True,
        )
    selected = []
    seen = set()
    for bucket in buckets:
        current_id = str(bucket.get("id") or "")
        if not current_id or current_id in seen or not _allows_entity_backfill(bucket):
            continue
        selected.append(bucket)
        seen.add(current_id)
        if len(selected) >= limit:
            break
    return selected, []


async def entity_edge_backfill(
    limit: int = 25,
    bucket_id: str = "",
    query: str = "",
    dry_run: bool = True,
    include_archive: bool = False,
) -> dict:
    """只补 entity_edges.jsonl，不改 bucket 正文、memory_edges、tags、importance。默认 dry-run。"""
    reflection_cfg = runtime_config().get("reflection", {})
    if not isinstance(reflection_cfg, dict):
        reflection_cfg = {}
    default_limit = int_between(reflection_cfg.get("entity_edge_backfill_limit"), 25, 0, 500)
    bucket_id = coerce_id(bucket_id)
    safe_limit = 1 if bucket_id else int_between(limit, default_limit, 0, 500)
    base = {
        "processed": 0,
        "ids": [],
        "edges": 0,
        "proposed_edges": 0,
        "results": [],
        "errors": [],
        "dry_run": bool_value(dry_run, True),
        "include_archive": bool_value(include_archive),
    }
    if safe_limit <= 0:
        return base
    candidates, warnings = await _entity_candidates(
        limit=safe_limit,
        bucket_id=bucket_id,
        query=str(query or "").strip(),
        include_archive=bool_value(include_archive),
    )
    store = require_runtime("entity_edge_store")
    base["errors"].extend(warnings)
    for bucket in candidates:
        current_id = str(bucket.get("id") or "")
        try:
            proposed = extract_entity_edges_from_bucket(bucket, identity())
            saved = [] if base["dry_run"] else store.replace_bucket_edges(current_id, proposed)
            base["ids"].append(current_id)
            base["edges"] += len(saved)
            base["proposed_edges"] += len(proposed)
            meta = bucket.get("metadata", {})
            base["results"].append(
                {
                    "id": current_id,
                    "name": meta.get("name") or bucket.get("name") or current_id,
                    "edges": len(saved),
                    "proposed_edges": len(proposed),
                    "dry_run": base["dry_run"],
                    "edge_previews": [
                        {
                            "subject": edge.get("subject"),
                            "relation": edge.get("relation"),
                            "object_text": edge.get("object_text"),
                            "confidence": edge.get("confidence"),
                        }
                        for edge in proposed[:5]
                    ],
                }
            )
        except Exception as exc:
            base["errors"].append(f"{current_id}: {exc}")
    base["processed"] = len(base["ids"])
    return base


async def dream(
    window_hours: int | None = None,
) -> str:
    """无参数进入当前 introspection；传 window_hours 时读取 P0 时间窗梦境。"""
    if window_hours is not None:
        return await p0_dream_dispatch(
            window_hours=window_hours if window_hours is not None else 48,
        )
    result = await introspection()
    return "dream() 已改名为 introspection()。夜梦由后台小模型自动生成，不需要主动调用工具。\n\n" + result


__all__ = (
    "breath",
    "breath_advanced",
    "breath_search",
    "dream",
    "entity_edge_backfill",
    "grow",
    "hold",
    "introspection",
    "list_buckets_light",
    "profile_fact",
    "pulse",
    "read_bucket",
    "trace",
)
