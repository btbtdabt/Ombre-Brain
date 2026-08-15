from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import tools._runtime as rt
from ombrebrain.storage.relation_store import (
    normalize_relation_label,
    normalize_relation_links,
    normalize_relation_type,
    relation_display_label,
    relation_hint,
)
from tools.dream.candidates import collect_candidates
from tools.relation_bindings import attach, detach, restore
from tools.relation_read import dispatch as relation_read


@pytest.mark.parametrize(
    ("relation_type", "expected"),
    [
        ("caused_by", "\u539f\u56e0"), ("causes", "\u7ed3\u679c"),
        ("continuation_of", "\u524d\u6bb5"), ("continues", "\u540e\u7eed"),
        ("related_to", "\u76f8\u5173"), ("same_event", "\u540c\u4e00\u4e8b\u4ef6"),
    ],
)
def test_relation_default_display_labels_and_custom_override(relation_type, expected):
    assert relation_display_label(relation_type, "") == expected
    assert relation_display_label(relation_type, "\u81ea\u5b9a\u4e49") == "\u81ea\u5b9a\u4e49"


def test_relation_normalizers_fail_closed_for_malformed_metadata():
    assert normalize_relation_label(None) == ""
    assert normalize_relation_label("x" * 20) == "x" * 20
    assert normalize_relation_type(" Causes ") == "causes"
    with pytest.raises(ValueError):
        normalize_relation_type("custom.type-1")
    for value in (True, 1, [], {}):
        with pytest.raises(ValueError):
            normalize_relation_label(value)
        with pytest.raises(ValueError):
            normalize_relation_type(value)
    for malformed in (
        {"target_bucket_id": [], "type": "causes", "label": "", "status": "active"},
        {"target_bucket_id": "b", "type": {}, "label": "", "status": "active"},
        {"target_bucket_id": "b", "type": "causes", "label": True, "status": "active"},
        {"target_bucket_id": "b", "type": "causes", "label": "", "status": True},
        {"target_bucket_id": "b", "type": "causes", "label": "x\ny", "status": "active"},
        {"target_bucket_id": "b", "type": "causes", "label": "x" * 21, "status": "active"},
    ):
        with pytest.raises(ValueError):
            normalize_relation_links([malformed])


def test_relation_hint_is_metadata_only_and_limited_to_two_rows():
    bucket = {"metadata": {"relation_links": [
        {"target_bucket_id": "b", "type": "caused_by", "label": "", "status": "active"},
        {"target_bucket_id": "c", "type": "continues", "label": "\u81ea\u5b9a\u4e49", "status": "active"},
        {"target_bucket_id": "d", "type": "same_event", "label": "", "status": "active"},
    ]}}
    hint = relation_hint(bucket)
    assert "\u539f\u56e0" in hint and "\u81ea\u5b9a\u4e49" in hint
    assert "\u540c\u4e00\u4e8b\u4ef6" not in hint and hint.count("\u2192") == 2


@pytest.mark.asyncio
async def test_relation_slots_direction_and_read_manifest_are_stable(bucket_mgr, monkeypatch):
    a = await bucket_mgr.create("A", title="A")
    b = await bucket_mgr.create("target hidden body", title="Sensitive target title")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    monkeypatch.setattr(rt, "logger", MagicMock(), raising=False)
    before = await bucket_mgr.get(a)

    assert "slot=1" in await attach(a, "A", b, "causes")
    assert "slot=1" in await attach(a, "A", b, "causes")
    assert "slot=2" in await attach(a, "A", b, "causes", "\u81ea\u5b9a\u4e49")
    assert "slot=3" in await attach(a, "A", b, "related_to")
    assert "detached" in await detach(a, "A", 1)
    assert "relation_restore" in await attach(a, "A", b, "causes")
    assert "detached" in await detach(a, "A", 1)
    assert "relation_detach ok" in await detach(a, "A", 1)
    assert "active" in await restore(a, "A", 1)
    assert "relation_restore ok" in await restore(a, "A", 1)

    manifest = await relation_read(a, "A")
    assert "slot=1" in manifest and "label=\u7ed3\u679c" in manifest
    assert "label=\u81ea\u5b9a\u4e49" in manifest and "target_bucket_id=" + b in manifest
    assert "Sensitive target title" not in manifest and "target hidden body" not in manifest
    target = await bucket_mgr.get(b)
    assert "relation_links" not in target["metadata"]  # A -> B never creates B -> A.
    after = await bucket_mgr.get(a)
    for key in ("last_active", "activation_count", "importance", "tags", "domain", "created"):
        assert after["metadata"].get(key) == before["metadata"].get(key)


