"""Shared recall candidate policy for MCP and Dashboard inspection paths."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from memory_relevance import (
    MemoryRelevanceOptions,
    active_facets,
    facets_for_text,
    query_has_explicit_entity_marker,
)
from runtime_values import (
    float_between as _float_between,
    metadata_dict as _metadata,
    numeric_int_between as _int_between,
)
from self_anchor import is_self_anchor_bucket
from utils import bucket_content_for_recall, count_tokens_approx


TASK_ONLY_MOMENT_SECTIONS = frozenset({"followup", "followup_log"})
TEMPERATURE_MOMENT_SECTIONS = frozenset(
    {"affect_anchor", "favorite_reason", "comment"}
)


def dehydration_metadata(bucket: Mapping[str, Any]) -> dict[str, Any]:
    metadata = bucket.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    return {key: value for key, value in metadata.items() if key not in {"tags", "comments"}}


def is_daily_impression(bucket: Mapping[str, Any]) -> bool:
    metadata = _metadata(bucket)
    tags = {str(tag).lower() for tag in metadata.get("tags", []) or []}
    return metadata.get("type") == "feel" and bool(
        {"relationship_weather", "daily_impression", "weekly_impression"} & tags
    )


def recallable_bucket(bucket: Mapping[str, Any]) -> bool:
    return bool(bucket.get("id")) and not is_self_anchor_bucket(bucket) and not is_daily_impression(bucket)


def is_unpinned_anchor_candidate(bucket: Mapping[str, Any]) -> bool:
    metadata = _metadata(bucket)
    return bool(
        metadata.get("anchor")
        and not is_self_anchor_bucket(bucket)
        and not metadata.get("pinned")
        and not metadata.get("protected")
        and metadata.get("type") not in {"permanent", "feel"}
    )


def apply_topic_evidence_gate(
    gate: dict[str, Any],
    *,
    source_key: str,
    injection_key: str,
    would_inject_key: str,
    topic_required: bool,
    topic_present: bool,
) -> dict[str, Any]:
    source = gate.get(source_key)
    source = source if isinstance(source, Mapping) else {}
    allowed = bool(source.get("allowed"))
    reason = str(source.get("reason") or "")
    if allowed and topic_required and not topic_present:
        allowed = False
        reason = "query_topic_evidence_missing"
    gate["topic_evidence"] = {
        "required": topic_required,
        "present": topic_present if topic_required else None,
    }
    gate[injection_key] = {"allowed": allowed, "reason": reason}
    gate[would_inject_key] = allowed
    return gate


def source_record_fragment_window(
    content: str,
    terms: Iterable[str],
    max_chars: int = 360,
) -> tuple[str, bool, bool]:
    """Select the earliest source-record window and report clipped sides."""

    original = str(content or "")
    if not original:
        return "", False, False
    lowered = original.lower()
    matches: list[tuple[int, str]] = []
    for term in terms:
        needle = str(term or "").strip().lower()
        if len(needle) < 2:
            continue
        index = lowered.find(needle)
        if index >= 0:
            matches.append((index, needle))
    if not matches:
        return "", False, False
    index, needle = min(matches, key=lambda item: (item[0], -len(item[1])))
    half = max_chars // 2
    start = max(0, index - half)
    end = min(len(original), index + len(needle) + half)
    return original[start:end].strip(), start > 0, end < len(original)


def trim_to_token_budget(value: str, token_budget: int) -> str:
    text = str(value or "").strip()
    if token_budget <= 0 or not text:
        return ""
    if count_tokens_approx(text) <= token_budget:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[:middle].rstrip() + "…"
        if count_tokens_approx(candidate) <= token_budget:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + ("…" if low else "")


def group_moments_by_bucket(moments: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for source in moments:
        moment = dict(source)
        bucket_id = str(moment.get("bucket_id") or "")
        if bucket_id:
            grouped.setdefault(bucket_id, []).append(moment)
    for bucket_moments in grouped.values():
        bucket_moments.sort(key=lambda item: int(item.get("ordinal") or 0))
    return grouped


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def score_unit(value: Any) -> float:
    score = safe_float(value, 0.0) or 0.0
    if score > 1.0:
        score /= 100.0
    return max(0.0, min(1.0, score))


def recall_thresholds(
    query: str,
    max_results: int,
    *,
    config: Mapping[str, Any],
    options: MemoryRelevanceOptions,
    specific_terms: Iterable[str],
) -> dict[str, Any]:
    query_facets = active_facets(facets_for_text(query, options))
    explicit = query_has_explicit_entity_marker(query)
    vague = not any(str(term).strip() for term in specific_terms)
    profile = (
        "explicit"
        if explicit
        else "vague"
        if vague
        else "facet"
        if query_facets
        else "default"
    )
    if explicit:
        vector_min = _float_between(
            config.get("explicit_vector_min_score"), 0.55, 0.0, 1.0
        )
    elif vague:
        vector_min = _float_between(
            config.get("vague_vector_min_score"), 0.40, 0.0, 1.0
        )
    elif query_facets:
        vector_min = _float_between(
            config.get("facet_vector_min_score"), 0.45, 0.0, 1.0
        )
    else:
        vector_min = _float_between(
            config.get("vector_min_score"), 0.50, 0.0, 1.0
        )
    top_k = max(max_results, 20)
    if vague:
        top_k = max(
            top_k,
            _int_between(config.get("vague_top_k"), 50, 20, 100),
        )
    return {
        "profile": profile,
        "vector_min_score": vector_min,
        "semantic_top_k": top_k,
        "query_facets": sorted(query_facets),
        "has_explicit_entity": explicit,
        "is_vague": vague,
    }


def seed_diagnostic(
    diagnostics: dict[str, dict[str, Any]],
    bucket: Mapping[str, Any],
    source: str,
    *,
    bucket_score: Any = None,
    embedding_score: Any = None,
) -> None:
    bucket_id = str(bucket.get("id") or "")
    if not bucket_id:
        return
    metadata = _metadata(bucket)
    item = diagnostics.setdefault(
        bucket_id,
        {
            "bucket_id": bucket_id,
            "bucket_name": metadata.get("name") or bucket_id,
            "sources": [],
        },
    )
    sources = item.setdefault("sources", [])
    if source not in sources:
        sources.append(source)
    if bucket_score is not None:
        item["bucket_search_score"] = safe_float(bucket_score, 0.0)
        item["keyword_score"] = round(score_unit(bucket_score), 4)
    if embedding_score is not None:
        item["embedding_score"] = round(
            safe_float(embedding_score, 0.0) or 0.0,
            4,
        )


def _bucket_haystack(bucket: Mapping[str, Any]) -> str:
    metadata = _metadata(bucket)
    return " ".join(
        [
            str(metadata.get("name") or bucket.get("id") or ""),
            " ".join(str(tag) for tag in metadata.get("tags", []) or []),
            " ".join(str(domain) for domain in metadata.get("domain", []) or []),
            bucket_content_for_recall(dict(bucket)),
        ]
    ).lower()


def append_lexical_matches(
    query: str,
    matches: list[dict[str, Any]],
    all_buckets: list[dict[str, Any]],
    diagnostics: dict[str, dict[str, Any]],
    *,
    specific_terms: Iterable[str],
    recallable: Callable[[dict[str, Any]], bool],
) -> list[str]:
    terms = [str(term).strip() for term in specific_terms if str(term).strip()]
    if not terms:
        terms = re.findall(
            r"[A-Za-z][A-Za-z0-9_.:-]{1,}|[\u4e00-\u9fff]{2,}",
            query,
        )
    terms = list(dict.fromkeys(terms))[:5]
    matched_ids = {str(bucket.get("id") or "") for bucket in matches}
    for bucket in all_buckets:
        bucket_id = str(bucket.get("id") or "")
        if not bucket_id or bucket_id in matched_ids or not recallable(bucket):
            continue
        hits = [term for term in terms if term.lower() in _bucket_haystack(bucket)]
        if not hits:
            continue
        candidate = dict(bucket)
        candidate["score"] = round(min(100.0, 70.0 + len(hits) * 8.0), 2)
        matches.append(candidate)
        matched_ids.add(bucket_id)
        seed_diagnostic(
            diagnostics,
            candidate,
            "lexical",
            bucket_score=candidate["score"],
        )
    return terms


def append_word_map_matches(
    matches: list[dict[str, Any]],
    all_buckets: list[dict[str, Any]],
    diagnostics: dict[str, dict[str, Any]],
    *,
    terms: Iterable[str],
    store: Any,
    gateway_config: Mapping[str, Any],
    recallable: Callable[[dict[str, Any]], bool],
    warning: Callable[..., Any] | None = None,
) -> dict[str, float]:
    enabled = bool(gateway_config.get("word_map_hint_enabled", False))
    if not enabled or store is None or not getattr(store, "enabled", False):
        return {}
    try:
        hints = store.hint_buckets_for_terms(
            list(terms),
            neighbor_limit=_int_between(
                gateway_config.get("word_map_hint_neighbor_limit"),
                6,
                0,
                40,
            ),
            bucket_limit=_int_between(
                gateway_config.get("word_map_hint_bucket_limit"),
                12,
                1,
                100,
            ),
        )
    except Exception as exc:
        if callable(warning):
            warning("Word-map hint lookup failed: %s", exc)
        return {}
    hints = hints if isinstance(hints, Mapping) else {}
    raw_scores = hints.get("bucket_scores")
    raw_scores = raw_scores if isinstance(raw_scores, Mapping) else {}
    scores = {
        str(bucket_id): float(score)
        for bucket_id, score in raw_scores.items()
    }
    bucket_map = {str(bucket.get("id") or ""): bucket for bucket in all_buckets}
    matched_ids = {str(bucket.get("id") or "") for bucket in matches}
    raw_evidence = hints.get("evidence")
    evidence = raw_evidence if isinstance(raw_evidence, Mapping) else {}
    for bucket_id, score in scores.items():
        bucket = bucket_map.get(bucket_id)
        if not bucket or not recallable(bucket):
            continue
        if bucket_id not in matched_ids:
            candidate = dict(bucket)
            candidate["score"] = round(score * 100, 2)
            matches.append(candidate)
            matched_ids.add(bucket_id)
        seed_diagnostic(
            diagnostics,
            bucket,
            "word_map",
            bucket_score=score,
        )
        diagnostics[bucket_id]["word_map_score"] = round(score, 4)
        raw_bucket_evidence = evidence.get(bucket_id)
        bucket_evidence = (
            raw_bucket_evidence
            if isinstance(raw_bucket_evidence, Mapping)
            else {}
        )
        diagnostics[bucket_id]["word_map_terms"] = list(
            bucket_evidence.get("direct_terms") or []
        )
        diagnostics[bucket_id]["word_map_neighbor_terms"] = list(
            bucket_evidence.get("neighbor_terms") or []
        )
    return scores


def moment_rerank_document(moment: Mapping[str, Any]) -> str:
    metadata = _metadata(moment)
    return "\n".join(
        [
            f"title: {metadata.get('bucket_name') or moment.get('bucket_id') or ''}",
            f"section: {moment.get('section') or ''}",
            f"domain: {' '.join(str(item) for item in metadata.get('bucket_domain', []) or [])}",
            f"tags: {' '.join(str(item) for item in metadata.get('bucket_tags', []) or [])}",
            f"summary: {metadata.get('annotation_summary') or metadata.get('summary') or ''}",
            f"text: {moment.get('text') or ''}",
        ]
    )[:4000]


def admit_moments(
    query: str,
    candidates: list[dict[str, Any]],
    seed_diagnostics: Mapping[str, Mapping[str, Any]],
    *,
    policy: Any,
    query_plan: Any = None,
    auto: bool = False,
    direct_override: Callable[[dict[str, Any]], tuple[str, dict[str, Any]] | None]
    | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    admitted: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for moment in candidates:
        override = direct_override(moment) if callable(direct_override) else None
        if override is not None:
            reason, debug = override
            item = dict(moment)
            item["_admission_reason"] = reason
            item["_admission_debug"] = dict(debug)
            admitted.append(item)
            continue

        bucket_id = str(moment.get("bucket_id") or "")
        seed = seed_diagnostics.get(bucket_id, {})
        sources = set(seed.get("sources") or [])
        has_topic = policy.moment_has_topic_evidence(query, moment)
        word_map_only = bool(sources) and not (sources - {"word_map"})
        if word_map_only and not has_topic:
            item = dict(moment)
            item["_admission_reason"] = "word_map_topic_evidence_missing"
            suppressed.append(item)
            continue
        decision = policy.assess(
            query,
            moment,
            query_plan=query_plan,
            has_topic_evidence=has_topic,
            semantic_score=safe_float(seed.get("embedding_score")),
            rerank_score=safe_float(moment.get("rerank_score")),
            high_confidence_edge="lexical" in sources,
            context_only=str(moment.get("section") or "")
            in TEMPERATURE_MOMENT_SECTIONS,
            auto=auto,
        )
        item = dict(moment)
        item["_admission_reason"] = decision.reason
        item["_admission_debug"] = dict(decision.debug)
        (admitted if decision.admit_direct else suppressed).append(item)
    return admitted, suppressed


__all__ = [
    "TASK_ONLY_MOMENT_SECTIONS",
    "TEMPERATURE_MOMENT_SECTIONS",
    "admit_moments",
    "append_lexical_matches",
    "append_word_map_matches",
    "moment_rerank_document",
    "recall_thresholds",
    "safe_float",
    "score_unit",
    "seed_diagnostic",
]
