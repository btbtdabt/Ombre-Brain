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
    reverse_relation_type,
)
from tools.dream.candidates import collect_candidates
from tools.relation_bindings import attach, detach, restore
from tools.relation_read import dispatch as relation_read


@pytest.mark.parametrize(
    ("relation_type", "expected", "reverse"),
    [
        ("caused_by", "\u539f\u56e0", "causes"),
        ("causes", "\u7ed3\u679c", "caused_by"),
        ("continuation_of", "\u524d\u6bb5", "continues"),
        ("continues", "\u540e\u7eed", "continuation_of"),
        ("related_to", "\u76f8\u5173", "related_to"),
        ("same_event", "\u540c\u4e00\u4e8b\u4ef6", "same_event"),
    ],
)
def test_relation_fixed_display_labels_and_reverse_mapping(
    relation_type, expected, reverse
):
    assert relation_display_label(relation_type, "") == expected
    assert reverse_relation_type(relation_type) == reverse


def test_relation_custom_display_and_reverse_are_symmetric():
    assert normalize_relation_type(" custom ") == "custom"
    assert reverse_relation_type("custom") == "custom"
    assert (
        relation_display_label("custom", "\u540c\u4e00\u6b21\u5207\u6362")
        == "\u540c\u4e00\u6b21\u5207\u6362"
    )


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
        {"target_bucket_id": "b", "type": "custom", "label": "", "status": "active"},
        {
            "target_bucket_id": "b",
            "type": "causes",
            "label": "",
            "status": "active",
            "relation_id": [],
        },
    ):
        with pytest.raises(ValueError):
            normalize_relation_links([malformed])


def test_relation_hint_is_metadata_only_limited_and_reports_hidden_count():
    bucket = {
        "metadata": {
            "relation_links": [
                {"target_bucket_id": "b", "type": "caused_by", "label": "", "status": "active"},
                {
                    "target_bucket_id": "c",
                    "type": "custom",
                    "label": "\u540c\u4e00\u6b21\u5207\u6362",
                    "status": "active",
                },
                {"target_bucket_id": "d", "type": "same_event", "label": "", "status": "active"},
                {"target_bucket_id": "e", "type": "causes", "label": "", "status": "detached"},
            ]
        }
    }
    hint = relation_hint(bucket)
    assert "\u539f\u56e0" in hint and "\u540c\u4e00\u6b21\u5207\u6362" in hint
    assert "\u540c\u4e00\u4e8b\u4ef6" not in hint and hint.count("\u2192") == 2
    assert "\u53e6\u6709 1 \u6761 relation" in hint


@pytest.mark.asyncio
async def test_fixed_relation_attach_is_id_only_and_naturally_bidirectional(
    bucket_mgr, monkeypatch
):
    cause = await bucket_mgr.create("cause body", title="Cause title")
    effect = await bucket_mgr.create("effect body", title="Effect title")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    monkeypatch.setattr(rt, "logger", MagicMock(), raising=False)
    before_cause = await bucket_mgr.get(cause)
    before_effect = await bucket_mgr.get(effect)

    result = await attach(cause, effect, "causes")
    assert "slot=1" in result
    assert "target_slot=1" in result
    assert "relation_id=rel_" in result

    cause_links = (await bucket_mgr.get(cause))["metadata"]["relation_links"]
    effect_links = (await bucket_mgr.get(effect))["metadata"]["relation_links"]
    assert cause_links[0]["type"] == "causes"
    assert effect_links[0]["type"] == "caused_by"
    assert cause_links[0]["target_bucket_id"] == effect
    assert effect_links[0]["target_bucket_id"] == cause
    assert cause_links[0]["relation_id"] == effect_links[0]["relation_id"]
    assert cause_links[0]["label"] == effect_links[0]["label"] == ""

    manifest = await relation_read(cause)
    assert "active=1" in manifest and "type=causes" in manifest
    assert "Effect title" not in manifest and "effect body" not in manifest
    expanded = await relation_read(cause, include_titles=True)
    assert "title=Effect title" in expanded and "effect body" not in expanded

    after_cause = await bucket_mgr.get(cause)
    after_effect = await bucket_mgr.get(effect)
    for key in ("last_active", "activation_count", "importance", "tags", "domain", "created"):
        assert after_cause["metadata"].get(key) == before_cause["metadata"].get(key)
        assert after_effect["metadata"].get(key) == before_effect["metadata"].get(key)


