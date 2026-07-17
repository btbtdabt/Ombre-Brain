from __future__ import annotations

import re

import pytest

from tools import current


def _created_id(result: str) -> str:
    match = re.search(r"(?:新建|钉选|whisper|feel|letter|profile_fact)→([^\s|]+)", result)
    assert match, result
    return match.group(1)


@pytest.mark.asyncio
async def test_reminder_create_list_update_flow(current_runtime):
    created = await current.reminder_create(
        "喝水",
        "下午记得喝水",
        repeat_rule="daily",
        daily_limit=2,
    )
    reminder_id = created["reminder"]["id"]
    assert created["status"] == "created"
    assert created["reminder"]["source"] == "mcp"

    listed = await current.reminder_list()
    assert listed["count"] == 1
    assert listed["reminders"][0]["id"] == reminder_id

    updated = await current.reminder_update(reminder_id, status="done")
    assert updated["status"] == "updated"
    assert updated["reminder"]["status"] == "done"


@pytest.mark.asyncio
async def test_bucket_comment_trace_and_light_read_flow(current_runtime):
    result = await current.hold(
        "Amy喜欢茉莉花茶。",
        tags="preference,tea",
        title="茶偏好",
        date="2026-07-15",
        domain="life",
    )
    assert result.startswith("新建→茶偏好 life")
    initial_light = await current.list_buckets_light()
    bucket_id = initial_light["buckets"][0]["id"]

    exact = await current.read_bucket(bucket_id)
    assert exact["id"] == bucket_id
    assert exact["content"] == "Amy喜欢茉莉花茶。"
    assert exact["metadata"]["date"] == "2026-07-15"

    commented = await current.comment_bucket(
        bucket_id,
        "我会记得这份偏好。",
        kind="feel",
        valence=0.8,
    )
    assert commented["status"] == "commented"
    comment_id = commented["comment"]["id"]

    deleted = await current.delete_bucket_comment(bucket_id, comment_id)
    assert deleted["status"] == "deleted"

    traced = await current.trace(bucket_id, name="茉莉花茶", resolved=1)
    assert traced.startswith(f"已修改记忆桶 {bucket_id}:")
    after = await current.read_bucket(bucket_id)
    assert after["metadata"]["name"] == "茉莉花茶"
    assert after["metadata"]["resolved"] is True

    light = await current.list_buckets_light()
    item = next(item for item in light["buckets"] if item["id"] == bucket_id)
    assert "content" not in item
    assert item["name"] == "茉莉花茶"


@pytest.mark.asyncio
async def test_breath_date_recall_reads_archived_ordinary_memory_only(current_runtime):
    manager = current_runtime["bucket_mgr"]
    ordinary_id = await manager.create(
        content="这条普通记忆发生在指定日期。",
        tags=["dated"],
        importance=5,
        domain=["life"],
        valence=0.5,
        arousal=0.3,
        name="日期普通记忆",
        date="2026-07-15",
    )
    await manager.create(
        content="我在同一天留下了私密感受。",
        tags=["whisper"],
        importance=5,
        domain=["feel"],
        valence=0.5,
        arousal=0.3,
        name="日期私密感受",
        bucket_type="feel",
        date="2026-07-15",
    )
    assert await manager.delete(ordinary_id)

    by_argument = await current.breath(date="2026-07-15")
    by_query = await current.breath(query="还记得 2026-07-15 聊了什么")

    assert ordinary_id in by_argument
    assert "这条普通记忆发生在指定日期。" in by_argument
    assert "私密感受" not in by_argument
    assert ordinary_id in by_query


@pytest.mark.asyncio
async def test_letter_and_darkroom_flows_use_temporary_stores(current_runtime):
    letter_result = await current.letter_write(
        "ai",
        "这是一封测试信。",
        title="留给以后",
        date="2026-07-16",
    )
    letter_id = _created_id(letter_result)
    letters = await current.letter_read(query="测试信")
    assert letter_id in letters
    assert "这是一封测试信。" in letters

    entered = await current.darkroom_enter("我还在想这件事。", mood="quiet")
    assert entered["status"] == "entered"
    room_id = entered["room_id"]
    entry_id = entered["entry_id"]

    rooms = await current.darkroom_rooms()
    assert rooms["rooms"][0]["room_id"] == room_id
    viewed = await current.darkroom_view(entry_id)
    assert viewed["content"] == "我还在想这件事。"
    released = await current.darkroom_release(entry_id, reason="ready")
    assert released["status"] == "released"
    deleted = await current.darkroom_delete(room_id, confirm="DELETE")
    assert deleted["status"] == "deleted"
    assert deleted["backup_created"] is True