@pytest.mark.asyncio
async def test_relation_rejects_self_and_special_buckets_but_allows_archived_ordinary(bucket_mgr, monkeypatch):
    ordinary = await bucket_mgr.create("ordinary", title="ordinary")
    target = await bucket_mgr.create("target", title="target")
    plan = await bucket_mgr.create("plan", title="plan", bucket_type="plan")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    invalid_target: Any = []
    assert "必须是字符串" in await attach(ordinary, "ordinary", invalid_target, "causes")
    assert "自环" in await attach(ordinary, "ordinary", ordinary, "causes")
    result = await attach(ordinary, "ordinary", plan, "related_to")
    assert "Relation V1" in result
    assert "slot=" not in result and "status=" not in result
    assert await bucket_mgr.archive(ordinary)
    archived = await bucket_mgr.get_including_archive(ordinary)
    assert archived["metadata"].get("type") == "archived"
    assert "slot=1" in await attach(ordinary, "ordinary", target, "related_to")
    assert "slot=1" in await relation_read(ordinary, "ordinary")


def test_dream_recent_candidates_only_include_ordinary_buckets():
    from datetime import datetime

    now = datetime.now().isoformat()
    def bucket(bucket_id, bucket_type):
        return {"id": bucket_id, "metadata": {"type": bucket_type, "last_active": now}}
    selected = collect_candidates([
        bucket("ordinary", "dynamic"), bucket("plan", "plan"),
        bucket("feel", "feel"), bucket("i", "i"),
    ], 48)
    assert [item["id"] for item in selected] == ["ordinary"]


@pytest.mark.parametrize("value", ["custom.type", "next", "related-to", "causes!", "same_event_1"])
def test_relation_type_rejects_safe_but_unsupported_values(value):
    with pytest.raises(ValueError):
        normalize_relation_type(value)


@pytest.mark.parametrize("value", ["\n", " \r\n ", "x" * 21])
def test_relation_label_rejects_raw_newlines_and_overlong_values(value):
    with pytest.raises(ValueError):
        normalize_relation_label(value)


@pytest.mark.asyncio
async def test_archived_special_bucket_relation_tools_leave_ledger_unchanged(bucket_mgr, monkeypatch):
    source = await bucket_mgr.create("source", title="source")
    target = await bucket_mgr.create("target", title="target")
    special = await bucket_mgr.create("special", title="special", bucket_type="plan")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    await bucket_mgr.mutate_relation_links(
        special,
        lambda post: (True, post.__setitem__("relation_links", [{
            "target_bucket_id": target, "type": "related_to", "label": "", "status": "active",
        }]) or "seed"),
    )
    assert await bucket_mgr.archive(special)
    before = (await bucket_mgr.get_including_archive(special))["metadata"]["relation_links"]
    assert "only permits ordinary" in await relation_read(special, "special")
    assert "only permits ordinary" in await detach(special, "special", 1)
    assert "only permits ordinary" in await restore(special, "special", 1)
    assert "only permits ordinary" in await attach(special, "special", source, "causes")
    after = (await bucket_mgr.get_including_archive(special))["metadata"]["relation_links"]
    assert after == before


