"""Shared record-upsert policy for independent edge stores."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any


def upsert_by_confidence(
    records: list[dict[str, Any]],
    candidate: dict[str, Any],
    same_record: Callable[[dict[str, Any], dict[str, Any]], bool],
) -> list[dict[str, Any]]:
    """Replace a matching record only when confidence is not reduced."""

    for index, existing in enumerate(records):
        if not same_record(existing, candidate):
            continue
        if float(existing.get("confidence", 0.0)) <= float(candidate["confidence"]):
            records[index] = candidate
        return records
    records.append(candidate)
    return records


def load_jsonl_records(
    path: str,
    normalize: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> list[dict[str, Any]]:
    """Load valid normalized object records from a JSONL edge file."""

    if not os.path.exists(path):
        return []
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            record = normalize(raw)
            if record:
                records.append(record)
    return records
