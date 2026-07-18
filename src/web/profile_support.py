"""Shared profile-fact normalization used by routes and service adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from profile_facts import (
    is_profile_fact_bucket,
    legacy_profile_key,
    profile_evidence,
    profile_key,
    profile_kind_from_tags,
    profile_sections,
    profile_state,
)
from utils import strip_wikilinks

from .current_contract import maybe_await, valid_memory_id


async def build_profile_payload(
    bucket: dict[str, Any],
    *,
    get_bucket: Callable[[str], Any],
    edge_store: Any,
) -> dict[str, Any]:
    metadata = bucket.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    evidence_rows: list[dict[str, Any]] = []
    for row in profile_evidence(bucket, edge_store):
        evidence_bucket = (
            await maybe_await(get_bucket(row["bucket_id"]))
            if valid_memory_id(row["bucket_id"])
            else None
        )
        evidence_metadata = (
            evidence_bucket.get("metadata", {})
            if isinstance(evidence_bucket, dict)
            else {}
        )
        if not isinstance(evidence_metadata, dict):
            evidence_metadata = {}
        evidence_rows.append(
            {
                **row,
                "name": evidence_metadata.get("name", row["bucket_id"]),
                "exists": bool(evidence_bucket),
            }
        )

    sections = profile_sections(bucket.get("content", ""))
    state = profile_state(metadata)
    kind = str(
        metadata.get("profile_kind")
        or profile_kind_from_tags(metadata.get("tags", []))
        or ""
    ).strip()
    content = strip_wikilinks(str(bucket.get("content") or ""))
    return {
        "id": bucket.get("id", ""),
        "name": metadata.get("name", bucket.get("id", "")),
        "fact": sections.get("fact", content.strip()),
        "sections": sections,
        "kind": kind,
        "subject": metadata.get("subject", ""),
        "predicate": metadata.get("predicate", ""),
        "object": metadata.get("object", ""),
        "evidence": evidence_rows,
        "confidence": metadata.get("confidence"),
        "source": metadata.get("source", "profile_fact"),
        "active": state == "active",
        "deprecated": state == "deprecated",
        "state": state,
        "tags": metadata.get("tags", []),
        "created": metadata.get("created", ""),
        "updated_at": metadata.get("updated_at", ""),
        "last_active": metadata.get("last_active", ""),
        "content_preview": content[:200],
    }


__all__ = [
    "build_profile_payload",
    "is_profile_fact_bucket",
    "legacy_profile_key",
    "profile_evidence",
    "profile_key",
    "profile_kind_from_tags",
    "profile_sections",
    "profile_state",
]
