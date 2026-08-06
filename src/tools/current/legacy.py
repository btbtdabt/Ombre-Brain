"""Adapters for non-overlapping public P0 tools retained during migration."""

from __future__ import annotations

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
) -> str:
    """显式读取一个记忆桶对应的原文证据。必须同时给出精确 bucket_id 与该桶的显式 title；不做语义搜索、不扩散到相关桶、不调用模型。scope=event 只读该事件声明的行范围，scope=full_source 读取整份共享原文。内容过长时返回 next_cursor，继续以同一桶和标题分页读取。"""
    return await p0_source_read(
        bucket_id=bucket_id,
        expected_title=expected_title,
        scope=scope,
        cursor=cursor,
        max_tokens=max_tokens,
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


__all__ = ["I", "anchor", "plan", "release", "source_read"]
