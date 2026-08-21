from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast

import pytest
from mcp.server.fastmcp import Context

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
async def test_grow_retains_p0_erasable_test_data_contract(current_runtime) -> None:
    content = "A synthetic grow item that must remain safely erasable."

    result = await current.grow(items=[content], test_data=True)

    assert "1条(预拆分·逐字)|新1合0" in result
    bucket = (await current_runtime["bucket_mgr"].list_all())[0]
    assert bucket["content"] == content
    assert bucket["metadata"]["provenance"]["erasable"] is True

    deleted = await current.trace(
        bucket["id"],
        hard_delete=True,
        delete_reason="union grow test cleanup",
    )
    assert deleted == f"已永久删除测试桶: {bucket['id']}"


@pytest.mark.asyncio
async def test_grow_infers_ying_auto_source_from_hidden_client_context(
    current_runtime,
    monkeypatch,
) -> None:
    from tools.current import memory

    seen: dict[str, object] = {}

    class Gate:
        def should_gate(self, *, auto: bool, source: str) -> bool:
            seen.update(auto=auto, source=source)
            return False

        async def evaluate(self, *_args, **_kwargs):
            raise AssertionError("disabled gate must not evaluate")

    monkeypatch.setattr(memory.rt, "memory_write_gate", Gate())
    context = SimpleNamespace(
        request_context=SimpleNamespace(
            session=SimpleNamespace(
                client_params=SimpleNamespace(
                    clientInfo=SimpleNamespace(name="ob-auto-grow-relay")
                )
            )
        )
    )

    await current.grow(
        content="A context-derived automatic grow source is retained.",
        context=cast(Context, context),
    )

    assert seen == {"auto": False, "source": "operit"}


@pytest.mark.asyncio
async def test_breath_search_retains_p0_created_date_window(current_runtime) -> None:
    query = "calendar-window-marker"
    await current_runtime["bucket_mgr"].create(
        content=f"{query} on the requested day",
        created="2026-07-19T12:00:00+00:00",
    )
    await current_runtime["bucket_mgr"].create(
        content=f"{query} outside the requested day",
        created="2026-07-18T12:00:00+00:00",
    )

    result = await current.breath_search(
        query=query,
        max_results=5,
        date_from="2026-07-19",
        date_to="2026-07-19",
    )

    assert "on the requested day" in result
    assert "outside the requested day" not in result


@pytest.mark.asyncio
async def test_canonical_grow_preserves_shared_source_evidence(
    current_runtime,
) -> None:
    source = "第一行背景\n第二行是需要核对的原话"

    result = await current.grow(
        content=source,
        items=[
            {
                "title": "原话证据",
                "content": "Amy 记住了这句原话。",
                "source_ranges": [[2, 2]],
            }
        ],
    )

    assert "1条(预拆分·逐字)|新1合0" in result
    bucket = (await current_runtime["bucket_mgr"].list_all())[0]
    source_refs = bucket["metadata"]["source_refs"]
    assert source_refs[0]["ref"].startswith("src_")
    assert source_refs[0]["ranges"] == [[2, 2]]


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