@pytest.mark.asyncio
async def test_profile_fact_and_entity_backfill_flow(current_runtime):
    evidence_id = await current_runtime["bucket_mgr"].create(
        content="Amy喜欢茉莉花茶。",
        tags=["preference"],
        importance=6,
        domain=["life"],
        valence=0.7,
        arousal=0.3,
        name="证据",
    )

    profile_result = await current.profile_fact(
        "Amy喜欢茉莉花茶。",
        evidence_id,
        predicate="likes",
        object_value="茉莉花茶",
        reflection="我会记得这个稳定偏好。",
    )
    profile_id = _created_id(profile_result)
    profile = await current.read_bucket(profile_id)
    assert profile["metadata"]["profile_kind"] == "preference"
    assert any(
        edge["source"] == profile_id
        and edge["target"] == evidence_id
        and edge["relation_type"] == "evidenced_by"
        for edge in current_runtime["memory_edge_store"].list_edges()
    )

    preview = await current.entity_edge_backfill(bucket_id=evidence_id)
    assert preview["dry_run"] is True
    assert preview["proposed_edges"] >= 1
    applied = await current.entity_edge_backfill(bucket_id=evidence_id, dry_run=False)
    assert applied["edges"] >= 1
    assert current_runtime["entity_edge_store"].list_edges()


@pytest.mark.asyncio
async def test_grow_pulse_introspection_and_dream_compatibility(current_runtime):
    result = await current.grow("第一条长期片段，包含足够内容。||第二条长期片段，也包含足够内容。")
    assert "2条" in result

    pulse = await current.pulse()
    assert "Ombre Brain 记忆系统" in pulse
    assert "记忆列表" in pulse

    introspection = await current.introspection(limit=2)
    assert "=== Introspection ===" in introspection
    compatibility = await current.dream()
    assert compatibility.startswith("dream() 已改名为 introspection()。")


@pytest.mark.asyncio
async def test_grow_infers_operit_source_for_timestamped_auto_payload(
    current_runtime,
    monkeypatch,
):
    class GateSpy:
        def __init__(self):
            self.calls = []

        def should_gate(self, *, auto, source):
            self.calls.append((auto, source))
            return False

        async def evaluate(self, content, *, source, bucket_mgr, auto):
            raise AssertionError("evaluate should not run when should_gate is false")

    gate = GateSpy()
    monkeypatch.setattr("tools._runtime.memory_write_gate", gate)

    await current.grow(
        "【2026-07-17 09:30】\n第一条长期片段，包含足够内容。||第二条长期片段，也包含足够内容。",
        auto=True,
    )

    assert gate.calls == [(True, "operit")]


@pytest.mark.asyncio
async def test_non_overlapping_p0_tool_adapters_remain_callable(current_runtime):
    bucket_id = await current_runtime["bucket_mgr"].create(
        content="保留 P0 anchor 适配器。",
        tags=["compat"],
        importance=5,
        domain=["general"],
        valence=0.5,
        arousal=0.3,
        name="P0 adapter",
    )
    anchored = await current.anchor(bucket_id)
    assert "anchor" in anchored
    assert (await current_runtime["bucket_mgr"].get(bucket_id))["metadata"]["anchor"] is True
    released = await current.release(bucket_id)
    assert "anchor" in released
    assert not (await current_runtime["bucket_mgr"].get(bucket_id))["metadata"].get("anchor", False)

    plan_result = await current.plan("验证迁移后的 P0 plan 入口。")
    assert "plan→" in plan_result
    self_result = await current.I("我会保留兼容边界。", aspect="stance")
    assert "I [stance]" in self_result
    self_read = await current.I(read=True)
    assert "我会保留兼容边界。" in self_read
