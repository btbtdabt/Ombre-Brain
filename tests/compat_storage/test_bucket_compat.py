from __future__ import annotations

import asyncio
import json
from pathlib import Path

import frontmatter
import pytest

import bucket_manager as bucket_manager_module
from bucket_manager import BucketManager


def _config(tmp_path):
    return {
        "buckets_dir": str(tmp_path / "buckets"),
        "state_dir": str(tmp_path / "state"),
        "embedding": {"enabled": False},
    }


@pytest.mark.asyncio
async def test_imported_bucket_metadata_round_trips_through_create(tmp_path):
    manager = BucketManager(_config(tmp_path))

    bucket_id = await manager.create(
        "### moment\nA durable imported memory.",
        name="Imported title",
        domain="relationship",
        bucket_id="legacy-memory-id",
        created="2025-04-03T02:01:00+00:00",
        last_active="2025-04-04T02:01:00+00:00",
        updated_at="2025-04-05T02:01:00+00:00",
        source="operit",
        confidence=0.8,
        extra_metadata={
            "comments": [{"id": "ring-1", "kind": "feel", "content": "I remember."}],
            "comment_count": 1,
            "moment_annotations": {"moment:0": {"confidence": 0.9}},
            "id": "must-not-override",
            "content": "must-not-override",
        },
    )

    assert bucket_id == "legacy-memory-id"
    bucket = await manager.get(bucket_id)
    assert bucket is not None
    assert bucket["content"] == "### moment\nA durable imported memory."
    assert bucket["metadata"]["id"] == bucket_id
    assert bucket["metadata"]["name"] == "Imported title"
    assert bucket["metadata"]["domain"] == ["relationship"]
    assert bucket["metadata"]["created"] == "2025-04-03T02:01:00+00:00"
    assert bucket["metadata"]["last_active"] == "2025-04-04T02:01:00+00:00"
    assert bucket["metadata"]["updated_at"] == "2025-04-05T02:01:00+00:00"
    assert bucket["metadata"]["source"] == "operit"
    assert bucket["metadata"]["confidence"] == 0.8
    assert bucket["metadata"]["comment_count"] == 1
    assert bucket["metadata"]["comments"][0]["id"] == "ring-1"
    assert bucket["metadata"]["moment_annotations"]["moment:0"]["confidence"] == 0.9


@pytest.mark.asyncio
async def test_concurrent_comment_and_moment_metadata_update_preserve_both(tmp_path):
    manager = BucketManager(_config(tmp_path))
    bucket_id = await manager.create(
        "Original body",
        bucket_id="comment-target",
        domain="relationship",
    )

    comment, updated = await asyncio.gather(
        manager.add_comment(
            bucket_id,
            "A new feeling attached to the old memory.",
            author="Ombre",
            kind="feel",
            valence=0.25,
            source="comment_bucket",
            created="2026-07-16T12:00:00+00:00",
            touch=False,
        ),
        manager.update(
            bucket_id,
            content="Updated body",
            extra_metadata={
                "moment_annotations": {"moment:0": {"layer": "episodic"}}
            },
        ),
    )

    assert updated is True
    assert comment is not None
    bucket = await manager.get(bucket_id)
    assert bucket is not None
    assert bucket["content"] == "Updated body"
    assert bucket["metadata"]["comment_count"] == 1
    assert bucket["metadata"]["comments"] == [comment]
    assert comment["kind"] == "feel"
    assert comment["valence"] == 0.25
    assert bucket["metadata"]["moment_annotations"]["moment:0"]["layer"] == "episodic"
    assert bucket["metadata"]["activation_count"] == 0


@pytest.mark.asyncio
async def test_comment_delete_authorization_and_string_activation_count(tmp_path):
    manager = BucketManager(_config(tmp_path))
    bucket_id = await manager.create(
        "Comment lifecycle",
        bucket_id="comment-lifecycle",
    )
    bucket = await manager.get(bucket_id)
    assert bucket is not None
    post = frontmatter.load(bucket["path"])
    post["activation_count"] = "3"
    Path(bucket["path"]).write_text(frontmatter.dumps(post), encoding="utf-8")
    comment = await manager.add_comment(
        bucket_id,
        "Increment from a historical numeric string.",
        author="Ombre",
        source="comment_bucket",
    )
    assert comment is not None
    bucket = await manager.get(bucket_id)
    assert bucket is not None
    assert bucket["metadata"]["activation_count"] == 4.0

    forbidden = await manager.delete_comment(
        bucket_id,
        comment["id"],
        allowed_source="feel",
    )
    assert forbidden["status"] == "forbidden"
    assert (await manager.get(bucket_id))["metadata"]["comment_count"] == 1

    deleted = await manager.delete_comment(
        bucket_id,
        comment["id"],
        allowed_author="Ombre",
        allowed_source="comment_bucket",
    )
    assert deleted == {"status": "deleted", "comment": comment}
    bucket = await manager.get(bucket_id)
    assert bucket is not None
    assert bucket["metadata"]["comments"] == []
    assert bucket["metadata"]["comment_count"] == 0


@pytest.mark.asyncio
async def test_touch_increments_fractional_string_activation_count(tmp_path):
    manager = BucketManager(_config(tmp_path))
    bucket_id = await manager.create(
        "Historical fractional activation",
        bucket_id="fractional-activation",
    )
    bucket = await manager.get(bucket_id)
    assert bucket is not None
    post = frontmatter.load(bucket["path"])
    post["activation_count"] = "2.5"
    Path(bucket["path"]).write_text(frontmatter.dumps(post), encoding="utf-8")

    await manager.touch(bucket_id, ripple=False)

    touched = await manager.get(bucket_id)
    assert touched is not None
    assert touched["metadata"]["activation_count"] == 3.5


