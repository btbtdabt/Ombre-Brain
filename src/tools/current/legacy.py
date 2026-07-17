"""Adapters for non-overlapping public P0 tools retained during migration."""

from __future__ import annotations

from ..anchor import anchor_release, anchor_set
from ..i import dispatch as p0_i
from ..plan import plan_create


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


async def i_tool(
    content: str = "",
    aspect: str = "",
    read: bool = False,
    limit: int = 20,
) -> str:
    """记录或读取自我认知条目。content=要记录的自我认知内容(空=进入读取模式)。aspect=维度:nature(本质)/values(看重的)/patterns(规律)/limits(局限)/becoming(变化方向)/uncertainty(不确定的)/stance(立场)(可选)。read=True=读取所有已积累条目。limit=返回条数上限(默认 20)。条目不参与普通 breath/dream，SessionStart 时自动附最近 3 条。"""
    return await p0_i(content=content, aspect=aspect, read=read, limit=limit)


i_tool.__name__ = "I"
I = i_tool  # noqa: E741 - historical public MCP tool name


__all__ = ["I", "anchor", "plan", "release"]
