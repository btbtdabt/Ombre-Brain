from __future__ import annotations

import asyncio
import errno
import os
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

import tools._runtime as rt
from ombrebrain.storage.source_store import (
    MAX_SOURCE_LINKS,
    MAX_SOURCE_REFS,
    SourceStore,
    normalize_source_links,
    normalize_source_ranges,
    referenced_source_ids_from_markdown,
)
from tools.source_bindings import (
    attach as source_attach,
    detach as source_detach,
    restore as source_restore,
)
from tools.source_read import dispatch as source_read
from tools.hold import dispatch as hold
from utils import count_tokens_approx


def test_source_store_is_content_addressed_and_verifies_integrity(tmp_path):
    store = SourceStore(tmp_path)
    ref = store.put("第一行\n第二行\n第三行\n")
    assert store.put("第一行\n第二行\n第三行\n") == ref
    assert len(list((tmp_path / "_sources").glob("*.source"))) == 1
    assert store.read(ref) == "第一行\n第二行\n第三行\n"

    (tmp_path / "_sources" / f"{ref}.source").write_text("被篡改", encoding="utf-8")
    with pytest.raises(OSError, match="完整性"):
        store.read(ref)


def test_source_ranges_are_normalized_and_selected(tmp_path):
    store = SourceStore(tmp_path)
    ranges = normalize_source_ranges([[3, 3], [1, 2], [5, 5]])
    assert ranges == [[1, 3], [5, 5]]
    assert store.select_ranges("一\n二\n三\n四\n五\n", ranges) == "一\n二\n三\n五\n"
    with pytest.raises(ValueError, match="超出"):
        store.select_ranges("一\n二\n", [[2, 3]])


def test_source_links_reject_duplicate_bindings():
    ref = "src_" + "a" * 64
    duplicate = {"ref": ref, "ranges": [[1, 2]], "status": "active"}
    with pytest.raises(ValueError, match="重复绑定"):
        normalize_source_links([duplicate, dict(duplicate, status="detached")])


@pytest.mark.parametrize("invalid", [[[True, 1]], [[1.5, 2]], [["1", 2]]])
def test_source_ranges_reject_non_integer_line_numbers(invalid):
    with pytest.raises(ValueError, match="行号必须是整数"):
        normalize_source_ranges(invalid)


def test_source_store_falls_back_to_atomic_publish_without_hardlinks(
    tmp_path, monkeypatch
):
    store = SourceStore(tmp_path)

    def unsupported_link(*_args, **_kwargs):
        raise OSError(errno.EOPNOTSUPP, "hard links unsupported")

    monkeypatch.setattr(os, "link", unsupported_link)
    ref = store.put("NAS 上也必须原子发布")

    assert store.read(ref) == "NAS 上也必须原子发布"
    assert not list((tmp_path / "_sources").glob(".source-*"))


@pytest.mark.asyncio
async def test_source_read_requires_exact_bucket_and_title(
    bucket_mgr, monkeypatch
):
    store = SourceStore(bucket_mgr.base_dir)
    ref = store.put("开场\nwife 喔，不是 girlfriend 喔。\n直接 wife。\n尾声\n")
    bucket_id = await bucket_mgr.create(
        content="她注意到我直接用了 wife，我们就这个称呼笑了一阵。",
        title="wife",
        source_refs=[{"ref": ref, "ranges": [[2, 3]]}],
    )
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    monkeypatch.setattr(rt, "source_store", store, raising=False)
    monkeypatch.setattr(rt, "logger", MagicMock(), raising=False)

    denied = await source_read(bucket_id, "直接确认关系")
    assert "标题不匹配" in denied

    event = await source_read(bucket_id, "wife", scope="event")
    assert "wife 喔" in event
    assert "直接 wife" in event
    assert "开场" not in event
    assert "尾声" not in event

    full = await source_read(bucket_id, "wife", scope="full_source")
    assert "开场" in full and "尾声" in full


@pytest.mark.asyncio
async def test_source_read_pages_without_silent_truncation(bucket_mgr, monkeypatch):
    store = SourceStore(bucket_mgr.base_dir)
    original = "段落内容。" * 3000
    ref = store.put(original)
    bucket_id = await bucket_mgr.create(
        content="分页测试正文",
        title="分页测试",
        source_refs=[{"ref": ref, "ranges": []}],
    )
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    monkeypatch.setattr(rt, "source_store", store, raising=False)

    first = await source_read(
        bucket_id, "分页测试", scope="full_source", max_tokens=300
    )
    assert "next_cursor=0" not in first
    next_cursor = int(first.split("next_cursor=", 1)[1].splitlines()[0])
    second = await source_read(
        bucket_id,
        "分页测试",
        scope="full_source",
        cursor=next_cursor,
        max_tokens=300,
    )
    assert f"cursor={next_cursor}" in second
    assert count_tokens_approx(first) <= 300
    assert count_tokens_approx(second) <= 300