@pytest.mark.asyncio
async def test_expected_title_is_optional_guard_not_identity_key(bucket_mgr, monkeypatch):
    a = await bucket_mgr.create("A", title="A title")
    b = await bucket_mgr.create("B", title="B title")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    assert "status=active" in await attach(a, b, "related_to")
    assert "active=1" in await relation_read(a)
    assert "\u6807\u9898\u4e0d\u5339\u914d" in await relation_read(a, expected_title="wrong")

    c = await bucket_mgr.create("C", title="C title")
    assert "\u6807\u9898\u4e0d\u5339\u914d" in await attach(
        a, c, "same_event", expected_title="wrong"
    )
    assert "relation_links" not in (await bucket_mgr.get(c))["metadata"]


@pytest.mark.asyncio
async def test_custom_relation_uses_forward_and_reverse_labels(bucket_mgr, monkeypatch):
    a = await bucket_mgr.create("A", title="A")
    b = await bucket_mgr.create("B", title="B")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    result = await attach(
        a,
        b,
        "custom",
        label="\u542f\u53d1\u4e86",
        reverse_label="\u53d7\u542f\u53d1\u4e8e",
    )
    assert "status=active" in result
    a_link = (await bucket_mgr.get(a))["metadata"]["relation_links"][0]
    b_link = (await bucket_mgr.get(b))["metadata"]["relation_links"][0]
    assert a_link["type"] == b_link["type"] == "custom"
    assert a_link["label"] == "\u542f\u53d1\u4e86"
    assert b_link["label"] == "\u53d7\u542f\u53d1\u4e8e"

    c = await bucket_mgr.create("C", title="C")
    assert "status=active" in await attach(
        a, c, "custom", label="\u540c\u4e00\u7ec4\u5b9e\u9a8c"
    )
    c_link = (await bucket_mgr.get(c))["metadata"]["relation_links"][0]
    assert c_link["label"] == "\u540c\u4e00\u7ec4\u5b9e\u9a8c"


@pytest.mark.asyncio
async def test_fixed_types_reject_labels_and_custom_requires_label(bucket_mgr, monkeypatch):
    a = await bucket_mgr.create("A", title="A")
    b = await bucket_mgr.create("B", title="B")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    assert "\u56fa\u5b9a\u516d\u79cd" in await attach(a, b, "causes", label="extra")
    assert "\u56fa\u5b9a\u516d\u79cd" in await attach(
        a, b, "causes", reverse_label="extra"
    )
    assert "\u5fc5\u987b\u586b label" in await attach(a, b, "custom")
    assert "relation_links" not in (await bucket_mgr.get(a))["metadata"]
    assert "relation_links" not in (await bucket_mgr.get(b))["metadata"]


@pytest.mark.asyncio
async def test_detach_restore_updates_both_mirrors_and_read_hides_detached_by_default(
    bucket_mgr, monkeypatch
):
    a = await bucket_mgr.create("A", title="A")
    b = await bucket_mgr.create("B", title="B")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    await attach(a, b, "continuation_of")
    assert "status=detached" in await detach(a, 1)
    a_link = (await bucket_mgr.get(a))["metadata"]["relation_links"][0]
    b_link = (await bucket_mgr.get(b))["metadata"]["relation_links"][0]
    assert a_link["status"] == b_link["status"] == "detached"
    compact = await relation_read(a)
    assert "active=0 | detached=1" in compact and "slot=1" not in compact
    history = await relation_read(a, include_detached=True)
    assert "slot=1" in history and "detached" in history
    assert "status=active" in await restore(b, 1)
    a_link = (await bucket_mgr.get(a))["metadata"]["relation_links"][0]
    b_link = (await bucket_mgr.get(b))["metadata"]["relation_links"][0]
    assert a_link["status"] == b_link["status"] == "active"


