"""Profile-fact normalization shared by tools, services, and HTTP adapters."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from self_anchor import is_self_anchor_bucket
from utils import strip_wikilinks


def profile_key(value: Any, default: str = "") -> str:
    text = str(value or "").strip().lower()
    if not text:
        return default
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^0-9a-zA-Z_\-\u4e00-\u9fff]+", "", text)
    return text or default


def legacy_profile_key(value: Any, default: str = "") -> str:
    """Preserve the Dashboard edit route's persisted ASCII key contract."""

    text = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower())
    return text.strip("_") or default


def profile_sections(
    content: Any,
    *,
    key_normalizer: Callable[[Any, str], str] = profile_key,
) -> dict[str, str]:
    text = strip_wikilinks(str(content or "")).strip()
    if not text:
        return {}
    matches = list(re.finditer(r"(?m)^###\s+([^\n]+)\n?", text))
    if not matches:
        return {"fact": text}
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        heading = key_normalizer(match.group(1), "")
        if not heading:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[heading] = text[match.end() : end].strip()
    prefix = text[: matches[0].start()].strip()
    if "fact" not in sections and prefix:
        sections["fact"] = prefix
    return sections


def profile_kind_from_tags(tags: Any) -> str:
    for tag in tags or []:
        text = str(tag or "").strip()
        if not text.startswith("profile_"):
            continue
        if text in {"profile_fact", "profile_predicate"}:
            continue
        if text.startswith("profile_predicate_"):
            continue
        return text.removeprefix("profile_")
    return ""


def profile_state(metadata: dict[str, Any]) -> str:
    if metadata.get("deprecated") or metadata.get("active") is False:
        return "deprecated"
    if metadata.get("resolved") or metadata.get("digested"):
        return "inactive"
    return "active"


def is_profile_fact_bucket(bucket: dict[str, Any]) -> bool:
    if is_self_anchor_bucket(bucket):
        return False
    metadata = bucket.get("metadata", {})
    if not isinstance(metadata, dict):
        return False
    tags = {str(tag).strip() for tag in metadata.get("tags", []) or []}
    return "profile_fact" in tags or bool(metadata.get("profile_kind"))


def profile_evidence(bucket: dict[str, Any], edge_store: Any) -> list[dict[str, str]]:
    metadata = bucket.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    raw = metadata.get("evidence", [])
    raw = [raw] if isinstance(raw, dict) else raw
    rows: list[dict[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            bucket_id = str(item.get("bucket_id") or item.get("id") or "").strip()
            if bucket_id:
                rows.append(
                    {
                        "bucket_id": bucket_id,
                        "moment_id": str(item.get("moment_id") or "").strip(),
                    }
                )
    for bucket_key, moment_key in (
        ("evidence_bucket_id", "evidence_moment_id"),
        ("source_bucket_id", "source_moment_id"),
    ):
        bucket_id = str(metadata.get(bucket_key) or "").strip()
        if bucket_id:
            rows.append(
                {
                    "bucket_id": bucket_id,
                    "moment_id": str(metadata.get(moment_key) or "").strip(),
                }
            )

    list_edges = getattr(edge_store, "list_edges", None)
    if callable(list_edges):
        try:
            edge_values = list_edges()
            if not isinstance(edge_values, Iterable):
                edge_values = ()
            for edge in edge_values:
                if not isinstance(edge, dict):
                    continue
                if str(edge.get("source") or "") != str(bucket.get("id") or ""):
                    continue
                if str(edge.get("relation_type") or "") != "evidenced_by":
                    continue
                rows.append(
                    {
                        "bucket_id": str(edge.get("target") or ""),
                        "moment_id": "",
                    }
                )
        except Exception:
            pass

    bucket_ids_with_moment = {
        row["bucket_id"]
        for row in rows
        if row["bucket_id"] and row["moment_id"]
    }
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for row in rows:
        if not row["moment_id"] and row["bucket_id"] in bucket_ids_with_moment:
            continue
        key = (row["bucket_id"], row["moment_id"])
        if row["bucket_id"] and key not in seen:
            seen.add(key)
            result.append(row)
    return result


__all__ = [
    "is_profile_fact_bucket",
    "legacy_profile_key",
    "profile_evidence",
    "profile_key",
    "profile_kind_from_tags",
    "profile_sections",
    "profile_state",
]