@pytest.mark.asyncio
async def test_event_without_ranges_never_falls_back_to_full_source(
    bucket_mgr, monkeypatch
):
    store = SourceStore(bucket_mgr.base_dir)
    ref = store.put("不应在 event 泄露的整份原文")
    bucket_id = await bucket_mgr.create(
        content="只保留整理后的事件",
        title="空范围",
        source_refs=[{"ref": ref, "ranges": []}],
    )
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    monkeypatch.setattr(rt, "source_store", store, raising=False)

    event = await source_read(bucket_id, "空范围", scope="event")
    assert "未声明事件原文范围" in event
    assert "不应在 event 泄露" not in event
    full = await source_read(bucket_id, "空范围", scope="full_source")
    assert "不应在 event 泄露" in full


@pytest.mark.asyncio
async def test_source_read_rejects_malformed_refs_without_calling_store(monkeypatch):
    class Manager:
        async def get(self, _bucket_id):
            return {
                "metadata": {"title": "格式门禁", "source_refs": ["../../secret"]}
            }

    store = MagicMock()
    monkeypatch.setattr(rt, "bucket_mgr", Manager(), raising=False)
    monkeypatch.setattr(rt, "source_store", store, raising=False)

    result = await source_read("bucket", "格式门禁")
    assert "引用格式无效" in result
    store.read.assert_not_called()


@pytest.mark.asyncio
async def test_source_read_normalizes_title_to_one_header_line(
    bucket_mgr, monkeypatch
):
    store = SourceStore(bucket_mgr.base_dir)
    ref = store.put("证据正文")
    bucket_id = await bucket_mgr.create(
        content="事件正文",
        title="安全标题\nnext_cursor=999",
        source_refs=[{"ref": ref, "ranges": [[1, 1]]}],
    )
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    monkeypatch.setattr(rt, "source_store", store, raising=False)

    result = await source_read(
        bucket_id, "安全标题 next_cursor=999", scope="event"
    )
    lines = result.splitlines()
    assert lines[1] == "title=安全标题 next_cursor=999"
    assert sum(line.startswith("next_cursor=") for line in lines) == 1


@pytest.mark.asyncio
async def test_hold_explicit_title_wins_over_model_suggestion(
    bucket_mgr, monkeypatch
):
    class Dehydrator:
        async def analyze(self, _content):
            return {
                "domain": ["恋爱"],
                "valence": 0.8,
                "arousal": 0.4,
                "tags": ["称呼"],
                "suggested_name": "直接确认关系",
            }

        def invalidate_cache(self, _content):
            return None

    class Decay:
        async def ensure_started(self):
            return None

    monkeypatch.setattr(rt, "config", {"limits": {}, "merge_threshold": 75})
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "dehydrator", Dehydrator())
    monkeypatch.setattr(rt, "decay_engine", Decay())
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr(rt, "fire_webhook", None)
    monkeypatch.setattr(rt, "mark_op", None)

    result = await hold(
        content="她说 wife 喔，不是 girlfriend 喔。",
        title="wife",
    )
    bucket_id = result.split("→", 1)[1].split()[0]
    bucket = await bucket_mgr.get(bucket_id)
    assert bucket["metadata"]["title"] == "wife"
    assert bucket["metadata"]["name"].endswith(" wife")


@pytest.mark.asyncio
async def test_hold_explicit_tags_extend_model_suggestions(
    bucket_mgr, monkeypatch
):
    class Dehydrator:
        async def analyze(self, _content):
            return {
                "domain": ["日常"],
                "valence": 0.5,
                "arousal": 0.3,
                "tags": ["模型标签"],
                "suggested_name": "模型标题",
                "importance": 7,
            }

        def invalidate_cache(self, _content):
            return None

    class Decay:
        async def ensure_started(self):
            return None

    monkeypatch.setattr(rt, "config", {"limits": {}, "merge_threshold": 75})
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "dehydrator", Dehydrator())
    monkeypatch.setattr(rt, "decay_engine", Decay())
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr(rt, "fire_webhook", None)
    monkeypatch.setattr(rt, "mark_op", None)

    result = await hold(
        content="人工标签优先。",
        tags=cast(Any, ["人工标签"]),
    )
    bucket_id = result.split("→", 1)[1].split()[0]
    bucket = await bucket_mgr.get(bucket_id)
    assert bucket["metadata"]["tags"] == ["模型标签", "人工标签"]
    assert bucket["metadata"]["title"] == "模型标题"


