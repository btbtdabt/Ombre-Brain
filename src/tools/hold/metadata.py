"""Canonical metadata normalization shared by hold storage branches."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class HoldMetadata:
    domains: list[Any]
    valence: float
    arousal: float
    tags: list[Any]
    suggested_name: Any


def default_hold_analysis() -> dict[str, Any]:
    return {
        "domain": ["未分类"],
        "valence": 0.5,
        "arousal": 0.3,
        "tags": [],
        "suggested_name": "",
    }


def normalize_hold_metadata(
    analysis: object,
    extra_tags: list[Any],
    requested_valence: float,
    requested_arousal: float,
) -> HoldMetadata:
    analysis = analysis if isinstance(analysis, Mapping) else default_hold_analysis()
    domains = analysis.get("domain") or ["未分类"]
    if not isinstance(domains, list):
        domains = ["未分类"]
    analyzed_valence = analysis.get("valence", 0.5)
    analyzed_arousal = analysis.get("arousal", 0.3)
    valence = (
        requested_valence
        if 0 <= requested_valence <= 1
        else float(analyzed_valence)
        if analyzed_valence is not None
        else 0.5
    )
    arousal = (
        requested_arousal
        if 0 <= requested_arousal <= 1
        else float(analyzed_arousal)
        if analyzed_arousal is not None
        else 0.3
    )
    raw_tags = analysis.get("tags") or []
    tags = list(
        dict.fromkeys(
            (raw_tags if isinstance(raw_tags, list) else []) + list(extra_tags)
        )
    )
    return HoldMetadata(
        domains=domains,
        valence=valence,
        arousal=arousal,
        tags=tags,
        suggested_name=analysis.get("suggested_name", ""),
    )
