"""Predicates shared by retained P0 breath modes."""

from __future__ import annotations

from typing import Any


def bucket_has_tags(metadata: dict[str, Any], tag_filter: list[Any]) -> bool:
    if not tag_filter:
        return True
    bucket_tags = set(metadata.get("tags", []) or [])
    return all(tag in bucket_tags for tag in tag_filter)


__all__ = ["bucket_has_tags"]
