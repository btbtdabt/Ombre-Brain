"""
========================================
tools/hold/core.py — hold 普通存入分支（含自动合并）
========================================

非 feel、非 pinned 时走这里：优先调 LLM 自动打标，失败则用本地中性元数据，
再用检索找近似桶，
超过 merge_threshold 则合并（hold 用 raw_merge=True 拼接原文，不压缩），
否则新建。

关键行为：
- analyze() 失败（API key/限流/网络不可用）时仍逐字保存正文，只降级元数据
- 她/他显式 valence/arousal 优先于 LLM 打标
- 调 _common.merge_or_create 走合并/新建
- iter 2.0：source_tool 写 ``hold``；合并到老桶时只更新 ``last_merged_by``
- embedding 失败时桶正常创建，返回追加向量化降级警告
- 写完 fire-and-forget：plan 完成建议判断 + 新桶疑似重复扫描

不做什么（边界）：
- 不做 pinned 配额检查（那是 pinned 分支的事）
- 不做单桶字节上限校验（已在 dispatch 入口做过）

对外暴露：store_core(content, extra_tags, importance, valence, arousal,
                     why_remembered, meaning, media) → str
========================================
"""

import asyncio

from utils import normalize_memory_title

from .. import _runtime as rt
from .._common import merge_or_create, check_duplicate_for, check_plan_resolution
from .metadata import default_hold_analysis, normalize_hold_metadata


async def store_core(
    content: str,
    extra_tags: list,
    importance: int,
    valence: float,
    arousal: float,
    why_remembered: str,
    title: str = "",
    meaning: str = "",
    media: list | str | None = None,
    test_data: bool = False,
    explicit_domain: list[str] | None = None,
    source_refs: list[dict] | None = None,
    quotes: list[dict] | None = None,
) -> str:
    metadata_fallback = False
    try:
        analysis = await rt.dehydrator.analyze(content)
    except Exception as e:
        metadata_fallback = True
        rt.logger.warning(
            "hold metadata analysis failed; preserving raw content with local defaults / "
            f"hold 打标失败，使用本地默认元数据并原样保存正文: {type(e).__name__}: {e}"
        )
        default_analysis = getattr(rt.dehydrator, "_default_analysis", None)
        analysis = default_analysis() if callable(default_analysis) else default_hold_analysis()

    metadata = normalize_hold_metadata(analysis, extra_tags, valence, arousal)
    final_domain = explicit_domain or metadata.domains
    final_valence = metadata.valence
    final_arousal = metadata.arousal
    all_tags = metadata.tags
    suggested_name = metadata.suggested_name
    final_title = title or normalize_memory_title(suggested_name)

    result_name, is_merged, embed_warn = await merge_or_create(
        content=content,
        tags=all_tags,
        importance=importance,
        domain=final_domain,
        valence=final_valence,
        arousal=final_arousal,
        name=suggested_name,
        title=final_title,
        source_refs=source_refs,
        quotes=quotes,
        raw_merge=True,
        why_remembered=why_remembered,
        source_tool="hold",
        meaning=meaning,
        media=media,
        test_data=test_data,
    )

    action = "合并→" if is_merged else "新建→"
    asyncio.create_task(check_plan_resolution(content, source_bucket_id=result_name))
    if not is_merged:
        asyncio.create_task(check_duplicate_for(result_name, content))
    result = f"{action}{result_name} {','.join(str(d) for d in final_domain if d is not None)}"
    if embed_warn:
        result += f"\n⚠️ {embed_warn}"
    if metadata_fallback:
        result += "\n⚠️ 打标 API 暂不可用：正文已逐字保存，未做任何压缩；元数据暂用本地中性值。"
    return result
