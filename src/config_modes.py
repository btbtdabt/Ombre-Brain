"""Canonical normalization for runtime configuration modes."""

from __future__ import annotations

from typing import Any


def normalize_direct_render_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in {"auto", "compact", "full"} else "auto"


def normalize_retrieval_mode(value: Any) -> str:
    mode = str(value or "graph").strip().lower()
    if mode == "legacy":
        return "bucket"
    return mode if mode in {"graph", "bucket"} else "graph"


def normalize_thinking_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized in {"enabled", "enable", "on", "true", "thinking"}:
        return "enabled"
    if normalized in {"disabled", "disable", "off", "false", "non-thinking"}:
        return "disabled"
    return ""
