"""Validation and presentation helpers for Relation V1 sidecar ledgers."""
from __future__ import annotations

from typing import Any

MAX_RELATION_LINKS = 64
MAX_ACTIVE_RELATION_LINKS = 16
MAX_RELATION_LABEL_CHARS = 20
MAX_RELATION_TYPE_CHARS = 32
_RELATION_TYPES = frozenset({
    "caused_by", "causes", "continuation_of", "continues", "related_to",
    "same_event",
})
_DEFAULT_DISPLAY_LABELS = {
    "caused_by": "\u539f\u56e0",
    "causes": "\u7ed3\u679c",
    "continuation_of": "\u524d\u6bb5",
    "continues": "\u540e\u7eed",
    "related_to": "\u76f8\u5173",
    "same_event": "\u540c\u4e00\u4e8b\u4ef6",
}


def normalize_relation_type(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("relation_type 必须是字符串安全键")
    value = value.strip().lower()
    if value not in _RELATION_TYPES:
        raise ValueError("relation_type must be one of the six supported relation types")
    return value


def normalize_relation_label(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("relation label 必须是字符串")
    if "\r" in value or "\n" in value:
        raise ValueError("relation label 不允许换行")
    value = value.strip()
    if len(value) > MAX_RELATION_LABEL_CHARS:
        raise ValueError(f"relation label 最多 {MAX_RELATION_LABEL_CHARS} 个字符")
    return value


def normalize_relation_links(value: Any) -> list[dict[str, str]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("relation_links 必须是列表")
    if len(value) > MAX_RELATION_LINKS:
        raise ValueError(f"relation_links 过多（{len(value)} > {MAX_RELATION_LINKS}）")
    links: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("relation_links 每项必须是对象")
        target_bucket_id = item.get("target_bucket_id")
        if not isinstance(target_bucket_id, str):
            raise ValueError("relation_links target_bucket_id 必须是字符串")
        target_bucket_id = target_bucket_id.strip()
        if not target_bucket_id or "\r" in target_bucket_id or "\n" in target_bucket_id:
            raise ValueError("relation_links 包含非法 target_bucket_id")
        status = item.get("status")
        if not isinstance(status, str):
            raise ValueError("relation_links status 必须是字符串")
        status = status.strip().lower()
        if status not in {"active", "detached"}:
            raise ValueError("relation_links status 必须是 active 或 detached")
        links.append({"target_bucket_id": target_bucket_id, "type": normalize_relation_type(item.get("type")), "label": normalize_relation_label(item.get("label")), "status": status})
    if sum(item["status"] == "active" for item in links) > MAX_ACTIVE_RELATION_LINKS:
        raise ValueError(f"活动 relation_links 过多（>{MAX_ACTIVE_RELATION_LINKS}）")
    return links


def relation_display_label(relation_type: str, label: str | None = "") -> str:
    """Render a Relation label without changing its raw type or ledger data."""
    return label or _DEFAULT_DISPLAY_LABELS.get(relation_type, relation_type)


def relation_hint(bucket: dict, limit: int = 2) -> str:
    meta = bucket.get("metadata") or {}
    if str(meta.get("type") or "dynamic").strip().lower() in {"plan", "feel", "letter", "i", "i_candidate", "identity"}:
        return ""
    try:
        links = normalize_relation_links(meta.get("relation_links"))
    except ValueError:
        return ""
    rows = []
    for link in links:
        if link["status"] == "active":
            label = relation_display_label(link["type"], link["label"])
            rows.append(f"↳ {label} → {link['target_bucket_id']}")
            if len(rows) >= limit:
                break
    return "\n".join(rows)