@pytest.mark.asyncio
async def test_restore_at_active_limit_keeps_detached_slot(bucket_mgr, monkeypatch):
    source = await bucket_mgr.create("source", title="source")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    targets = [await bucket_mgr.create(f"target-{i}", title=f"target-{i}") for i in range(17)]
    for target in targets[:16]:
        assert "status=active" in await attach(source, "source", target, "related_to")
    await bucket_mgr.mutate_relation_links(
        source,
        lambda post: (True, post.__setitem__("relation_links", post.metadata["relation_links"] + [{
            "target_bucket_id": targets[16], "type": "causes", "label": "", "status": "detached",
        }]) or "seed"),
    )
    result = await restore(source, "source", 17)
    assert "relation_restore" in result and "16" in result
    links = (await bucket_mgr.get(source))["metadata"]["relation_links"]
    assert len(links) == 17 and links[16]["status"] == "detached"


@pytest.mark.asyncio
async def test_restore_rejects_a_missing_target_and_keeps_slot_detached(
    bucket_mgr,
    monkeypatch,
):
    source = await bucket_mgr.create("source", title="source")
    target = await bucket_mgr.create("target", title="target")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    assert "status=active" in await attach(source, "source", target, "related_to")
    assert "status=detached" in await detach(source, "source", 1)

    target_file = bucket_mgr._find_bucket_file(target)
    assert target_file is not None
    original_mutate = bucket_mgr.mutate_relation_links

    async def remove_target_after_preflight(
        candidate_id: str,
        mutation: Any,
        **kwargs: Any,
    ):
        Path(target_file).unlink()
        return await original_mutate(candidate_id, mutation, **kwargs)

    monkeypatch.setattr(
        bucket_mgr,
        "mutate_relation_links",
        remove_target_after_preflight,
    )
    result = await restore(source, "source", 1)

    assert "target 必须真实存在" in result
    stored_source = await bucket_mgr.get(source)
    assert stored_source is not None
    links = stored_source["metadata"]["relation_links"]
    assert links[0]["status"] == "detached"


@pytest.mark.asyncio
async def test_attach_revalidates_target_inside_dual_bucket_turn(
    bucket_mgr,
    monkeypatch,
):
    source = await bucket_mgr.create("source", title="source")
    target = await bucket_mgr.create("target", title="target")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    target_file = bucket_mgr._find_bucket_file(target)
    assert target_file is not None
    original_mutate = bucket_mgr.mutate_relation_links

    async def remove_target_after_preflight(
        candidate_id: str,
        mutation: Any,
        **kwargs: Any,
    ):
        Path(target_file).unlink()
        return await original_mutate(candidate_id, mutation, **kwargs)

    monkeypatch.setattr(
        bucket_mgr,
        "mutate_relation_links",
        remove_target_after_preflight,
    )
    result = await attach(source, "source", target, "related_to")

    assert "target 必须真实存在" in result
    stored_source = await bucket_mgr.get(source)
    assert stored_source is not None
    assert "relation_links" not in stored_source["metadata"]


def test_dream_relation_hint_is_separated_from_footprint(monkeypatch):
    from tools.dream.output import format_dream_output

    class Snapshot:
        def summary(self, _bucket_id, _metadata):
            return "FOOTPRINT"

    class Manager:
        def footprint_snapshot(self):
            return Snapshot()

    monkeypatch.setattr(rt, "bucket_mgr", Manager(), raising=False)
    monkeypatch.setattr(rt, "config", {}, raising=False)
    bucket = {"id": "source", "content": "body", "metadata": {
        "name": "source", "type": "dynamic", "domain": [], "relation_links": [{
            "target_bucket_id": "target-id", "type": "continues", "label": "", "status": "active",
        }],
    }}
    output = format_dream_output([bucket], [bucket], 48, "", "")
    assert "target-id\nFOOTPRINT" in output
