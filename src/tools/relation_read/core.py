from ombrebrain.storage.relation_store import normalize_relation_links, relation_display_label
from .. import _runtime as rt
from ..source_read.core import _normalized_title
from ..relation_bindings.core import _ordinary


async def dispatch(bucket_id: str, expected_title: str) -> str:
    bucket_id = str(bucket_id or '').strip()
    expected_title = _normalized_title(expected_title)
    bucket = await getattr(rt.bucket_mgr, 'get_including_archive', rt.bucket_mgr.get)(bucket_id)
    if not bucket:
        return f"未找到桶 {bucket_id}。"
    meta = bucket.get('metadata') or {}
    if not _ordinary(bucket, bucket_id):
        return 'Relation V1 only permits ordinary memory buckets.'
    if not _ordinary(bucket):
        return 'Relation V1 只允许普通记忆桶。'
    if not expected_title or _normalized_title(meta.get('title')) != expected_title:
        return '标题不匹配，拒绝读取 Relation。'
    try:
        links = normalize_relation_links(meta.get('relation_links'))
    except ValueError:
        return '该桶的 relation_links 格式无效，拒绝读取。'
    if not links:
        return 'relation manifest：没有关系。'
    return 'relation manifest\n' + '\n'.join(
        f"slot={i} | type={x['type']} | label={relation_display_label(x['type'], x['label'])} | target_bucket_id={x['target_bucket_id']} | {x['status']}"
        for i, x in enumerate(links, 1)
    )
