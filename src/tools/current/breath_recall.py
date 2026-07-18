"""Current-production surfacing, recall, rendering, and diffusion for breath."""

from __future__ import annotations

import random
import re
import hashlib
from dataclasses import replace
from typing import Any

from config_modes import normalize_direct_render_mode, normalize_retrieval_mode
from memory_diffusion import (
    diffuse_memory,
    diffusion_options_from_config,
    format_diffusion_trace,
    path_has_caution,
    seed_scores_for_buckets,
)
from memory_layers import (
    can_bucket_be_related_target,
    can_moment_be_direct_seed,
    can_moment_be_recall_context,
    is_source_record_bucket,
)
from memory_moments import parse_bucket_moments
from memory_relevance import (
    memory_relevance_options_from_config,
    recall_rank,
    recall_search_query,
    relevance_multiplier,
)
from recall_pipeline import (
    TASK_ONLY_MOMENT_SECTIONS,
    TEMPERATURE_MOMENT_SECTIONS,
    admit_moments,
    append_lexical_matches,
    append_word_map_matches,
    dehydration_metadata,
    group_moments_by_bucket,
    is_unpinned_anchor_candidate,
    moment_rerank_document,
    recallable_bucket as _recallable_bucket,
    recall_thresholds,
    safe_float as _safe_float,
    score_unit as _score_unit,
    seed_diagnostic,
    source_record_fragment_window,
    trim_to_token_budget,
)
from recall_policy import RecallPolicy
from self_anchor import SELF_ANCHOR_TAG, is_self_anchor_bucket
from utils import (
    bucket_content_for_recall,
    bucket_text_for_embedding,
    count_tokens_approx,
    strip_display_temperature_sections,
    strip_followup_sections,
    strip_temperature_meaning_lines,
    strip_wikilinks,
)

from .. import _runtime as rt
from ._helpers import (
    call_async,
    clip_text as _clip,
    dict_items,
    float_between,
    identity,
    int_between,
    log_warning as _warning,
    runtime_config,
)

__all__ = ["normalize_direct_render_mode", "normalize_retrieval_mode"]


def _config_section(name: str) -> dict:
    value = runtime_config().get(name, {})
    return value if isinstance(value, dict) else {}


_trim_tokens = trim_to_token_budget


def _relevance_options():
    return memory_relevance_options_from_config(runtime_config())


def _policy() -> RecallPolicy:
    injected = getattr(rt, "recall_policy", None)
    if injected is not None and hasattr(injected, "assess"):
        return injected
    cfg = _config_section("recall_thresholds")
    ai_name = str(identity().get("ai_name") or "").strip()
    return RecallPolicy(
        _relevance_options(),
        semantic_threshold=float_between(
            cfg.get("explicit_admission_semantic_score"),
            0.72,
            0.0,
            1.0,
        ),
        rerank_threshold=float_between(
            cfg.get("explicit_admission_rerank_score"),
            0.65,
            0.0,
            1.0,
        ),
        ai_reaction_names=[ai_name] if ai_name else [],
    )


def _rendered_content(bucket: dict) -> str:
    text = strip_wikilinks(str(bucket.get("content") or ""))
    text = strip_display_temperature_sections(text)
    text = strip_followup_sections(text)
    return strip_temperature_meaning_lines(text).strip()


def _moment_text(moment: dict, max_chars: int = 500) -> str:
    return _clip(strip_temperature_meaning_lines(str(moment.get("text") or "")), max_chars)


def _moment_title(moment: dict) -> str:
    meta = moment.get("metadata", {}) if isinstance(moment.get("metadata"), dict) else {}
    return str(meta.get("bucket_name") or moment.get("bucket_id") or "").strip()


def _bucket_date(bucket: dict, moment: dict) -> str:
    meta = bucket.get("metadata", {}) if isinstance(bucket.get("metadata"), dict) else {}
    moment_meta = moment.get("metadata", {}) if isinstance(moment.get("metadata"), dict) else {}
    value = meta.get("date") or moment_meta.get("bucket_date") or moment_meta.get("date")
    if value:
        return f"[date:{str(value)[:10]}]"
    value = meta.get("created") or moment_meta.get("bucket_created") or moment.get("created_at")
    return f"[created:{str(value)[:10]}]" if value else ""


def _direct_header(bucket: dict, moment: dict) -> str:
    bucket_id = str(bucket.get("id") or moment.get("bucket_id") or "")
    title = _moment_title(moment) or str((bucket.get("metadata") or {}).get("name") or bucket_id)
    return " ".join(
        part
        for part in (
            f"[bucket_id:{bucket_id}]",
            f"[moment_id:{moment.get('moment_id') or ''}]",
            _bucket_date(bucket, moment),
            str(moment.get("section") or "body"),
            title,
        )
        if part
    )


def _high_value(bucket: dict) -> bool:
    meta = bucket.get("metadata", {}) if isinstance(bucket.get("metadata"), dict) else {}
    return bool(
        meta.get("pinned")
        or meta.get("protected")
        or meta.get("anchor")
        or int_between(meta.get("importance"), 5, 1, 10) >= 9
    )


def _detail_query(query: str) -> bool:
    return any(
        phrase in str(query or "").lower()
        for phrase in (
            "细节",
            "原文",
            "完整",
            "整条",
            "整桶",
            "全部",
            "当时怎么说",
            "具体怎么说",
            "怎么写的",
            "旧记录",
        )
    )


def _window_around_moment(original: str, moment: dict, max_chars: int = 760) -> str:
    compact = " ".join(str(original or "").split())
    needle = " ".join(str(moment.get("text") or "").split())
    if not compact:
        return ""
    if not needle:
        return _clip(compact, max_chars)
    index = compact.find(needle)
    if index < 0:
        index = compact.find(needle[:80])
    if index < 0:
        return _clip(compact, max_chars)
    half = max_chars // 2
    start = max(0, index - half)
    end = min(len(compact), index + len(needle) + half)
    window = compact[start:end].strip()
    return ("…" if start else "") + window + ("…" if end < len(compact) else "")


_moments_by_bucket = group_moments_by_bucket


def _direct_moments_for_bucket(bucket: dict, query: str) -> list[dict]:
    explicit_lookup = _policy().plan_query(query).explicit_old_memory
    return [
        moment
        for moment in parse_bucket_moments(bucket, _relevance_options())
        if can_moment_be_recall_context(moment)
        and can_moment_be_direct_seed(moment, explicit_lookup=explicit_lookup)
        and str(moment.get("section") or "") not in TASK_ONLY_MOMENT_SECTIONS
    ]


_is_source_record_bucket = is_source_record_bucket


