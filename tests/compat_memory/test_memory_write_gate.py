import pytest

from memory_write_gate import MemoryWriteGate


class EmptyBucketManager:
    async def list_all(self, include_archive=False):
        return []


class DuplicateBucketManager:
    async def list_all(self, include_archive=False):
        return [
            {
                "metadata": {"name": "Operit 自动写入门卫", "tags": ["workflow"], "domain": ["tech"]},
                "content": "决定把自动总结先交给 grow 门卫判断。",
            }
        ]


@pytest.mark.asyncio
async def test_write_gate_skips_low_signal_auto_candidates_and_records_them(tmp_path):
    gate = MemoryWriteGate({"state_dir": str(tmp_path)})

    decision = await gate.evaluate(
        "刚才只是测试一下，不用记。",
        source="operit",
        bucket_mgr=EmptyBucketManager(),
    )

    assert gate.should_gate(source="operit")
    assert not decision.allow
    assert decision.decision == "skipped"
    assert "low_surprise" in decision.reasons
    assert gate.list_recent()[-1]["candidate_id"] == decision.candidate_id


@pytest.mark.asyncio
async def test_write_gate_keeps_task_status_pending_then_promotes_a_repeat(tmp_path):
    gate = MemoryWriteGate(
        {
            "state_dir": str(tmp_path),
            "memory_write_gate": {
                "pending_threshold": 0.35,
                "grow_threshold": 0.95,
                "repeat_promote_count": 2,
            },
        }
    )
    content = (
        "2026-05-31 Operit workflow 决定把自动总结先交给 grow 门卫判断，"
        "TODO：接入 memory_commit；未完成：确认 Termux 服务路径。"
    )

    first = await gate.evaluate(content, source="operit", bucket_mgr=EmptyBucketManager())
    second = await gate.evaluate(content, source="operit", bucket_mgr=EmptyBucketManager())

    assert first.decision == "pending"
    assert "task_status_signal" in first.reasons
    assert second.allow
    assert second.decision == "grow"
    assert "repeated_pending" in second.reasons
    assert [row["decision"] for row in gate.list_recent()][-2:] == ["pending", "grow"]


@pytest.mark.asyncio
async def test_write_gate_rejects_existing_memory_duplicates(tmp_path):
    gate = MemoryWriteGate({"state_dir": str(tmp_path)})

    decision = await gate.evaluate(
        "决定把自动总结先交给 grow 门卫判断。",
        source="operit",
        bucket_mgr=DuplicateBucketManager(),
    )

    assert not decision.allow
    assert decision.decision == "skipped"
    assert "duplicate_existing_memory" in decision.reasons

