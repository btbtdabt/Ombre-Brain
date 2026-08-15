"""Adapters for non-overlapping public P0 tools retained during migration."""

from __future__ import annotations

from .. import relation_bindings as p0_relation_bindings
from ..relation_read import dispatch as p0_relation_read
from .. import source_bindings as p0_source_bindings
from ..anchor import anchor_release, anchor_set
from ..i import dispatch as p0_i
from ..plan import plan_create
from ..source_read import dispatch as p0_source_read


async def anchor(bucket_id: str) -> str:
    """把指定桶标记为 anchor(坐标系)。anchor 不主动出现在默认 breath，但 query/domain/emotion 命中时仍返回。硬上限 24，已满时拒绝并提示先 release。"""
    return await anchor_set(bucket_id)


async def release(bucket_id: str) -> str:
    """解除指定桶的 anchor 标记。桶恢复为普通状态，重新参与默认 breath；pinned 状态保留。"""
    return await anchor_release(bucket_id)


async def plan(
    content: str,
    status: str = "active",
    related_bucket: str = "",
    weight: float = 0.5,
    why_remembered: str = "",
) -> str:
    """登记一个待办/承诺/未闭环事项。status=active(默认)/resolved/abandoned。related_bucket 可选,关联到某个普通记忆桶。weight=承诺重量 0.0-1.0(默认 0.5),与 importance 区分——importance 表示「多重要」、weight 表示「多重」。why_remembered=登记原因(可选、仅展示)。plan 不衰减、不出现在普通 breath,仅在 dream 末尾的 active 段返回;后续 hold/grow 写入新事件时系统自动判断已登记的 plan 是否完成。"""
    return await plan_create(
        content=content,
        status=status,
        related_bucket=related_bucket,
        weight=weight,
        why_remembered=why_remembered,
    )


async def source_read(
    bucket_id: str,
    expected_title: str,
    scope: str = "event",
    cursor: int = 0,
    max_tokens: int = 6000,
    source_slots: list[int] | None = None,
    all_sources: bool = False,
) -> str:
    """显式读取一个记忆桶对应的原文证据。必须给出精确 bucket_id 与 title；多 Source 默认只回 slot/ranges/status 清单，显式 source_slots 或 all_sources 才读活动原文。分页时保持同一桶、标题、scope 与 Source 选择。"""
    return await p0_source_read(
        bucket_id=bucket_id,
        expected_title=expected_title,
        scope=scope,
        cursor=cursor,
        max_tokens=max_tokens,
        source_slots=source_slots,
        all_sources=all_sources,
    )


async def source_attach(
    bucket_id: str,
    expected_title: str,
    source_content: str,
    source_ranges: list[list[int]] | None = None,
) -> str:
    """给精确 bucket_id + title 的已有桶后补一份独立不可变 Source；只改证据绑定，不改正文、活跃度或生命周期。"""
    return await p0_source_bindings.attach(
        bucket_id,
        expected_title,
        source_content,
        source_ranges,
    )


async def source_detach(
    bucket_id: str,
    expected_title: str,
    source_slot: int,
) -> str:
    """断开一个稳定 Source slot，只停用本桶绑定，不删除共享 Source blob，也不改变桶生命周期。"""
    return await p0_source_bindings.detach(bucket_id, expected_title, source_slot)


async def source_restore(
    bucket_id: str,
    expected_title: str,
    source_slot: int,
) -> str:
    """恢复一个 detached Source slot 的原绑定；只恢复证据引用，不恢复 archived 桶。桶生命周期恢复请用 trace(..., restore=True)。"""
    return await p0_source_bindings.restore(bucket_id, expected_title, source_slot)


async def relation_read(bucket_id: str, expected_title: str) -> str:
    """读取本普通记忆桶的极简 Relation ledger；不读取目标标题或正文。"""
    return await p0_relation_read(bucket_id, expected_title)


async def relation_attach(
    bucket_id: str,
    expected_title: str,
    target_bucket_id: str,
    relation_type: str,
    label: str = "",
) -> str:
    """为两个普通记忆桶建立一跳有向 Relation，不创建反向边。"""
    return await p0_relation_bindings.attach(
        bucket_id,
        expected_title,
        target_bucket_id,
        relation_type,
        label,
    )


async def relation_detach(
    bucket_id: str,
    expected_title: str,
    relation_slot: int,
) -> str:
    """原位停用一个稳定 Relation slot，不改记忆正文或活跃度。"""
    return await p0_relation_bindings.detach(
        bucket_id,
        expected_title,
        relation_slot,
    )


async def relation_restore(
    bucket_id: str,
    expected_title: str,
    relation_slot: int,
) -> str:
    """恢复一个 detached Relation slot，不恢复 archived 桶生命周期。"""
    return await p0_relation_bindings.restore(
        bucket_id,
        expected_title,
        relation_slot,
    )


async def i_tool(
    content: str = "",
    aspect: str = "",
    read: bool = False,
    limit: int = 20,
    promote: str = "",
) -> str:
    """写下或读取自我认知。I 是沉淀物不是日记：content=一个「我觉得……」，先落成一条普通记忆（候选），会浮现也会衰减，每次 dream 都跟相关记忆摆在一起碰撞。aspect=维度:nature(本质)/values(看重的)/patterns(规律)/limits(局限)/becoming(变化方向)/uncertainty(不确定的)/stance(立场)(可选)。read=True 或全空=读正式条目+待沉淀候选。limit=返回条数上限(默认 20)。promote=候选桶ID，被 3 次不同日期的 dream 见证后才能升级成正式条目（可同时传 content 用提炼后的措辞）。正式条目不参与普通 breath/dream，SessionStart 时自动附最近 3 条。"""
    return await p0_i(
        content=content,
        aspect=aspect,
        read=read,
        limit=limit,
        promote=promote,
    )


i_tool.__name__ = "I"
I = i_tool  # noqa: E741 - historical public MCP tool name


__all__ = [
    "I",
    "anchor",
    "plan",
    "relation_attach",
    "relation_detach",
    "relation_read",
    "relation_restore",
    "release",
    "source_attach",
    "source_detach",
    "source_read",
    "source_restore",
]