def _source_record_fragment(query: str, bucket: dict, max_chars: int = 360) -> str:
    original = _rendered_content(bucket)
    if not original:
        return ""
    fragment, clipped_start, clipped_end = source_record_fragment_window(
        original,
        _policy().specific_query_terms(query),
        max_chars,
    )
    return ("…" if clipped_start else "") + fragment + ("…" if clipped_end else "")


def _lookup_key(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


def _source_record_match_reason(query: str, bucket: dict) -> str:
    bucket_id = str(bucket.get("id") or "")
    if bucket_id and bucket_id in str(query or ""):
        return "explicit_bucket_id"
    meta = bucket.get("metadata", {}) if isinstance(bucket.get("metadata"), dict) else {}
    title_key = _lookup_key(meta.get("name") or bucket_id)
    query_key = _lookup_key(query)
    if title_key and (query_key == title_key or title_key in query_key):
        return "explicit_bucket_title"
    for term in _policy().specific_query_terms(query):
        term_key = _lookup_key(term)
        if term_key and (term_key == title_key or (len(term_key) >= 3 and term_key in title_key)):
            return "explicit_bucket_title"
    return ""


def _source_record_synthetic_moment(
    bucket: dict,
    query: str,
    *,
    selected_reason: str = "",
) -> dict | None:
    if not _is_source_record_bucket(bucket) or is_self_anchor_bucket(bucket):
        return None
    bucket_id = str(bucket.get("id") or "")
    if not bucket_id:
        return None
    fragment = _source_record_fragment(query, bucket)
    explicit_reason = _source_record_match_reason(query, bucket)
    if not fragment and not explicit_reason:
        return None
    fragment_seed = bool(fragment)
    reason = (
        "source_record_fragment_direct"
        if fragment_seed
        else "source_record_explicit_bucket_capsule"
    )
    meta = bucket.get("metadata", {}) if isinstance(bucket.get("metadata"), dict) else {}
    text = fragment or _clip(
        " | ".join(
            part
            for part in (
                str(meta.get("name") or bucket_id),
                str(meta.get("annotation_summary") or meta.get("summary") or ""),
            )
            if part
        ),
        260,
    )
    digest = hashlib.sha1(
        f"{bucket_id}\n{reason}\n{text}".encode(),
        usedforsecurity=False,
    ).hexdigest()[:12]
    return {
        "moment_id": f"{bucket_id}:source-record:{digest}",
        "bucket_id": bucket_id,
        "section": "source_fragment" if fragment_seed else "source_capsule",
        "text": text,
        "ordinal": 0,
        "source": "source_record_synthetic",
        "source_id": reason,
        "score": max(1.0, _score_unit(bucket.get("score", 0.0))),
        "metadata": {
            "bucket_name": meta.get("name") or bucket_id,
            "bucket_type": meta.get("type") or "source",
            "bucket_tags": list(meta.get("tags") or []),
            "bucket_domain": list(meta.get("domain") or []),
            "bucket_importance": meta.get("importance"),
            "bucket_created": meta.get("created"),
            "source_record_direct": True,
            "source_record_direct_reason": reason,
            "source_record_match_reason": explicit_reason or selected_reason or "selected_bucket",
            "source_record_fragment_seed": fragment_seed,
            "source_record_capsule_only": not fragment_seed,
        },
        "_source_record_synthetic": True,
    }


def _source_record_synthetics(matches: list[dict], query: str) -> list[dict]:
    output = []
    seen = set()
    for bucket in matches:
        bucket_id = str(bucket.get("id") or "")
        if not bucket_id or bucket_id in seen:
            continue
        moment = _source_record_synthetic_moment(
            bucket,
            query,
            selected_reason="seed_bucket",
        )
        if moment:
            output.append(moment)
            seen.add(bucket_id)
    return output


def _prepend_source_records(moments: list[dict], synthetics: list[dict]) -> list[dict]:
    if not synthetics:
        return moments
    source_ids = {str(moment.get("bucket_id") or "") for moment in synthetics}
    return list(synthetics) + [
        moment
        for moment in moments
        if str(moment.get("bucket_id") or "") not in source_ids
    ]


def _is_source_record_synthetic(moment: dict | None) -> bool:
    if not isinstance(moment, dict):
        return False
    meta = moment.get("metadata", {}) if isinstance(moment.get("metadata"), dict) else {}
    return bool(meta.get("source_record_direct") or moment.get("_source_record_synthetic"))


def _representative_moment(moments: list[dict]) -> dict | None:
    for section in (
        "original",
        "moment",
        "fact",
        "body",
        "evidence_context",
        "context",
        "reflection",
        "feeling",
        "comment",
    ):
        for moment in moments:
            if moment.get("section") == section:
                return moment
    return moments[0] if moments else None


def _context_moments(seed: dict, grouped: dict[str, list[dict]]) -> list[dict]:
    bucket_moments = grouped.get(str(seed.get("bucket_id") or ""), [])
    seed_ordinal = int(seed.get("ordinal") or 0)
    output = []
    for moment in bucket_moments:
        if moment.get("moment_id") == seed.get("moment_id"):
            continue
        ordinal = int(moment.get("ordinal") or 0)
        if abs(ordinal - seed_ordinal) == 1 or moment.get("section") in TEMPERATURE_MOMENT_SECTIONS:
            output.append(moment)
    return output[:4]


async def _format_direct_bucket(
    bucket: dict,
    moment: dict,
    grouped: dict[str, list[dict]],
    token_budget: int,
    *,
    query: str,
    render_mode: str,
) -> str:
    if token_budget <= 0:
        return ""
    original = _rendered_content(bucket)
    header = _direct_header(bucket, moment)
    if _is_source_record_synthetic(moment):
        dehydrate = getattr(getattr(rt, "dehydrator", None), "dehydrate_direct_capsule", None)
        matched = _moment_text(moment, 260)
        capsule = matched
        if callable(dehydrate):
            try:
                capsule = str(
                    await call_async(
                        dehydrate,
                        original or matched,
                        dehydration_metadata(bucket),
                    )
                    or matched
                )
            except Exception as exc:
                _warning("Source-record capsule failed: %s", exc)
        meta = moment.get("metadata", {}) if isinstance(moment.get("metadata"), dict) else {}
        matched_label = (
            "matched_fragment" if meta.get("source_record_fragment_seed") else "matched_source_record"
        )
        return _trim_tokens(
            f"{header} bucket_capsule\n{capsule}\n{matched_label}: {matched}",
            token_budget,
        )
    original_block = f"{header} bucket_original\n{original}".strip()
    if count_tokens_approx(original_block) <= token_budget:
        return original_block

    wants_capsule = render_mode == "full" or (
        render_mode == "auto" and (_high_value(bucket) or _detail_query(query))
    )
    if wants_capsule:
        dehydrate = getattr(getattr(rt, "dehydrator", None), "dehydrate_direct_capsule", None)
        if callable(dehydrate):
            try:
                capsule = str(
                    await call_async(dehydrate, original, dehydration_metadata(bucket))
                    or ""
                )
                block = (
                    f"{header} bucket_capsule\n{capsule}\n"
                    f"matched_moment: {_moment_text(moment, 220)}"
                )
                return _trim_tokens(block, token_budget)
            except Exception as exc:
                _warning("Direct bucket capsule failed: %s", exc)

    window = _window_around_moment(original, moment)
    parts = [
        f"{header} bucket_window",
        f"matched_moment: {_moment_text(moment, 320)}",
    ]
    if window:
        parts.append("original_window:\n" + window)
    contexts = [
        f"- [{item.get('section') or 'moment'}] [moment_id:{item.get('moment_id') or ''}] "
        f"{_moment_text(item, 120)}"
        for item in _context_moments(moment, grouped)
        if item.get("section") in TEMPERATURE_MOMENT_SECTIONS
    ][:2]
    if contexts:
        parts.append("语境:\n" + "\n".join(contexts))
    return _trim_tokens("\n".join(parts), token_budget)


def _format_secondary_moment(moment: dict) -> str:
    return (
        f"- [bucket_id:{moment.get('bucket_id') or ''}] "
        f"[moment_id:{moment.get('moment_id') or ''}] "
        f"[{moment.get('section') or 'moment'}] {_moment_title(moment)}: "
        f"{_moment_text(moment, 180)}"
    )


async def _dehydrate_summary(bucket: dict, max_chars: int = 180) -> str:
    dehydrate = getattr(getattr(rt, "dehydrator", None), "dehydrate", None)
    if callable(dehydrate):
        try:
            clean_meta = dehydration_metadata(bucket)
            summary = await call_async(dehydrate, bucket_text_for_embedding(bucket), clean_meta)
            return _clip(summary, max_chars)
        except Exception as exc:
            _warning("Breath dehydration failed: %s", exc)
    return _clip(bucket_content_for_recall(bucket), max_chars)


def _node_callbacks(query: str, buckets: list[dict]):
    store = getattr(rt, "memory_node_store", None)
    if store is None or not bool(_config_section("memory_nodes").get("enabled", False)):
        return None, None
    try:
        store.bulk_upsert(buckets)
        query_facets = store.facets_for_text(query)
    except Exception as exc:
        _warning("Memory node refresh failed: %s", exc)
        return None, None

    def salience(bucket_id: str, bucket: dict | None = None) -> float:
        return float(store.node_salience(bucket_id, fallback_bucket=bucket))

    def resonance(bucket_id: str, bucket: dict | None = None) -> float:
        return float(store.node_resonance(bucket_id, query_facets, fallback_bucket=bucket))

    return salience, resonance


async def _diffused_bucket_blocks(
    source_buckets: list[dict],
    all_buckets: list[dict],
    *,
    token_budget: int,
    related_per_memory: int,
    edge_min_confidence: float,
    query: str,
    exclude_bucket_ids: set[str] | None = None,
) -> str:
    if token_budget <= 0 or related_per_memory <= 0 or not source_buckets:
        return ""
    edge_store = getattr(rt, "memory_edge_store", None)
    list_edges = getattr(edge_store, "list_edges", None)
    if not callable(list_edges):
        return ""
    source_buckets = [bucket for bucket in source_buckets if _recallable_bucket(bucket)]
    source_ids = {str(bucket.get("id")) for bucket in source_buckets if bucket.get("id")}
    if not source_ids:
        return ""
    bucket_map = {
        str(bucket["id"]): bucket
        for bucket in all_buckets
        if _recallable_bucket(bucket)
    }
    edges = [
        edge
        for edge in dict_items(list_edges())
        if (_safe_float(edge.get("confidence"), 0.0) or 0.0) >= edge_min_confidence
    ]
    if not edges:
        return ""
    top_k = max(1, len(source_ids) * related_per_memory)
    options = replace(diffusion_options_from_config(runtime_config()), top_k=top_k)
    salience, resonance = _node_callbacks(query, list(bucket_map.values()))
    hits = diffuse_memory(
        seed_scores_for_buckets(source_buckets),
        edges,
        bucket_map,
        options=options,
        exclude_ids=source_ids | set(exclude_bucket_ids or set()),
        node_salience=salience,
        node_resonance=resonance,
        query_text=query,
    )
    query_plan = _policy().plan_query(query)
    output = []
    remaining = token_budget
    for hit in hits:
        target_id = str(hit.bucket_id or "")
        target = bucket_map.get(target_id)
        if not target or not can_bucket_be_related_target(
            target,
            explicit_lookup=query_plan.allow_archive_targets,
        ):
            continue
        if query_plan.enforce_topic_evidence and not _policy().bucket_has_topic_evidence(query, target):
            continue
        summary = await _dehydrate_summary(target)
        path = format_diffusion_trace(hit.best_path, bucket_map, use_labels=True)
        note = (
            "路径含冲突/阻断，仅作边界背景。"
            if path_has_caution(hit.best_path)
            else "背景联想，不代表当前事实。"
        )
        block = f"- [bucket_id:{target_id}] 路径: {path}；摘要: {summary}（{note}）"
        block_tokens = count_tokens_approx(block)
        if block_tokens > remaining:
            break
        output.append(block)
        remaining -= block_tokens
    return "\n---\n".join(output)


def _select_anchors(all_buckets: list[dict], limit: int = 2) -> list[dict]:
    anchors = []
    for bucket in all_buckets:
        if not is_unpinned_anchor_candidate(bucket):
            continue
        anchors.append(bucket)
    anchors.sort(
        key=lambda bucket: (
            int_between((bucket.get("metadata") or {}).get("importance"), 5, 1, 10),
            _safe_float(
                getattr(rt.decay_engine, "calculate_score")(
                    bucket.get("metadata", {})
                ),
                0.0,
            ),
            str((bucket.get("metadata") or {}).get("updated_at") or ""),
        ),
        reverse=True,
    )
    return anchors[: max(0, limit)]


async def _dream_overlay(
    *,
    query: str,
    valence: float,
    arousal: float,
    is_session_start: bool,
    auto_surface: bool,
) -> str:
    if auto_surface:
        return ""
    engine = getattr(rt, "dream_engine", None)
    surface = getattr(engine, "surface_for_breath", None)
    if not callable(surface):
        return ""
    try:
        result = await call_async(
            surface,
            query=query,
            valence=valence,
            arousal=arousal,
            is_session_start=is_session_start,
            embedding_engine=getattr(rt, "embedding_engine", None),
        )
    except Exception as exc:
        _warning("Dream surfacing failed: %s", exc)
        return ""
    return str(result or "").strip()


async def surface_breath(
    *,
    max_tokens: int,
    max_results: int,
    include_related: bool,
    related_per_memory: int,
    edge_min_confidence: float,
    include_core: bool,
    core_limit: int,
    valence: float,
    arousal: float,
    is_session_start: bool,
    auto_surface: bool,
) -> str:
    """Surface weighted current memories, protected core, anchors, and dreams."""
    if auto_surface:
        return "没有找到可靠命中。"
    try:
        all_buckets = await rt.bucket_mgr.list_all(include_archive=False)
    except Exception as exc:
        _warning("Breath surfacing list failed: %s", exc)
        return "记忆系统暂时无法访问。"

    core_candidates = [
        bucket
        for bucket in all_buckets
        if not is_self_anchor_bucket(bucket)
        and (
            (bucket.get("metadata") or {}).get("pinned")
            or (bucket.get("metadata") or {}).get("protected")
        )
    ]
    protected = [
        bucket for bucket in core_candidates if (bucket.get("metadata") or {}).get("protected")
    ]
    pinned = [
        bucket
        for bucket in core_candidates
        if (bucket.get("metadata") or {}).get("pinned")
        and not (bucket.get("metadata") or {}).get("protected")
    ]
    protected.sort(
        key=lambda bucket: rt.decay_engine.calculate_score(bucket.get("metadata", {})),
        reverse=True,
    )
    pinned.sort(
        key=lambda bucket: (
            int_between((bucket.get("metadata") or {}).get("importance"), 5, 1, 10),
            rt.decay_engine.calculate_score(bucket.get("metadata", {})),
            str((bucket.get("metadata") or {}).get("updated_at") or ""),
        ),
        reverse=True,
    )
    selected_core = (protected + pinned)[:core_limit] if include_core else []
    selected_anchors = _select_anchors(all_buckets, limit=min(2, max_results))

    unresolved = [
        bucket
        for bucket in all_buckets
        if not is_self_anchor_bucket(bucket)
        and not (bucket.get("metadata") or {}).get("resolved", False)
        and (bucket.get("metadata") or {}).get("type") not in {"permanent", "feel"}
        and not (bucket.get("metadata") or {}).get("anchor")
        and not (bucket.get("metadata") or {}).get("pinned")
        and not (bucket.get("metadata") or {}).get("protected")
    ]
    unresolved.sort(
        key=lambda bucket: rt.decay_engine.calculate_score(bucket.get("metadata", {})),
        reverse=True,
    )
    if len(unresolved) > 1:
        pool = unresolved[1 : min(20, len(unresolved))]
        random.shuffle(pool)
        unresolved = unresolved[:1] + pool + unresolved[min(20, len(unresolved)) :]
    candidates = unresolved[:max_results]

    token_budget = max_tokens
    core_results = []
    core_budget = min(token_budget, max(0, int(max_tokens * 0.25)))
    for bucket in selected_core:
        entry = f"📌 [核心准则] [bucket_id:{bucket.get('id', '')}] {await _dehydrate_summary(bucket, 360)}"
        tokens = count_tokens_approx(entry)
        if tokens > core_budget or tokens > token_budget:
            break
        core_results.append(entry)
        core_budget -= tokens
        token_budget -= tokens

    anchor_results = []
    surfaced_sources = []
    anchor_budget = min(token_budget, max(0, int(max_tokens * 0.18)))
    for bucket in selected_anchors:
        entry = f"⚓ [长期锚点] [bucket_id:{bucket.get('id', '')}] {await _dehydrate_summary(bucket, 280)}"
        tokens = count_tokens_approx(entry)
        if tokens > anchor_budget or tokens > token_budget:
            break
        anchor_results.append(entry)
        surfaced_sources.append(bucket)
        anchor_budget -= tokens
        token_budget -= tokens

    dynamic_results = []
    for bucket in candidates:
        if token_budget <= 0:
            break
        score = rt.decay_engine.calculate_score(bucket.get("metadata", {}))
        entry = (
            f"[权重:{score:.2f}] [bucket_id:{bucket.get('id', '')}] "
            f"{await _dehydrate_summary(bucket, 420)}"
        )
        tokens = count_tokens_approx(entry)
        if tokens > token_budget:
            break
        dynamic_results.append(entry)
        surfaced_sources.append(bucket)
        token_budget -= tokens

    related_block = ""
    if include_related and surfaced_sources and related_per_memory > 0:
        related_block = await _diffused_bucket_blocks(
            surfaced_sources,
            all_buckets,
            token_budget=max(0, token_budget - count_tokens_approx("=== 联想浮现 ===\n")),
            related_per_memory=related_per_memory,
            edge_min_confidence=edge_min_confidence,
            query="",
        )

    parts = []
    if core_results:
        parts.append("=== 核心准则 ===\n" + "\n---\n".join(core_results))
    if anchor_results:
        parts.append("=== 长期锚点 ===\n" + "\n---\n".join(anchor_results))
    if dynamic_results:
        parts.append("=== 浮现记忆 ===\n" + "\n---\n".join(dynamic_results))
    if related_block:
        parts.append("=== 联想浮现 ===\n" + related_block)
    dream = await _dream_overlay(
        query="",
        valence=valence,
        arousal=arousal,
        is_session_start=is_session_start,
        auto_surface=False,
    )
    if dream:
        parts.append(dream)
    return "\n\n".join(parts) if parts else "权重池平静，没有需要处理的记忆。"


def _self_anchor_candidates(all_buckets: list[dict]) -> list[dict]:
    anchors = []
    for bucket in all_buckets:
        if not is_self_anchor_bucket(bucket):
            continue
        meta = bucket.get("metadata", {}) if isinstance(bucket.get("metadata"), dict) else {}
        if meta.get("active") is False or meta.get("deprecated") or meta.get("resolved"):
            continue
        anchors.append(bucket)
    anchors.sort(
        key=lambda bucket: (
            int_between((bucket.get("metadata") or {}).get("importance"), 5, 1, 10),
            str((bucket.get("metadata") or {}).get("updated_at") or ""),
        ),
        reverse=True,
    )
    entry_id = str(_config_section("self_anchor").get("entry_bucket_id") or "").strip()
    if entry_id:
        anchors.sort(key=lambda bucket: str(bucket.get("id") or "") != entry_id)
    return anchors


async def read_self_anchor_breath(
    *,
    query: str = "",
    max_tokens: int = 1000,
    limit: int = 3,
    domain_entry: bool = False,
) -> str:
    try:
        all_buckets = await rt.bucket_mgr.list_all(include_archive=False)
    except Exception as exc:
        _warning("Self-anchor read failed: %s", exc)
        return "自我入口暂时无法访问。"
    anchors = _self_anchor_candidates(all_buckets)
    if not anchors:
        return "还没有自我 anchor。"
    query_key = str(query or "").strip().lower()
    if domain_entry and not query_key:
        return "=== 自我入口 ===\n" + _clip(_rendered_content(anchors[0]), 520)
    rows = []
    remaining = max_tokens
    for bucket in anchors:
        meta = bucket.get("metadata", {}) if isinstance(bucket.get("metadata"), dict) else {}
        haystack = " ".join(
            [
                str(bucket.get("id") or ""),
                str(meta.get("name") or ""),
                " ".join(str(tag) for tag in meta.get("tags", []) or []),
                str(bucket.get("content") or ""),
            ]
        ).lower()
        if query_key and query_key not in haystack:
            continue
        row = (
            f"[bucket_id:{bucket.get('id', '')}] {meta.get('name') or SELF_ANCHOR_TAG}\n"
            f"{_rendered_content(bucket)}"
        )
        tokens = count_tokens_approx(row)
        if rows and tokens > remaining:
            break
        rows.append(_trim_tokens(row, remaining))
        remaining -= min(tokens, remaining)
        if len(rows) >= limit or remaining <= 0:
            break
    title = "自我分段" if domain_entry and query_key else "自我"
    empty = "没有找到相关自我分段。" if domain_entry and query_key else "还没有可读的自我 anchor。"
    return f"=== {title} ===\n" + ("\n---\n".join(rows) if rows else empty)


def _word_map_hint_enabled() -> bool:
    store = getattr(rt, "word_map_store", None)
    return bool(
        _config_section("gateway").get("word_map_hint_enabled", False)
        and store is not None
        and getattr(store, "enabled", False)
    )


async def _collect_search_materials(
    *,
    query: str,
    domain: str,
    valence: float,
    arousal: float,
    max_results: int,
) -> tuple[str, list[dict], list[dict], dict[str, dict], dict[str, Any], list[str], dict[str, float]]:
    search_query = recall_search_query(query, _relevance_options())
    domains = [item.strip() for item in str(domain or "").split(",") if item.strip()]
    query_valence = valence if 0 <= valence <= 1 else None
    query_arousal = arousal if 0 <= arousal <= 1 else None
    search_kwargs: dict[str, Any] = {
        "limit": max(max_results, 20),
        "domain_filter": domains,
    }
    if query_valence is not None and query_arousal is not None:
        search_kwargs["query_valence"] = query_valence
        search_kwargs["query_arousal"] = query_arousal
    matches = await rt.bucket_mgr.search(search_query, **search_kwargs)
    matches = [dict(bucket) for bucket in matches if _recallable_bucket(bucket)]
    diagnostics: dict[str, dict] = {}
    for bucket in matches:
        seed_diagnostic(
            diagnostics,
            bucket,
            "keyword",
            bucket_score=bucket.get("score"),
        )

    specific_terms = _policy().specific_query_terms(query)
    thresholds = recall_thresholds(
        query,
        max_results,
        config=_config_section("recall_thresholds"),
        options=_relevance_options(),
        specific_terms=specific_terms,
    )
    matched_ids = {str(bucket.get("id") or "") for bucket in matches}
    embedding = getattr(rt, "embedding_engine", None)
    vector_search = getattr(embedding, "search_similar", None)
    if callable(vector_search):
        try:
            vector_results = await call_async(
                vector_search,
                search_query,
                top_k=int(thresholds["semantic_top_k"]),
            )
            for bucket_id, score in vector_results:
                bucket_id = str(bucket_id)
                if bucket_id in diagnostics:
                    diagnostics[bucket_id]["embedding_score"] = round(float(score), 4)
                if bucket_id in matched_ids or float(score) < thresholds["vector_min_score"]:
                    continue
                bucket = await rt.bucket_mgr.get(bucket_id)
                if not bucket or not _recallable_bucket(bucket):
                    continue
                candidate = dict(bucket)
                candidate["score"] = round(float(score) * 100, 2)
                candidate["vector_match"] = True
                matches.append(candidate)
                matched_ids.add(bucket_id)
                seed_diagnostic(
                    diagnostics,
                    candidate,
                    "vector",
                    embedding_score=score,
                )
        except Exception as exc:
            _warning("Vector search failed, using keyword only: %s", exc)

    explicit_lookup = _policy().plan_query(query).explicit_old_memory
    try:
        all_buckets = await rt.bucket_mgr.list_all(include_archive=explicit_lookup)
    except Exception as exc:
        _warning("Moment recall list failed: %s", exc)
        all_buckets = list(matches)
    all_buckets = [bucket for bucket in all_buckets if isinstance(bucket, dict)]
    lexical_terms = append_lexical_matches(
        query,
        matches,
        all_buckets,
        diagnostics,
        specific_terms=specific_terms,
        recallable=_recallable_bucket,
    )
    word_map_scores = append_word_map_matches(
        matches,
        all_buckets,
        diagnostics,
        terms=specific_terms,
        store=getattr(rt, "word_map_store", None),
        gateway_config=_config_section("gateway"),
        recallable=_recallable_bucket,
        warning=_warning,
    )
    return (
        search_query,
        matches,
        all_buckets,
        diagnostics,
        thresholds,
        lexical_terms,
        word_map_scores,
    )


async def _rerank(query: str, candidates: list[dict]) -> list[dict]:
    engine = getattr(rt, "reranker_engine", None)
    if not candidates or engine is None or not getattr(engine, "enabled", False):
        return candidates
    limit = min(
        len(candidates),
        max(1, int(getattr(engine, "candidate_limit", 20) or 20)),
    )
    head, tail = candidates[:limit], candidates[limit:]
    try:
        results = await engine.rerank(
            query,
            [moment_rerank_document(moment) for moment in head],
            top_n=len(head),
        )
    except Exception as exc:
        _warning("Breath reranker failed; keeping original candidates: %s", exc)
        return candidates
    if not results:
        return candidates
    by_index = {}
    for result in results:
        if isinstance(result, dict):
            index, score = result.get("index"), result.get("score")
        else:
            index, score = getattr(result, "index", None), getattr(result, "score", None)
        if index is not None and score is not None:
            by_index[int(index)] = float(score)
    weight = max(0.0, min(1.0, float(getattr(engine, "score_weight", 0.65))))
    reranked = []
    for index, moment in enumerate(head):
        item = dict(moment)
        base_score = _safe_float(item.get("score"), 0.0) or 0.0
        score = by_index.get(index)
        if score is None:
            item["rerank_score"] = None
            item["combined_score"] = base_score
        else:
            item["rerank_score"] = round(score, 4)
            item["combined_score"] = round(base_score * (1.0 - weight) + score * weight, 4)
            item["score"] = item["combined_score"]
        reranked.append(item)
    reranked.sort(
        key=lambda item: (
            recall_rank(query, item, _relevance_options())[0],
            item.get("rerank_score") is None,
            -(_safe_float(item.get("combined_score"), 0.0) or 0.0),
        )
    )
    return reranked + tail


def _apply_relevance_gate(query: str, candidates: list[dict]) -> list[dict]:
    output = []
    options = _relevance_options()
    adjusted = False
    for moment in candidates:
        multiplier = relevance_multiplier(query, moment, options)
        if multiplier <= 0:
            adjusted = True
            continue
        item = dict(moment)
        old_score = _safe_float(item.get("score"), 0.0) or 0.0
        item["score"] = round(old_score * float(multiplier), 4)
        adjusted = adjusted or item["score"] != old_score
        output.append(item)
    if adjusted:
        output.sort(key=lambda item: recall_rank(query, item, options))
    return output


def _source_record_admission_override(
    moment: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    if not _is_source_record_synthetic(moment):
        return None
    metadata = (
        moment.get("metadata", {})
        if isinstance(moment.get("metadata"), dict)
        else {}
    )
    return (
        str(metadata.get("source_record_direct_reason") or "source_record_direct"),
        {
            "source_record_direct_override": True,
            "fragment_seed": bool(metadata.get("source_record_fragment_seed")),
        },
    )


def _admit_moments(
    query: str,
    candidates: list[dict],
    seed_diagnostics: dict[str, dict],
    *,
    auto_surface: bool,
) -> tuple[list[dict], list[dict]]:
    return admit_moments(
        query,
        candidates,
        seed_diagnostics,
        policy=_policy(),
        auto=auto_surface,
        direct_override=_source_record_admission_override,
    )

def _write_diagnostics(
    *,
    query: str,
    mode: str,
    thresholds: dict[str, Any],
    seed_diagnostics: dict[str, dict],
    candidates: list[dict],
    suppressed: list[dict],
    returned: list[dict],
    displayed_moment_ids: list[str],
    secondary_moment_ids: list[str],
    related_source_bucket_ids: list[str],
    related_included: bool,
    dream_included: bool,
    response_sections: list[str],
) -> None:
    diagnostics = getattr(rt, "recall_diagnostics", None)
    write = getattr(diagnostics, "write", None)
    if not callable(write) or not getattr(diagnostics, "enabled", False):
        return
    max_candidates = max(1, int(getattr(diagnostics, "max_candidates", 20) or 20))
    returned_ids = [str(moment.get("moment_id") or "") for moment in returned]
    write(
        {
            "source": "breath",
            "mode": mode,
            "query": str(query or ""),
            "recall_thresholds": thresholds,
            "seed_buckets": list(seed_diagnostics.values())[:max_candidates],
            "candidates": [
                {
                    "bucket_id": str(moment.get("bucket_id") or ""),
                    "bucket_name": _moment_title(moment),
                    "moment_id": str(moment.get("moment_id") or ""),
                    "section": moment.get("section"),
                    "score": _safe_float(moment.get("score"), 0.0),
                    "rerank_score": _safe_float(moment.get("rerank_score")),
                    "admission_reason": moment.get("_admission_reason"),
                    "text_preview": _moment_text(moment, 220),
                }
                for moment in candidates[:max_candidates]
            ],
            "suppressed_candidates": [
                {
                    "bucket_id": str(moment.get("bucket_id") or ""),
                    "moment_id": str(moment.get("moment_id") or ""),
                    "admission_reason": moment.get("_admission_reason"),
                    "score": _safe_float(moment.get("score"), 0.0),
                    "rerank_score": _safe_float(moment.get("rerank_score")),
                    "text_preview": _moment_text(moment, 220),
                }
                for moment in suppressed[:max_candidates]
            ],
            "final": {
                "returned_moment_ids": returned_ids,
                "direct_moment_ids": displayed_moment_ids,
                "secondary_moment_ids": secondary_moment_ids,
                "related_source_bucket_ids": related_source_bucket_ids,
                "related_included": related_included,
                "drift_included": False,
                "dream_included": dream_included,
                "response_sections": response_sections,
            },
        }
    )


def _suppressed_debug_block(suppressed: list[dict], *, bucket_mode: bool = False) -> str:
    rows = [
        f"- [bucket_id:{moment.get('bucket_id') or ''}] "
        f"[moment_id:{moment.get('moment_id') or ''}] "
        f"reason={moment.get('_admission_reason') or 'suppressed'} "
        f"rerank={moment.get('rerank_score')} score={moment.get('score')}"
        for moment in suppressed[:10]
    ]
    title = "suppressed_bucket_candidates" if bucket_mode else "suppressed_candidates"
    return f"=== {title} ===\n" + "\n".join(rows)


async def _bucket_search_mode(
    *,
    query: str,
    matches: list[dict],
    seed_diagnostics: dict[str, dict],
    thresholds: dict[str, Any],
    lexical_terms: list[str],
    word_map_scores: dict[str, float],
    max_tokens: int,
    max_results: int,
    render_mode: str,
    valence: float,
    arousal: float,
    is_session_start: bool,
    auto_surface: bool,
    debug: bool,
) -> str:
    direct_results = []
    returned = []
    suppressed = []
    displayed_ids = []
    token_used = 0
    seen = set()
    policy = _policy()
    for bucket in matches:
        if len(direct_results) >= max_results or token_used >= max_tokens:
            break
        bucket_id = str(bucket.get("id") or "")
        if not bucket_id or bucket_id in seen:
            continue
        moments = _direct_moments_for_bucket(bucket, query)
        moment = _representative_moment(moments)
        if moment is None:
            moment = _source_record_synthetic_moment(
                bucket,
                query,
                selected_reason="seed_bucket",
            )
        if moment is None:
            continue
        seed = seed_diagnostics.get(bucket_id, {})
        sources = set(seed.get("sources") or [])
        has_topic = policy.bucket_has_topic_evidence(query, bucket)
        word_map_only = bool(sources) and not (sources - {"word_map"})
        if _is_source_record_synthetic(moment):
            decision = None
        else:
            decision = policy.assess(
                query,
                moment,
                has_topic_evidence=has_topic,
                semantic_score=seed.get("embedding_score"),
                high_confidence_edge="lexical" in sources,
                auto=auto_surface,
            )
        if word_map_only and not has_topic:
            item = dict(moment)
            item["_admission_reason"] = "word_map_topic_evidence_missing"
            suppressed.append(item)
            continue
        if decision is not None and not decision.admit_direct:
            item = dict(moment)
            item["_admission_reason"] = decision.reason
            suppressed.append(item)
            continue
        grouped = {bucket_id: moments}
        entry = await _format_direct_bucket(
            bucket,
            moment,
            grouped,
            max_tokens - token_used,
            query=query,
            render_mode=render_mode,
        )
        if not entry:
            break
        tokens = count_tokens_approx(entry)
        if token_used + tokens > max_tokens:
            break
        try:
            await rt.bucket_mgr.touch(bucket_id)
        except Exception as exc:
            _warning("Breath touch failed for %s: %s", bucket_id, exc)
        direct_results.append(entry)
        returned.append(moment)
        displayed_ids.append(str(moment.get("moment_id") or ""))
        token_used += tokens
        seen.add(bucket_id)

    dream = await _dream_overlay(
        query=query,
        valence=valence,
        arousal=arousal,
        is_session_start=is_session_start,
        auto_surface=auto_surface,
    )
    response_parts = []
    sections = []
    if direct_results:
        response_parts.append("=== 直接命中记忆 ===\n" + "\n---\n".join(direct_results))
        sections.append("direct")
    if debug and suppressed:
        response_parts.append(_suppressed_debug_block(suppressed, bucket_mode=True))
    if dream:
        sections.append("dream")
    _write_diagnostics(
        query=query,
        mode="search",
        thresholds={
            **thresholds,
            "retrieval_mode": "bucket",
            "lexical_terms": lexical_terms,
            "word_map_hint_enabled": _word_map_hint_enabled(),
            "word_map_hint_bucket_ids": sorted(word_map_scores),
        },
        seed_diagnostics=seed_diagnostics,
        candidates=returned,
        suppressed=suppressed,
        returned=returned,
        displayed_moment_ids=displayed_ids,
        secondary_moment_ids=[],
        related_source_bucket_ids=[],
        related_included=False,
        dream_included=bool(dream),
        response_sections=sections,
    )
    if not response_parts:
        return dream or "没有找到可靠命中。"
    response = "\n\n".join(response_parts)
    return response + ("\n\n" + dream if dream else "")


def _refresh_moments(all_buckets: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    store = getattr(rt, "memory_moment_store", None)
    if store is None:
        return [], {}
    recallable = [bucket for bucket in all_buckets if _recallable_bucket(bucket)]
    bucket_ids = {str(bucket.get("id") or "") for bucket in recallable}
    store.bulk_upsert(recallable)
    moments = [
        moment
        for moment in store.list_all()
        if str(moment.get("bucket_id") or "") in bucket_ids
        and can_moment_be_recall_context(moment)
        and str(moment.get("section") or "") not in TASK_ONLY_MOMENT_SECTIONS
    ]
    return moments, _moments_by_bucket(moments)


async def _graph_search_mode(
    *,
    query: str,
    search_query: str,
    matches: list[dict],
    all_buckets: list[dict],
    seed_diagnostics: dict[str, dict],
    thresholds: dict[str, Any],
    lexical_terms: list[str],
    word_map_scores: dict[str, float],
    max_tokens: int,
    max_results: int,
    include_related: bool,
    related_per_memory: int,
    edge_min_confidence: float,
    render_mode: str,
    valence: float,
    arousal: float,
    is_session_start: bool,
    auto_surface: bool,
    debug: bool,
) -> str:
    store = getattr(rt, "memory_moment_store", None)
    if store is None:
        return await _bucket_search_mode(
            query=query,
            matches=matches,
            seed_diagnostics=seed_diagnostics,
            thresholds=thresholds,
            lexical_terms=lexical_terms,
            word_map_scores=word_map_scores,
            max_tokens=max_tokens,
            max_results=max_results,
            render_mode=render_mode,
            valence=valence,
            arousal=arousal,
            is_session_start=is_session_start,
            auto_surface=auto_surface,
            debug=debug,
        )
    bucket_map = {
        str(bucket["id"]): bucket
        for bucket in all_buckets
        if _recallable_bucket(bucket)
    }
    try:
        moments, grouped = _refresh_moments(all_buckets)
        boosts = seed_scores_for_buckets(matches)
        boost_weight = float_between(
            _config_section("gateway").get("word_map_hint_moment_boost"),
            0.25,
            0.0,
            1.0,
        )
        for bucket_id, score in word_map_scores.items():
            boosts[bucket_id] = max(boosts.get(bucket_id, 0.0), score * boost_weight)
        candidates = store.search_moment_items(
            search_query,
            moments,
            limit=max(max_results, 20),
            bucket_boosts=boosts,
            exclude_sections=TASK_ONLY_MOMENT_SECTIONS,
        )
    except Exception as exc:
        _warning("Moment graph search failed; using bucket retrieval: %s", exc)
        return await _bucket_search_mode(
            query=query,
            matches=matches,
            seed_diagnostics=seed_diagnostics,
            thresholds=thresholds,
            lexical_terms=lexical_terms,
            word_map_scores=word_map_scores,
            max_tokens=max_tokens,
            max_results=max_results,
            render_mode=render_mode,
            valence=valence,
            arousal=arousal,
            is_session_start=is_session_start,
            auto_surface=auto_surface,
            debug=debug,
        )

    explicit_lookup = _policy().plan_query(query).explicit_old_memory
    candidates = [
        moment
        for moment in candidates
        if can_moment_be_direct_seed(moment, explicit_lookup=explicit_lookup)
    ]
    source_record_moments = _source_record_synthetics(matches, query)
    if not candidates and not source_record_moments:
        for bucket in matches:
            candidates.extend(_direct_moments_for_bucket(bucket, query)[:1])
    candidates = _prepend_source_records(candidates, source_record_moments)
    gated = _prepend_source_records(
        _apply_relevance_gate(
            query,
            [moment for moment in candidates if not _is_source_record_synthetic(moment)],
        ),
        source_record_moments,
    )
    reranked = await _rerank(query, gated)
    reranked = _prepend_source_records(reranked, source_record_moments)
    admitted, suppressed = _admit_moments(
        query,
        reranked,
        seed_diagnostics,
        auto_surface=auto_surface,
    )
    returned = admitted[:max_results]

    direct_results = []
    displayed_bucket_ids = set()
    displayed_moment_ids = []
    token_used = 0
    direct_limit = 1 if include_related else max_results
    for moment in returned:
        if len(direct_results) >= direct_limit or token_used >= max_tokens:
            break
        bucket_id = str(moment.get("bucket_id") or "")
        if not bucket_id or bucket_id in displayed_bucket_ids:
            continue
        bucket = bucket_map.get(bucket_id)
        if not bucket:
            continue
        entry = await _format_direct_bucket(
            bucket,
            moment,
            grouped,
            max_tokens - token_used,
            query=query,
            render_mode=render_mode,
        )
        if not entry:
            break
        tokens = count_tokens_approx(entry)
        if token_used + tokens > max_tokens:
            break
        try:
            await rt.bucket_mgr.touch(bucket_id)
        except Exception as exc:
            _warning("Breath touch failed for %s: %s", bucket_id, exc)
        direct_results.append(entry)
        displayed_bucket_ids.add(bucket_id)
        displayed_moment_ids.append(str(moment.get("moment_id") or ""))
        token_used += tokens

    related_entry = ""
    secondary_ids = []
    related_source_ids = []
    if include_related and related_per_memory > 0 and returned:
        remaining = max_tokens - token_used - count_tokens_approx("=== 联想浮现 ===\n")
        related_parts = []
        query_plan = _policy().plan_query(query)
        secondary_limit = query_plan.secondary_direct_limit(related_per_memory)
        secondary_bucket_ids = set()
        for moment in returned:
            bucket_id = str(moment.get("bucket_id") or "")
            if (
                remaining <= 0
                or len(secondary_ids) >= secondary_limit
                or bucket_id in displayed_bucket_ids
                or bucket_id in secondary_bucket_ids
            ):
                continue
            if query_plan.secondary_direct_requires_topic_evidence and not _policy().moment_has_topic_evidence(
                query,
                moment,
            ):
                continue
            block = _format_secondary_moment(moment)
            tokens = count_tokens_approx(block)
            if tokens > remaining:
                break
            related_parts.append(block)
            secondary_ids.append(str(moment.get("moment_id") or ""))
            secondary_bucket_ids.add(bucket_id)
            remaining -= tokens

        source_buckets = []
        seen_sources = set()
        for moment in returned:
            bucket_id = str(moment.get("bucket_id") or "")
            bucket = bucket_map.get(bucket_id)
            if bucket and not _is_source_record_bucket(bucket) and bucket_id not in seen_sources:
                source_buckets.append(bucket)
                related_source_ids.append(bucket_id)
                seen_sources.add(bucket_id)
        diffused = await _diffused_bucket_blocks(
            source_buckets,
            all_buckets,
            token_budget=max(0, remaining),
            related_per_memory=related_per_memory,
            edge_min_confidence=edge_min_confidence,
            query=query,
            exclude_bucket_ids=secondary_bucket_ids,
        )
        if diffused:
            related_parts.append(diffused)
        if related_parts:
            related_entry = "=== 联想浮现 ===\n" + "\n---\n".join(related_parts)

    dream = await _dream_overlay(
        query=query,
        valence=valence,
        arousal=arousal,
        is_session_start=is_session_start,
        auto_surface=auto_surface,
    )
    response_parts = []
    sections = []
    if direct_results:
        response_parts.append("=== 直接命中记忆 ===\n" + "\n---\n".join(direct_results))
        sections.append("direct")
    if related_entry:
        response_parts.append(related_entry)
        sections.append("related")
    if debug and suppressed:
        response_parts.append(_suppressed_debug_block(suppressed))
    if dream:
        sections.append("dream")
    _write_diagnostics(
        query=query,
        mode="search",
        thresholds={
            **thresholds,
            "retrieval_mode": "graph",
            "lexical_terms": lexical_terms,
            "word_map_hint_enabled": _word_map_hint_enabled(),
            "word_map_hint_bucket_ids": sorted(word_map_scores),
        },
        seed_diagnostics=seed_diagnostics,
        candidates=reranked,
        suppressed=suppressed,
        returned=returned,
        displayed_moment_ids=displayed_moment_ids,
        secondary_moment_ids=secondary_ids,
        related_source_bucket_ids=related_source_ids,
        related_included=bool(related_entry),
        dream_included=bool(dream),
        response_sections=sections,
    )
    if not response_parts:
        return dream or (
            "没有找到可靠命中。"
            if thresholds.get("has_explicit_entity") and suppressed
            else "未找到相关记忆。"
        )
    response = "\n\n".join(response_parts)
    return response + ("\n\n" + dream if dream else "")


async def search_breath(
    *,
    query: str,
    max_tokens: int,
    domain: str,
    valence: float,
    arousal: float,
    max_results: int,
    include_related: bool,
    related_per_memory: int,
    edge_min_confidence: float,
    is_session_start: bool,
    debug: bool,
    auto_surface: bool,
    direct_render_mode: str,
    retrieval_mode: str,
) -> str:
    """Run current-main's keyword/vector seeds through bucket or moment recall."""
    if auto_surface and _policy().is_auto_query_too_vague(query):
        return "没有找到可靠命中。"
    try:
        (
            search_query,
            matches,
            all_buckets,
            seed_diagnostics,
            thresholds,
            lexical_terms,
            word_map_scores,
        ) = await _collect_search_materials(
            query=query,
            domain=domain,
            valence=valence,
            arousal=arousal,
            max_results=max_results,
        )
    except Exception as exc:
        _warning("Breath search failed: %s", exc)
        return "检索过程出错，请稍后重试。"

    if retrieval_mode == "bucket":
        return await _bucket_search_mode(
            query=query,
            matches=matches,
            seed_diagnostics=seed_diagnostics,
            thresholds=thresholds,
            lexical_terms=lexical_terms,
            word_map_scores=word_map_scores,
            max_tokens=max_tokens,
            max_results=max_results,
            render_mode=direct_render_mode,
            valence=valence,
            arousal=arousal,
            is_session_start=is_session_start,
            auto_surface=auto_surface,
            debug=debug,
        )
    return await _graph_search_mode(
        query=query,
        search_query=search_query,
        matches=matches,
        all_buckets=all_buckets,
        seed_diagnostics=seed_diagnostics,
        thresholds=thresholds,
        lexical_terms=lexical_terms,
        word_map_scores=word_map_scores,
        max_tokens=max_tokens,
        max_results=max_results,
        include_related=include_related,
        related_per_memory=related_per_memory,
        edge_min_confidence=edge_min_confidence,
        render_mode=direct_render_mode,
        valence=valence,
        arousal=arousal,
        is_session_start=is_session_start,
        auto_surface=auto_surface,
        debug=debug,
    )