@pytest.mark.asyncio
async def test_archived_bucket_activation_and_letter_listing(tmp_path):
    manager = BucketManager(_config(tmp_path))
    memory_id = await manager.create(
        "Archived memory",
        bucket_id="archived-memory",
        domain="relationship",
    )
    letter_id = await manager.create(
        "A private letter",
        bucket_id="letter-memory",
        bucket_type="letter",
        domain="letter",
        extra_metadata={"author": "Ombre", "source_tool": "letter"},
    )

    assert [letter["id"] for letter in await manager.list_letters()] == [letter_id]
    assert letter_id not in {bucket["id"] for bucket in await manager.list_all()}
    assert await manager.archive(memory_id) is True
    assert await manager.update(memory_id, content="must not resurrect") is False

    assert await manager.activate(memory_id) is True
    assert await manager.update(memory_id, content="active again") is True
    activated = await manager.get(memory_id)
    assert activated is not None
    assert activated["content"] == "active again"
    assert activated["metadata"]["type"] == "dynamic"
    assert activated["metadata"]["active"] is True
    assert activated["metadata"]["deprecated"] is False
    assert Path(activated["path"]).parent.name == "relationship"
    assert Path(activated["path"]).parents[1].name == "dynamic"
    assert manager._parse_iso_datetime("2026-07-16T12:00:00Z") is not None
    assert manager._parse_iso_datetime("not-a-date") is None


@pytest.mark.asyncio
async def test_soft_delete_keeps_json_tombstone_transactional(
    tmp_path,
    monkeypatch,
):
    manager = BucketManager(_config(tmp_path))
    deleted_id = await manager.create("delete me", bucket_id="deleted-memory")

    assert await manager.delete(deleted_id) is True
    tombstone = Path(manager.tombstone_dir) / f"{deleted_id}.json"
    assert json.loads(tombstone.read_text(encoding="utf-8"))["id"] == deleted_id
    assert manager._find_bucket_file(deleted_id) is not None
    assert await manager.get(deleted_id) is None

    rollback_id = await manager.create("keep me", bucket_id="rollback-memory")
    source_path = manager._find_bucket_file(rollback_id)
    assert source_path is not None
    prior = Path(manager.tombstone_dir) / f"{rollback_id}.json"
    prior.write_text('{"id":"older","marker":true}\n', encoding="utf-8")
    real_remove = bucket_manager_module.os.remove

    def fail_source_remove(path):
        if str(path) == str(source_path):
            raise OSError("simulated source removal failure")
        return real_remove(path)

    monkeypatch.setattr(bucket_manager_module.os, "remove", fail_source_remove)

    assert await manager.delete(rollback_id) is False
    assert json.loads(prior.read_text(encoding="utf-8"))["marker"] is True
    assert await manager.get(rollback_id) is not None


@pytest.mark.asyncio
async def test_archive_uses_compatible_atomic_writer_and_exposes_unlocked_method(
    tmp_path,
    monkeypatch,
):
    manager = BucketManager(_config(tmp_path))
    bucket_id = await manager.create("move safely", bucket_id="archive-rollback")
    source_path = manager._find_bucket_file(bucket_id)
    assert source_path is not None
    real_writer = bucket_manager_module.atomic_write_text

    def fail_archive_write(path, text):
        if str(path).startswith(str(Path(manager.archive_dir))):
            raise OSError("simulated archive write failure")
        return real_writer(path, text)

    monkeypatch.setattr(bucket_manager_module, "atomic_write_text", fail_archive_write)

    assert hasattr(BucketManager.archive, "__wrapped__")
    assert await manager.archive(bucket_id) is False
    assert Path(source_path).exists()
    assert list(Path(manager.archive_dir).rglob("*.md")) == []


@pytest.mark.asyncio
async def test_batch_lexical_apis_remain_available_for_moment_recall(tmp_path):
    manager = BucketManager(_config(tmp_path))
    await manager.create(
        "The silver lighthouse overlooks the northern harbor.",
        bucket_id="lighthouse",
        name="Harbor light",
        domain="travel",
        tags=["navigation"],
    )
    await manager.create(
        "The daily journal records an ordinary breakfast.",
        bucket_id="journal",
        name="Daily journal",
        domain="routine",
        tags=["journal"],
    )
    buckets = await manager.list_all()

    assert manager.warm_lexical_profiles(buckets) == 2
    scores = manager.calc_topic_scores("silver lighthouse", buckets)
    assert scores["lighthouse"] > scores.get("journal", 0.0)
    assert manager.filter_specific_lexical_terms(
        ["the", "lighthouse", "missing"],
        buckets,
    ) == ["lighthouse"]
    stats = manager.lexical_term_specificity_stats(["lighthouse"], buckets)
    assert stats["lighthouse"]["document_frequency"] == 1.0


@pytest.mark.asyncio
async def test_loaded_bucket_reports_original_markdown_body_line(tmp_path):
    manager = BucketManager(_config(tmp_path))
    path = Path(manager.dynamic_dir) / "travel" / "source-lines.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\n"
        "id: source-lines\n"
        "name: Source lines\n"
        "domain: [travel]\n"
        "importance: 5\n"
        "activation_count: 0\n"
        "---\n"
        "\n"
        "First body line.\n",
        encoding="utf-8",
    )

    bucket = await manager.get("source-lines")

    assert bucket is not None
    assert bucket["content"] == "First body line."
    assert bucket["content_start_line"] == 9