@pytest.mark.asyncio
async def test_hold_explicit_domain_wins_over_model_suggestion(
    bucket_mgr, monkeypatch
):
    class Dehydrator:
        async def analyze(self, _content):
            return {
                "domain": ["模型域"],
                "valence": 0.5,
                "arousal": 0.3,
                "tags": [],
                "suggested_name": "模型标题",
            }

        def invalidate_cache(self, _content):
            return None

    class Decay:
        async def ensure_started(self):
            return None

    monkeypatch.setattr(rt, "config", {"limits": {}, "merge_threshold": 75})
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "dehydrator", Dehydrator())
    monkeypatch.setattr(rt, "decay_engine", Decay())
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr(rt, "fire_webhook", None)
    monkeypatch.setattr(rt, "mark_op", None)

    result = await hold(content="人工 domain 优先。", domain="人工域")
    bucket_id = result.split("→", 1)[1].split()[0]
    bucket = await bucket_mgr.get(bucket_id)
    assert bucket["metadata"]["domain"] == ["人工域"]


@pytest.mark.asyncio
async def test_hold_optional_source_content_uses_shared_source_layer(
    bucket_mgr, monkeypatch
):
    class Dehydrator:
        async def analyze(self, _content):
            return {
                "domain": ["旅行"],
                "valence": 0.8,
                "arousal": 0.5,
                "tags": ["京都"],
                "suggested_name": "京都计划",
            }

        def invalidate_cache(self, _content):
            return None

    class Decay:
        async def ensure_started(self):
            return None

    store = SourceStore(bucket_mgr.base_dir)
    monkeypatch.setattr(rt, "config", {"limits": {}, "merge_threshold": 75})
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "dehydrator", Dehydrator())
    monkeypatch.setattr(rt, "decay_engine", Decay())
    monkeypatch.setattr(rt, "source_store", store)
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr(rt, "fire_webhook", None)
    monkeypatch.setattr(rt, "mark_op", None)

    source = "第一行：讨论目的地\n第二行：决定去京都\n第三行：约定时间\n"
    existing_ref = store.put(source)
    result = await hold(
        content="我们决定下个月一起去京都。",
        title="京都计划",
        source_content=source,
    )
    bucket_id = result.split("→", 1)[1].split()[0]
    bucket = await bucket_mgr.get(bucket_id)
    refs = bucket["metadata"]["source_refs"]

    assert refs[0]["ref"] == existing_ref
    assert len(list((Path(bucket_mgr.base_dir) / "_sources").glob("*.source"))) == 1
    assert refs[0]["ranges"] == [[1, 3]]
    assert store.read(refs[0]["ref"]) == source

    event = await source_read(bucket_id, "京都计划", scope="event")
    assert "第一行：讨论目的地" in event
    assert "第三行：约定时间" in event


@pytest.mark.asyncio
async def test_hold_source_ranges_select_event_and_merge_appends_evidence(
    bucket_mgr, monkeypatch
):
    class Dehydrator:
        async def analyze(self, _content):
            return {
                "domain": ["旅行"],
                "valence": 0.8,
                "arousal": 0.5,
                "tags": [],
                "suggested_name": "去了京都",
            }

        def invalidate_cache(self, _content):
            return None

    class Decay:
        async def ensure_started(self):
            return None

    store = SourceStore(bucket_mgr.base_dir)
    monkeypatch.setattr(rt, "config", {"limits": {}, "merge_threshold": 75})
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "dehydrator", Dehydrator())
    monkeypatch.setattr(rt, "decay_engine", Decay())
    monkeypatch.setattr(rt, "source_store", store)
    monkeypatch.setattr(rt, "logger", MagicMock())
    monkeypatch.setattr(rt, "fire_webhook", None)
    monkeypatch.setattr(rt, "mark_op", None)

    memory = "我们今天真的去了京都。"
    first = await hold(
        content=memory,
        title="去了京都",
        source_content="前情\n今天到了京都\n去了清水寺\n收尾\n",
        source_ranges=[[2, 3]],
    )
    bucket_id = first.split("→", 1)[1].split()[0]
    event = await source_read(bucket_id, "去了京都", scope="event")
    assert "今天到了京都" in event
    assert "去了清水寺" in event
    assert "前情" not in event
    assert "收尾" not in event

    second = await hold(
        content=memory,
        title="去了京都",
        source_content="另一段独立原话",
    )
    assert second.startswith("合并→")
    bucket = await bucket_mgr.get(bucket_id)
    assert len(bucket["metadata"]["source_refs"]) == 2