@pytest.mark.asyncio
async def test_legacy_one_way_relation_remains_readable_and_locally_reversible(
    bucket_mgr, monkeypatch
):
    source = await bucket_mgr.create("source", title="source")
    target = await bucket_mgr.create("target", title="target")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    await bucket_mgr.mutate_relation_links(
        source,
        lambda post: (
            True,
            post.__setitem__(
                "relation_links",
                [{"target_bucket_id": target, "type": "related_to", "label": "", "status": "active"}],
            )
            or "seed",
        ),
    )
    assert "slot=1" in await relation_read(source)
    assert "legacy=true" in await detach(source, 1)
    source_links = (await bucket_mgr.get(source))["metadata"]["relation_links"]
    assert source_links[0]["status"] == "detached"
    assert "relation_links" not in (await bucket_mgr.get(target))["metadata"]
    assert "legacy=true" in await restore(source, 1)


@pytest.mark.asyncio
async def test_relation_rejects_self_and_special_buckets_but_allows_archived_ordinary(
    bucket_mgr, monkeypatch
):
    ordinary = await bucket_mgr.create("ordinary", title="ordinary")
    target = await bucket_mgr.create("target", title="target")
    plan = await bucket_mgr.create("plan", title="plan", bucket_type="plan")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    invalid_target: Any = []
    assert "\u5fc5\u987b\u662f\u5b57\u7b26\u4e32" in await attach(
        ordinary, invalid_target, "causes"
    )
    assert "\u81ea\u73af" in await attach(ordinary, ordinary, "causes")
    result = await attach(ordinary, plan, "related_to")
    assert "Relation" in result
    assert "slot=" not in result and "status=" not in result
    assert await bucket_mgr.archive(ordinary)
    archived = await bucket_mgr.get_including_archive(ordinary)
    assert archived["metadata"].get("type") == "archived"
    assert "slot=1" in await attach(ordinary, target, "related_to")
    assert "slot=1" in await relation_read(ordinary)
    assert "slot=1" in await relation_read(target)


def test_dream_recent_candidates_only_include_ordinary_buckets():
    from datetime import datetime

    now = datetime.now().isoformat()

    def bucket(bucket_id, bucket_type):
        return {"id": bucket_id, "metadata": {"type": bucket_type, "last_active": now}}

    selected = collect_candidates(
        [
            bucket("ordinary", "dynamic"),
            bucket("plan", "plan"),
            bucket("feel", "feel"),
            bucket("i", "i"),
        ],
        48,
    )
    assert [item["id"] for item in selected] == ["ordinary"]


@pytest.mark.parametrize(
    "value", ["custom.type", "next", "related-to", "causes!", "same_event_1"]
)
def test_relation_type_rejects_safe_but_unsupported_values(value):
    with pytest.raises(ValueError):
        normalize_relation_type(value)


@pytest.mark.parametrize("value", ["\n", " \r\n ", "x" * 21])
def test_relation_label_rejects_raw_newlines_and_overlong_values(value):
    with pytest.raises(ValueError):
        normalize_relation_label(value)


@pytest.mark.asyncio
async def test_archived_special_bucket_relation_tools_leave_ledger_unchanged(
    bucket_mgr, monkeypatch
):
    source = await bucket_mgr.create("source", title="source")
    target = await bucket_mgr.create("target", title="target")
    special = await bucket_mgr.create("special", title="special", bucket_type="plan")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    await bucket_mgr.mutate_relation_links(
        special,
        lambda post: (
            True,
            post.__setitem__(
                "relation_links",
                [{"target_bucket_id": target, "type": "related_to", "label": "", "status": "active"}],
            )
            or "seed",
        ),
    )
    assert await bucket_mgr.archive(special)
    before = (await bucket_mgr.get_including_archive(special))["metadata"]["relation_links"]
    assert "only permits ordinary" in await relation_read(special)
    assert "only permits ordinary" in await detach(special, 1)
    assert "only permits ordinary" in await restore(special, 1)
    assert "only permits ordinary" in await attach(special, source, "causes")
    after = (await bucket_mgr.get_including_archive(special))["metadata"]["relation_links"]
    assert after == before


