"""P0 plan implementation and compatibility adapters for canonical letters."""

from __future__ import annotations

import math
from typing import Optional

from letter_service import LetterService
from utils import get_ai_name

from .. import _runtime as rt
from .._common import (
    check_content_size,
    check_metadata_size,
    check_query_size,
)


async def plan_create(
    content: str,
    status: Optional[str] = "active",
    related_bucket: Optional[str] = "",
    weight: Optional[float] = 0.5,
    why_remembered: Optional[str] = "",
) -> str:
    if status is None:
        status = "active"
    if related_bucket is None:
        related_bucket = ""
    if weight is None:
        weight = 0.5
    if why_remembered is None:
        why_remembered = ""
    try:
        parsed_weight = float(weight)
    except (TypeError, ValueError, OverflowError):
        parsed_weight = 0.5
    if not math.isfinite(parsed_weight):
        parsed_weight = 0.5
    weight = max(0.0, min(1.0, parsed_weight))
    why_remembered = str(why_remembered).strip()[:500]
    await rt.decay_engine.ensure_started()
    if not content or not content.strip():
        return "内容为空，无法登记计划。"
    size_err = check_content_size(content)
    if size_err:
        return size_err
    metadata_err = check_metadata_size(
        status=status,
        related_bucket=related_bucket,
        why_remembered=why_remembered,
    )
    if metadata_err:
        return metadata_err
    status = status.strip().lower()
    if status not in ("active", "resolved", "abandoned"):
        status = "active"

    norm = content.strip()
    try:
        all_buckets = await rt.bucket_mgr.list_all(include_archive=False)
        for bucket in all_buckets:
            metadata = bucket.get("metadata", {})
            if (
                metadata.get("type") == "plan"
                and metadata.get("status", "active") == "active"
                and (bucket.get("content") or "").strip() == norm
            ):
                return f"跟原有 active plan 完全重复→{bucket['id']}（未重复登记）"
    except Exception as exc:
        rt.logger.warning("plan() dedup scan failed: %s", exc)

    bucket_id = await rt.bucket_mgr.create(
        content=content.strip(),
        tags=["__plan__"],
        importance=7,
        domain=["plan"],
        valence=0.5,
        arousal=0.4,
        name=None,
        bucket_type="plan",
        why_remembered=why_remembered,
        weight=weight,
        source_tool="plan",
    )
    from .._common import append_plan_change_log

    initial_log = append_plan_change_log([], "created", to=status)
    update_kwargs = {"status": status, "change_log": initial_log}
    if related_bucket.strip():
        update_kwargs["related_bucket"] = related_bucket.strip()
    try:
        await rt.bucket_mgr.update(bucket_id, **update_kwargs)
    except Exception as exc:
        rt.logger.warning("plan() failed to set status/related: %s", exc)
    return f"📋plan→{bucket_id} [{status}]"


async def letter_write(
    author: str,
    content: str,
    user_name: Optional[str] = "",
    title: Optional[str] = "",
    date: Optional[str] = "",
    ai_name: Optional[str] = "",
) -> str:
    """Compatibility adapter to the canonical current letter writer."""

    if error := check_content_size(content):
        return error
    if error := check_metadata_size(
        author=author,
        user_name=user_name or "",
        title=title or "",
        date=date or "",
        ai_name=ai_name or "",
    ):
        return error
    service = LetterService(
        {"identity": {"ai_name": get_ai_name()}},
        rt.bucket_mgr,
        rt.embedding_engine,
    )
    return await service.write(
        author=author,
        content=content,
        user_name=user_name or "",
        title=title or "",
        date=date or "",
        ai_name=ai_name or "",
    )


async def letter_read(
    query: Optional[str] = "",
    limit: Optional[int] = 10,
    author: Optional[str] = "",
    date_from: Optional[str] = "",
    date_to: Optional[str] = "",
) -> str:
    """Compatibility adapter to the canonical current letter reader."""

    if error := check_query_size(query or ""):
        return error
    if error := check_metadata_size(
        author=author or "",
        date_from=date_from or "",
        date_to=date_to or "",
    ):
        return error
    service = LetterService(
        {"identity": {"ai_name": get_ai_name()}},
        rt.bucket_mgr,
        rt.embedding_engine,
    )
    return await service.read(
        query=query or "",
        limit=10 if limit is None else limit,
        author=author or "",
        date_from=date_from or "",
        date_to=date_to or "",
    )