@pytest.mark.asyncio
async def test_hold_source_ranges_require_source_content(bucket_mgr, monkeypatch):
    class Decay:
        async def ensure_started(self):
            return None

    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "decay_engine", Decay())
    monkeypatch.setattr(rt, "mark_op", None)

    result = await hold(
        content="这条不应该写进去。",
        title="无原文",
        source_ranges=[[1, 1]],
    )
    assert "source_ranges 需要同时提供 source_content" in result
    assert await bucket_mgr.list_all() == []


@pytest.mark.asyncio
async def test_source_link_slots_are_reversible_and_read_selectively(
    bucket_mgr,
    monkeypatch,
):
    store = SourceStore(bucket_mgr.base_dir)
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "source_store", store)
    bucket_id = await bucket_mgr.create(
        "memory",
        title="evidence",
        tags=["keep"],
        importance=7,
    )
    before = await bucket_mgr.get(bucket_id)

    assert "slot=1" in await source_attach(bucket_id, "evidence", "alpha\n")
    assert "slot=2" in await source_attach(bucket_id, "evidence", "beta\n")
    assert "ranges=1-1,3-3" in await source_attach(
        bucket_id,
        "evidence",
        "one\ntwo\nthree\n",
        [[3, 3], [1, 1]],
    )
    manifest = await source_read(bucket_id, "evidence")
    assert "slot=1" in manifest and "alpha" not in manifest
    assert "alpha" in await source_read(bucket_id, "evidence", source_slots=[1])
    assert "beta" in await source_read(bucket_id, "evidence", source_slots=[2])
    both = await source_read(bucket_id, "evidence", source_slots=[2, 1])
    assert both.index("alpha") < both.index("beta")
    all_sources = await source_read(bucket_id, "evidence", all_sources=True)
    assert "alpha" in all_sources and "beta" in all_sources
    selected = await source_read(bucket_id, "evidence", source_slots=[3, 1, 3])
    assert selected.index("alpha") < selected.index("one")
    assert selected.count("one") == 1
    assert "detached" in await source_detach(bucket_id, "evidence", 1)
    assert "source_detach ok" in await source_detach(bucket_id, "evidence", 1)
    assert "source_restore" in await source_attach(
        bucket_id,
        "evidence",
        "alpha\n",
    )
    assert "detached" in await source_read(
        bucket_id,
        "evidence",
        source_slots=[1],
    )
    assert "active" in await source_restore(bucket_id, "evidence", 1)
    assert "source_restore ok" in await source_restore(bucket_id, "evidence", 1)

    after = await bucket_mgr.get(bucket_id)
    assert [item["status"] for item in after["metadata"]["source_links"]] == [
        "active",
        "active",
        "active",
    ]
    assert [item["ref"] for item in after["metadata"]["source_refs"]] == [
        item["ref"] for item in after["metadata"]["source_links"]
    ]
    for field in (
        "content",
        "tags",
        "importance",
        "created",
        "last_active",
        "activation_count",
    ):
        if field == "content":
            assert after[field] == before[field]
        else:
            assert after["metadata"][field] == before["metadata"][field]


@pytest.mark.asyncio
async def test_source_attach_preflights_access_before_publishing_blob(
    bucket_mgr,
    monkeypatch,
):
    store = SourceStore(bucket_mgr.base_dir)
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "source_store", store)

    bucket_id = await bucket_mgr.create("正文", title="精确标题")
    assert "标题不匹配" in await source_attach(
        bucket_id,
        "错误标题",
        "不应落盘",
    )
    assert "未找到桶" in await source_attach("missing", "精确标题", "不应落盘")

    locked_id = await bucket_mgr.create(
        "信件正文",
        title="锁定信件",
        bucket_type="letter",
        lock_type="permanent",
        locked_by="human",
    )
    assert "拒绝" in await source_attach(locked_id, "锁定信件", "不应落盘")
    assert not list((Path(bucket_mgr.base_dir) / "_sources").glob("*.source"))


