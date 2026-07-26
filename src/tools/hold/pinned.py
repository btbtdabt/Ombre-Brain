"""
========================================
tools/hold/pinned.py — hold(pinned=True) 分支
========================================

把这条桶钉为「永久核心准则」：跳过合并，强制 importance=10，写到
permanent 目录，不衰减、不会被合并掉。

关键行为：
- 先做 pinned 数量配额检查（默认 20 个上限），超了就拒绝并提示
- 仍然走 LLM analyze 拿 domain/valence/arousal/tags/suggested_name；
  她/他显式传入的 valence/arousal 优先
- type="permanent" + pinned=True 双重标记
- embedding 由 create() 尝试同步生成；不可用时仍保留逐字原文，稍后可 backfill

不做什么（边界）：
- 不做合并尝试：pinned 桶之间互不合并，分别保留
- 不允许 importance < 10：钉选意味着最高重要度

对外暴露：store_pinned(content, extra_tags, valence, arousal,
                       why_remembered, meaning, media) → str
========================================
"""

from .. import _runtime as rt
from .._common import check_pinned_quota, _quota_turn
from .metadata import default_hold_analysis, normalize_hold_metadata
from collections.abc import Awaitable, Callable
from typing import cast


async def store_pinned(
    content: str,
    extra_tags: list,
    valence: float,
    arousal: float,
    why_remembered: str,
    meaning: str = "",
    media: list | None = None,
) -> str:
    try:
        analysis = await rt.dehydrator.analyze(content)
    except Exception as e:
        rt.logger.warning(f"Auto-tagging failed, using defaults / 自动打标失败: {e}")
        analysis = default_hold_analysis()

    metadata = normalize_hold_metadata(analysis, extra_tags, valence, arousal)

    # 配额判定 + 落盘必须在同一把锁里：两个并发 hold(pinned=True) 都可能在
    # 对方提交前读到同一个「未满」快照，检查和创建隔着一次 await 就会互相看不见。
    async with _quota_turn("pinned"):
        err = await check_pinned_quota()
        if err:
            return err

        bucket_id = await rt.bucket_mgr.create(
            content=content,
            tags=metadata.tags,
            importance=10,
            domain=metadata.domains,
            valence=metadata.valence,
            arousal=metadata.arousal,
            name=metadata.suggested_name or None,
            bucket_type="permanent",
            pinned=True,
            why_remembered=why_remembered,
            source_tool="hold",
            allow_embedding_fallback=True,
            meaning=meaning,
            media=media,
            defer_derived_index=True,
        )
    post_index = getattr(rt.bucket_mgr, "_index_after_update", None)
    if callable(post_index):
        post_index_fn = cast(Callable[..., Awaitable[bool]], post_index)
        await post_index_fn(
            bucket_id,
            content_changed=True,
            meaning_changed=bool(meaning),
        )
    return f"📌钉选→{bucket_id} {','.join(str(d) for d in metadata.domains if d is not None)}"
