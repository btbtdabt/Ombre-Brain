"""
========================================
tools/anchor/core.py — anchor / release / pulse 实现
========================================

anchor 是 iter 2.0 引入的「坐标系桶」概念：把某条已经存在的桶钉为
我们关系/身份的基准点。它不会主动浮现在默认 breath，但 query/domain/
emotion/importance_min 命中时仍能返回。硬上限 24 个。

pulse 顺带放在这里：它是系统状态 + 桶清单的总览，调用频次低，把它
塞进一个文件不影响阅读。

关键行为：
- anchor_set / anchor_release：调 bucket_mgr.set_anchor，原样转译结果
- pulse：聚合 stats + list_all，按 type 分组（normal/feel/plan/letter）
  逐行展示 icon + 主题 + 情感 + 权重 + 标签
- pulse 同时附带「索引漂移」自检：embedding.db 的 ID 集合与磁盘桶 ID 集合
  对账，缺失/孤儿 > 0 时在状态块顶部告警，提示运行 backfill / clean 脚本

不做什么（边界）：
- anchor 没有「创建快捷键」：必须先 hold() 写下，确认是坐标系再钉
- pulse 不做 dehydrate：只读元数据，避免大开销

对外暴露：anchor_set(bucket_id) / anchor_release(bucket_id) /
         pulse(include_archive) → str
========================================
"""

from typing import Optional

from .. import _runtime as rt
from .._common import check_metadata_size


async def anchor_set(bucket_id: str) -> str:
    bucket_id = "" if bucket_id is None else str(bucket_id)
    metadata_err = check_metadata_size(bucket_id=bucket_id)
    if metadata_err:
        return metadata_err
    if rt.mark_op:
        rt.mark_op("anchor")
    result = await rt.bucket_mgr.set_anchor(bucket_id, True)
    if not result["ok"]:
        return f"我没能把它锚住。{result.get('error', '未知错误')} 当前 anchor: {result.get('count', '?')}/{result.get('limit', 24)}。"
    if result.get("noop"):
        return f"它已经是 anchor 了。当前 {result['count']}/{result['limit']}。"
    return f"我把它放进 anchor 了。它现在是坐标系的一部分，不会被默认浮现挤进上下文。当前 {result['count']}/{result['limit']}。"


async def anchor_release(bucket_id: str) -> str:
    bucket_id = "" if bucket_id is None else str(bucket_id)
    metadata_err = check_metadata_size(bucket_id=bucket_id)
    if metadata_err:
        return metadata_err
    if rt.mark_op:
        rt.mark_op("release")
    result = await rt.bucket_mgr.set_anchor(bucket_id, False)
    if not result["ok"]:
        return f"释放失败。{result.get('error', '未知错误')}"
    if result.get("noop"):
        return f"它本来就不是 anchor。当前 {result['count']}/{result['limit']}。"
    return f"我把它从 anchor 移开了。它会重新参与默认浮现。当前 {result['count']}/{result['limit']}。"


async def pulse(include_archive: Optional[bool] = False) -> str:
    from ..current.memory import pulse as current_pulse

    return await current_pulse(include_archive=bool(include_archive))
