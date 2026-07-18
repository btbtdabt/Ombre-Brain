from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from tools import current


@pytest.mark.asyncio
async def test_hold_persists_p0_metadata_and_trace_can_erase_test_data(
    current_runtime,
) -> None:
    written = await current.hold(
        "A synthetic memory used to verify the union contract.",
        title="Union test memory",
        why_remembered="It verifies migrated metadata.",
        meaning="The compatibility boundary matters.",
        test_data=True,
    )
    assert written.startswith("新建→Union test memory")

    listed = await current.list_buckets_light()
    bucket_id = listed["buckets"][0]["id"]
    bucket = await current.read_bucket(bucket_id)
    assert bucket["metadata"]["why_remembered"] == "It verifies migrated metadata."
    assert bucket["metadata"]["meaning"] == [
        "The compatibility boundary matters."
    ]
    assert bucket["metadata"]["provenance"]["erasable"] is True

    deleted = await current.trace(
        bucket_id,
        hard_delete=True,
        delete_reason="compatibility test cleanup",
    )
    assert deleted == f"已永久删除测试桶: {bucket_id}"
    assert await current_runtime["bucket_mgr"].get(bucket_id) is None


@pytest.mark.asyncio
async def test_identical_current_hold_calls_preserve_p0_merge_contract(
    current_runtime,
) -> None:
    content = "Identical ordinary hold calls must converge on one durable bucket."

    first = await current.hold(content, title="P0 merge contract")
    second = await current.hold(content, title="P0 merge contract")
    buckets = await current_runtime["bucket_mgr"].list_all()

    assert first.startswith("新建→P0 merge contract")
    assert len(buckets) == 1
    assert second.startswith(f"合并→{buckets[0]['id']}")
    assert buckets[0]["content"] == content


@pytest.mark.asyncio
async def test_grow_accepts_p0_pre_split_items_without_rewriting(
    current_runtime,
) -> None:
    first = "Amy keeps the first pre-split memory verbatim."
    second = "Haven keeps the second pre-split memory verbatim."

    result = await current.grow(items=[first, {"content": second}])

    assert "2条(预拆分·逐字)|新2合0" in result
    buckets = await current_runtime["bucket_mgr"].list_all()
    contents = {bucket["content"] for bucket in buckets}
    assert first in contents
    assert second in contents


@pytest.mark.asyncio
async def test_trace_combines_current_date_anchor_with_p0_metadata(
    current_runtime,
) -> None:
    created = datetime.now(timezone.utc) - timedelta(days=2)
    bucket_id = await current_runtime["bucket_mgr"].create(
        content="A mature memory can become an anchor.",
        domain=["relationship"],
        created=created.isoformat(),
    )

    result = await current.trace(
        bucket_id,
        anchor=1,
        date="2026-07-14",
        weight=0.8,
        dont_surface=1,
        why_remembered="A stable coordinate.",
        meaning_append="It remains useful as a reference point.",
    )

    assert result.startswith(f"已修改记忆桶 {bucket_id}:")
    bucket = await current.read_bucket(bucket_id)
    assert bucket["metadata"]["anchor"] is True
    assert bucket["metadata"]["date"] == "2026-07-14"
    assert bucket["metadata"]["weight"] == 0.8
    assert bucket["metadata"]["dont_surface"] is True
    assert bucket["metadata"]["why_remembered"] == "A stable coordinate."
    assert bucket["metadata"]["meaning"] == [
        "It remains useful as a reference point."
    ]


@pytest.mark.asyncio
async def test_trace_anchor_respects_pinned_invariant(current_runtime) -> None:
    current_runtime["config"]["anchor"] = {
        "max_count": 12,
        "min_age_hours": 0,
    }
    bucket_id = await current_runtime["bucket_mgr"].create(
        content="Pinned guidance cannot also become an anchor.",
        pinned=True,
    )

    result = await current.trace(bucket_id, anchor=1)

    bucket = await current_runtime["bucket_mgr"].get(bucket_id)
    assert "pinned" in result
    assert bucket["metadata"]["pinned"] is True
    assert not bucket["metadata"].get("anchor", False)


@pytest.mark.asyncio
async def test_concurrent_trace_anchor_respects_configured_limit(
    current_runtime,
) -> None:
    current_runtime["config"]["anchor"] = {
        "max_count": 1,
        "min_age_hours": 0,
    }
    bucket_ids = [
        await current_runtime["bucket_mgr"].create(
            content=f"Concurrent anchor candidate {index}.",
        )
        for index in range(5)
    ]

    results = await asyncio.gather(
        *(current.trace(bucket_id, anchor=1) for bucket_id in bucket_ids)
    )

    assert await current_runtime["bucket_mgr"].count_anchors() == 1
    assert sum(result.startswith("已修改记忆桶") for result in results) == 1


@pytest.mark.asyncio
async def test_dream_supports_current_alias_and_p0_time_window(
    current_runtime,
) -> None:
    await current_runtime["bucket_mgr"].create(
        content="A recent memory for the P0 dream window.",
        domain=["life"],
    )

    introspection = await current.dream()
    windowed = await current.dream(window_hours=48)

    assert introspection.startswith("dream() 已改名为 introspection()。")
    assert "A recent memory for the P0 dream window." in windowed