@pytest.mark.asyncio
async def test_concurrent_source_attach_does_not_publish_rejected_blob(
    bucket_mgr,
    monkeypatch,
):
    store = SourceStore(bucket_mgr.base_dir)
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "source_store", store)
    existing_refs = [
        {"ref": f"src_{index:064x}", "ranges": [[1, 1]]}
        for index in range(1, MAX_SOURCE_REFS)
    ]
    bucket_id = await bucket_mgr.create(
        "正文",
        title="并发证据",
        source_refs=existing_refs,
    )

    original_get = bucket_mgr.get_including_archive
    ready = asyncio.Event()
    arrivals = 0

    async def synchronized_get(candidate_id: str):
        nonlocal arrivals
        bucket = await original_get(candidate_id)
        arrivals += 1
        if arrivals == 2:
            ready.set()
        await ready.wait()
        return bucket

    monkeypatch.setattr(bucket_mgr, "get_including_archive", synchronized_get)
    results = await asyncio.gather(
        source_attach(bucket_id, "并发证据", "并发原文 A"),
        source_attach(bucket_id, "并发证据", "并发原文 B"),
    )

    assert sum("source_attach ok" in result for result in results) == 1
    assert sum("活动 source_refs 上限" in result for result in results) == 1
    assert len(list((Path(bucket_mgr.base_dir) / "_sources").glob("*.source"))) == 1


@pytest.mark.asyncio
async def test_source_links_are_immutable_shared_and_source_mutations_skip_indexing(
    bucket_mgr,
    monkeypatch,
):
    store = SourceStore(bucket_mgr.base_dir)
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "source_store", store)
    index_after_update = AsyncMock()
    monkeypatch.setattr(bucket_mgr, "_index_after_update", index_after_update)

    first_id = await bucket_mgr.create("A", title="A")
    second_id = await bucket_mgr.create("B", title="B")
    index_after_update.reset_mock()
    assert "slot=1" in await source_attach(first_id, "A", "shared\n")
    assert "slot=1" in await source_attach(second_id, "B", "shared\n")
    assert "slot=1" in await source_attach(first_id, "A", "shared\n")

    first = await bucket_mgr.get(first_id)
    second = await bucket_mgr.get(second_id)
    assert first["metadata"]["source_links"][0]["ref"] == second["metadata"][
        "source_links"
    ][0]["ref"]
    assert len(list((Path(bucket_mgr.base_dir) / "_sources").glob("*.source"))) == 1
    assert await source_detach(first_id, "A", 1)
    detached_manifest = await source_read(first_id, "A")
    assert "source manifest" in detached_manifest
    assert "slot=1" in detached_manifest
    assert "detached" in detached_manifest
    assert "shared" in await source_read(second_id, "B")
    assert len(list((Path(bucket_mgr.base_dir) / "_sources").glob("*.source"))) == 1
    assert "active" in await source_restore(first_id, "A", 1)
    assert index_after_update.await_count == 0


@pytest.mark.asyncio
async def test_source_links_support_legacy_projection_and_append_does_not_revive(
    bucket_mgr,
    monkeypatch,
):
    import frontmatter

    store = SourceStore(bucket_mgr.base_dir)
    ref = store.put("legacy\n")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "source_store", store)
    bucket_id = await bucket_mgr.create(
        "正文",
        title="legacy",
        source_refs=[{"ref": ref, "ranges": [[1, 1]]}],
    )
    path = Path((await bucket_mgr.get(bucket_id))["path"])
    post = frontmatter.load(path)
    post.metadata.pop("source_links", None)
    path.write_text(frontmatter.dumps(post), encoding="utf-8")

    assert "legacy" in await source_read(bucket_id, "legacy")
    assert "detached" in await source_detach(bucket_id, "legacy", 1)
    assert await bucket_mgr.update(
        bucket_id,
        source_refs_append=[{"ref": ref, "ranges": [[1, 1]]}],
    )
    latest = await bucket_mgr.get(bucket_id)
    assert latest["metadata"]["source_links"][0]["status"] == "detached"