@pytest.mark.asyncio
async def test_source_and_target_active_limits_are_checked_before_pair_write(
    bucket_mgr, monkeypatch
):
    source = await bucket_mgr.create("source", title="source")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    targets = [
        await bucket_mgr.create(f"target-{i}", title=f"target-{i}")
        for i in range(17)
    ]
    for target in targets[:16]:
        assert "status=active" in await attach(source, target, "related_to")
    rejected = await attach(source, targets[16], "related_to")
    assert "16" in rejected and "\u62d2\u7edd" in rejected
    assert "relation_links" not in (await bucket_mgr.get(targets[16]))["metadata"]


@pytest.mark.asyncio
async def test_attach_revalidates_target_inside_dual_bucket_turn(bucket_mgr, monkeypatch):
    source = await bucket_mgr.create("source", title="source")
    target = await bucket_mgr.create("target", title="target")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    target_file = bucket_mgr._find_bucket_file(target)
    assert target_file is not None
    original_mutate = bucket_mgr.mutate_relation_pair

    async def remove_target_after_preflight(
        left_bucket_id: str,
        right_bucket_id: str,
        mutation: Any,
    ):
        Path(target_file).unlink()
        return await original_mutate(left_bucket_id, right_bucket_id, mutation)

    monkeypatch.setattr(bucket_mgr, "mutate_relation_pair", remove_target_after_preflight)
    result = await attach(source, target, "related_to")
    assert "\u672a\u627e\u5230\u6876" in result
    stored_source = await bucket_mgr.get(source)
    assert stored_source is not None
    assert "relation_links" not in stored_source["metadata"]


@pytest.mark.asyncio
async def test_restore_rejects_missing_target_and_keeps_source_detached(
    bucket_mgr, monkeypatch
):
    source = await bucket_mgr.create("source", title="source")
    target = await bucket_mgr.create("target", title="target")
    monkeypatch.setattr(rt, "bucket_mgr", bucket_mgr, raising=False)
    assert "status=active" in await attach(source, target, "related_to")
    assert "status=detached" in await detach(source, 1)
    target_file = bucket_mgr._find_bucket_file(target)
    assert target_file is not None
    original_mutate = bucket_mgr.mutate_relation_pair

    async def remove_target_after_preflight(
        left_bucket_id: str,
        right_bucket_id: str,
        mutation: Any,
    ):
        Path(target_file).unlink()
        return await original_mutate(left_bucket_id, right_bucket_id, mutation)

    monkeypatch.setattr(bucket_mgr, "mutate_relation_pair", remove_target_after_preflight)
    result = await restore(source, 1)
    assert "\u672a\u627e\u5230\u6876" in result
    links = (await bucket_mgr.get(source))["metadata"]["relation_links"]
    assert links[0]["status"] == "detached"


@pytest.mark.asyncio
async def test_dream_relation_hint_is_separated_from_footprint(monkeypatch):
    from tools.dream.output import format_dream_output

    class Snapshot:
        def summary(self, _bucket_id, _metadata):
            return "FOOTPRINT"

    class Manager:
        def footprint_snapshot(self):
            return Snapshot()

    monkeypatch.setattr(rt, "bucket_mgr", Manager(), raising=False)
    monkeypatch.setattr(rt, "config", {}, raising=False)
    bucket = {
        "id": "source",
        "content": "body",
        "metadata": {
            "name": "source",
            "type": "dynamic",
            "domain": [],
            "relation_links": [
                {
                    "target_bucket_id": "target-id",
                    "type": "continues",
                    "label": "",
                    "status": "active",
                }
            ],
        },
    }
    output = await format_dream_output([bucket], [bucket], 48, "", "")
    assert "target-id\nFOOTPRINT" in output