@pytest.mark.asyncio
async def test_source_binding_operations_preserve_archived_and_locked_lifecycle(
    bucket_mgr,
    monkeypatch,
):
    store = SourceStore(bucket_mgr.base_dir)
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "source_store", store)

    archived_id = await bucket_mgr.create("正文", title="归档证据")
    assert await bucket_mgr.archive(archived_id)
    before = await bucket_mgr.get_including_archive(archived_id)
    assert before["metadata"]["type"] == "archived"
    assert "slot=1" in await source_attach(archived_id, "归档证据", "archive\n")
    assert "slot=1" in await source_detach(archived_id, "归档证据", 1)
    assert "active" in await source_restore(archived_id, "归档证据", 1)
    after = await bucket_mgr.get_including_archive(archived_id)
    assert after["path"] == before["path"]
    assert after["metadata"].get("type") == before["metadata"].get("type")
    assert after["metadata"].get("deleted_at") == before["metadata"].get(
        "deleted_at"
    )

    ref = store.put("locked\n")
    locked_id = await bucket_mgr.create(
        "信件正文",
        title="锁定证据",
        bucket_type="letter",
        lock_type="permanent",
        locked_by="human",
        source_refs=[{"ref": ref, "ranges": [[1, 1]]}],
    )
    assert "拒绝" in await source_detach(locked_id, "锁定证据", 1)
    assert "拒绝" in await source_restore(locked_id, "锁定证据", 1)


def test_source_evidence_closure_unions_and_validates_both_metadata_fields():
    first = "src_" + "1" * 64
    second = "src_" + "2" * 64
    markdown = (
        "---\n"
        f"source_refs:\n  - ref: {first}\n    ranges: []\n"
        "source_links:\n"
        f"  - ref: {second}\n    ranges: []\n    status: detached\n"
        "---\nbody\n"
    )
    assert referenced_source_ids_from_markdown(markdown) == {first, second}

    malformed = markdown.replace(first, "not-a-source-ref")
    with pytest.raises(ValueError):
        referenced_source_ids_from_markdown(malformed)


@pytest.mark.asyncio
async def test_source_binding_caps_reject_without_eviction(bucket_mgr, monkeypatch):
    store = SourceStore(bucket_mgr.base_dir)
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "source_store", store)

    active_id = await bucket_mgr.create("正文", title="活动上限")
    for index in range(MAX_SOURCE_REFS):
        assert "status=active" in await source_attach(
            active_id,
            "活动上限",
            f"active-{index}\n",
        )
    source_files_before = len(
        list((Path(bucket_mgr.base_dir) / "_sources").glob("*.source"))
    )
    rejected = await source_attach(active_id, "活动上限", "active-overflow\n")
    assert "上限" in rejected
    assert len((await bucket_mgr.get(active_id))["metadata"]["source_links"]) == (
        MAX_SOURCE_REFS
    )
    assert len(list((Path(bucket_mgr.base_dir) / "_sources").glob("*.source"))) == (
        source_files_before
    )

    total_id = await bucket_mgr.create("正文", title="总量上限")
    ref = store.put("cap\n")
    links = [
        {"ref": ref, "ranges": [[index, index]], "status": "detached"}
        for index in range(1, MAX_SOURCE_LINKS + 1)
    ]

    def seed(post):
        post["source_links"] = links
        post["source_refs"] = []
        return True, "seeded"

    await bucket_mgr.mutate_source_links(total_id, seed)
    source_files_before = len(
        list((Path(bucket_mgr.base_dir) / "_sources").glob("*.source"))
    )
    rejected = await source_attach(total_id, "总量上限", "total-overflow\n")
    assert "上限" in rejected
    assert len((await bucket_mgr.get(total_id))["metadata"]["source_links"]) == (
        MAX_SOURCE_LINKS
    )
    assert len(list((Path(bucket_mgr.base_dir) / "_sources").glob("*.source"))) == (
        source_files_before
    )


@pytest.mark.asyncio
async def test_title_over_limit_is_rejected_before_hold_writes(bucket_mgr, monkeypatch):
    class Decay:
        async def ensure_started(self):
            return None

    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(rt, "decay_engine", Decay())
    monkeypatch.setattr(rt, "mark_op", None)

    result = await hold(content="不能半截标题", title="长" * 121)
    assert "120" in result
    assert await bucket_mgr.list_all() == []


@pytest.mark.asyncio
async def test_bucket_manager_never_silently_truncates_explicit_title(bucket_mgr):
    valid_title = "标" * 120
    bucket_id = await bucket_mgr.create(content="正文", title=valid_title)
    assert (await bucket_mgr.get(bucket_id))["metadata"]["title"] == valid_title

    with pytest.raises(ValueError, match="120"):
        await bucket_mgr.update(bucket_id, title="越" * 121)
    assert (await bucket_mgr.get(bucket_id))["metadata"]["title"] == valid_title

    with pytest.raises(ValueError, match="title 不能为空"):
        await bucket_mgr.update(bucket_id, title="")
    assert (await bucket_mgr.get(bucket_id))["metadata"]["title"] == valid_title
