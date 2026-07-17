import asyncio
import json
import threading
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from bucket_manager import BucketManager
from embedding_outbox import EmbeddingOutbox, content_hash
from utils import bucket_text_for_embedding


def _config(tmp_path):
    buckets_dir = tmp_path / "buckets"
    state_dir = tmp_path / "state"
    buckets_dir.mkdir(exist_ok=True)
    state_dir.mkdir(exist_ok=True)
    return {
        "buckets_dir": str(buckets_dir),
        "state_dir": str(state_dir),
        "matching": {},
        "scoring_weights": {},
        "wikilink": {},
        "embedding": {
            "enabled": True,
            "background_indexing": True,
            "retry_base_seconds": 0.01,
            "retry_max_seconds": 0.02,
        },
    }


def test_bucket_turn_serializes_different_event_loops(tmp_path):
    manager = BucketManager(_config(tmp_path))
    barrier = threading.Barrier(3)
    active = 0
    maximum = 0
    guard = threading.Lock()

    async def hold_turn():
        nonlocal active, maximum
        barrier.wait()
        async with manager._bucket_turn("same-bucket"):
            with guard:
                active += 1
                maximum = max(maximum, active)
            await asyncio.sleep(0.05)
            with guard:
                active -= 1

    threads = [threading.Thread(target=lambda: asyncio.run(hold_turn())) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert maximum == 1


def test_archived_bucket_requires_explicit_activation_before_update(tmp_path):
    manager = BucketManager(_config(tmp_path))

    async def scenario():
        bucket_id = await manager.create(content="original")
        assert await manager.archive(bucket_id) is True
        assert await manager.update(bucket_id, content="must not resurrect") is False

        archived = await manager.get(bucket_id)
        assert archived is not None
        assert archived["content"] == "original"
        assert archived["metadata"]["type"] == "archived"
        assert len([b for b in await manager.list_all(include_archive=True) if b["id"] == bucket_id]) == 1

        assert await manager.activate(bucket_id) is True
        assert await manager.update(bucket_id, content="allowed after activation") is True
        active = await manager.get(bucket_id)
        assert active is not None
        assert active["content"] == "allowed after activation"

    asyncio.run(scenario())


def test_delete_serializes_yaml_dates_and_preserves_previous_tombstone_on_failure(
    tmp_path, monkeypatch
):
    manager = BucketManager(_config(tmp_path))
    bucket_id = asyncio.run(
        manager.create(
            content="dated",
            bucket_id="dated-bucket",
            created=datetime(2026, 7, 16, tzinfo=timezone.utc),
        )
    )
    source_path = manager._find_bucket_file(bucket_id)
    assert source_path is not None
    tombstone_path = Path(manager.tombstone_dir) / f"{bucket_id}.json"
    tombstone_path.parent.mkdir(parents=True, exist_ok=True)
    tombstone_path.write_text('{"id":"older","marker":true}\n', encoding="utf-8")
    original_remove = __import__("bucket_manager").os.remove

    def fail_source_remove(path):
        if str(path) == str(source_path):
            raise OSError("simulated source delete failure")
        return original_remove(path)

    monkeypatch.setattr("bucket_manager.os.remove", fail_source_remove)
    assert asyncio.run(manager.delete(bucket_id)) is False
    assert json.loads(tombstone_path.read_text(encoding="utf-8"))["marker"] is True
    assert Path(source_path).exists()


def test_bucket_move_rolls_back_without_duplicate_when_rewrite_fails(tmp_path, monkeypatch):
    manager = BucketManager(_config(tmp_path))
    bucket_id = asyncio.run(manager.create(content="original", bucket_id="move-bucket"))
    source_path_value = manager._find_bucket_file(bucket_id)
    assert source_path_value is not None
    source_path = Path(source_path_value)
    original_atomic_write = __import__("bucket_manager").atomic_write_text

    def fail_archive_write(path, text):
        if str(path).startswith(str(Path(manager.archive_dir))):
            raise OSError("simulated rewrite failure")
        return original_atomic_write(path, text)

    monkeypatch.setattr("bucket_manager.atomic_write_text", fail_archive_write)
    assert asyncio.run(manager.archive(bucket_id)) is False
    assert source_path.exists()
    assert list(Path(manager.archive_dir).rglob("*.md")) == []
    current = asyncio.run(manager.get(bucket_id))
    assert current is not None
    assert current["content"] == "original"


def test_concurrent_archive_and_update_leaves_at_most_one_copy(tmp_path, monkeypatch):
    manager = BucketManager(_config(tmp_path))

    async def scenario():
        bucket_id = await manager.create(content="before concurrent mutation")
        archive_entered = asyncio.Event()
        release_archive = asyncio.Event()
        archive_unlocked = getattr(BucketManager.archive, "__wrapped__")

        async def gated_archive(requested_id):
            async with manager._bucket_turn(requested_id):
                archive_entered.set()
                await release_archive.wait()
                return await archive_unlocked(manager, requested_id)

        monkeypatch.setattr(manager, "archive", gated_archive)
        archive_task = asyncio.create_task(manager.archive(bucket_id))
        await asyncio.wait_for(archive_entered.wait(), timeout=0.5)
        update_task = asyncio.create_task(
            manager.update(bucket_id, content="updated concurrently")
        )
        await asyncio.sleep(0)
        release_archive.set()

        archived, updated = await asyncio.gather(archive_task, update_task)
        assert archived is True
        assert updated is False

        matches = [
            bucket
            for bucket in await manager.list_all(include_archive=True)
            if bucket["id"] == bucket_id
        ]
        assert len(matches) == 1
        assert matches[0]["content"] == "before concurrent mutation"
        assert matches[0]["metadata"]["type"] == "archived"

    asyncio.run(scenario())


def test_concurrent_touch_and_delete_never_duplicates_bucket(tmp_path, monkeypatch):
    manager = BucketManager(_config(tmp_path))

    async def scenario():
        bucket_id = await manager.create(content="before concurrent delete")
        touch_entered = asyncio.Event()
        release_touch = asyncio.Event()
        original_touch_locked = manager._touch_locked

        async def gated_touch_locked(requested_id):
            touch_entered.set()
            await release_touch.wait()
            return await original_touch_locked(requested_id)

        monkeypatch.setattr(manager, "_touch_locked", gated_touch_locked)
        touch_task = asyncio.create_task(manager.touch(bucket_id, ripple=False))
        await asyncio.wait_for(touch_entered.wait(), timeout=0.5)
        delete_task = asyncio.create_task(manager.delete(bucket_id))
        await asyncio.sleep(0)
        release_touch.set()

        touched, deleted = await asyncio.gather(touch_task, delete_task)
        assert touched is None
        assert deleted is True
        assert await manager.get(bucket_id) is None
        tombstone = Path(manager.tombstone_dir) / f"{bucket_id}.json"
        assert json.loads(tombstone.read_text(encoding="utf-8"))["id"] == bucket_id

    asyncio.run(scenario())


class _Manager:
    def __init__(self, bucket_id="bucket-1", content="original"):
        self.bucket_id = bucket_id
        self.content = content
        self.exists = True

    async def get(self, bucket_id):
        if not self.exists or bucket_id != self.bucket_id:
            return None
        return {
            "id": bucket_id,
            "content": self.content,
            "metadata": {"name": "Memory"},
        }

    async def list_all(self, include_archive=True):
        bucket = await self.get(self.bucket_id)
        return [bucket] if bucket else []


class _Engine:
    enabled = True

    def __init__(self, manager, *, succeeds=True):
        self.manager = manager
        self.succeeds = succeeds
        self.calls = []
        self.hashes = {}

    async def generate_and_store(self, bucket_id, content):
        self.calls.append((bucket_id, content))
        if not self.succeeds:
            return False
        self.hashes[bucket_id] = content_hash(content)
        return True

    def delete_embedding(self, bucket_id):
        self.hashes.pop(bucket_id, None)

    def list_content_ids(self):
        return list(self.hashes)

    def list_content_hashes(self):
        return dict(self.hashes)


def test_embedding_outbox_survives_restart_and_retries(tmp_path):
    config = _config(tmp_path)
    manager = _Manager()
    failing = _Engine(manager, succeeds=False)
    first = EmbeddingOutbox(config, manager, failing)
    bucket = asyncio.run(manager.get(manager.bucket_id))
    assert bucket is not None
    desired = bucket_text_for_embedding(bucket)

    assert first.enqueue(manager.bucket_id, desired) is True
    assert asyncio.run(first.process_once()) is True
    assert first.status()["retrying"] == 1

    recovered = _Engine(manager, succeeds=True)
    second = EmbeddingOutbox(config, manager, recovered)
    assert second.is_pending(manager.bucket_id) is True
    second.retry_now()
    assert asyncio.run(second.process_once()) is True
    assert second.is_pending(manager.bucket_id) is False
    assert recovered.calls == [(manager.bucket_id, desired)]


def test_embedding_outbox_does_not_acknowledge_newer_content(tmp_path):
    config = _config(tmp_path)
    manager = _Manager()
    engine = _Engine(manager)
    outbox = EmbeddingOutbox(config, manager, engine)
    bucket = asyncio.run(manager.get(manager.bucket_id))
    assert bucket is not None
    old_text = bucket_text_for_embedding(bucket)
    outbox.enqueue(manager.bucket_id, old_text)
    old_item = dict(outbox._items[manager.bucket_id])

    manager.content = "newer"
    bucket = asyncio.run(manager.get(manager.bucket_id))
    assert bucket is not None
    new_text = bucket_text_for_embedding(bucket)
    outbox.enqueue(manager.bucket_id, new_text)
    outbox._complete(manager.bucket_id, old_item["content_hash"])

    assert outbox.is_pending(manager.bucket_id) is True
    assert outbox._items[manager.bucket_id]["content_hash"] == content_hash(new_text)


def test_embedding_outbox_discards_deleted_bucket(tmp_path):
    config = _config(tmp_path)
    manager = _Manager()
    engine = _Engine(manager)
    outbox = EmbeddingOutbox(config, manager, engine)
    bucket = asyncio.run(manager.get(manager.bucket_id))
    assert bucket is not None
    desired = bucket_text_for_embedding(bucket)
    outbox.enqueue(manager.bucket_id, desired)
    manager.exists = False

    assert asyncio.run(outbox.process_once()) is True
    assert outbox.is_pending(manager.bucket_id) is False


def test_embedding_outbox_background_processing_is_nonblocking(tmp_path):
    config = _config(tmp_path)
    manager = _Manager()

    class SlowEngine(_Engine):
        def __init__(self, manager):
            super().__init__(manager)
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def generate_and_store(self, bucket_id, content):
            self.started.set()
            await self.release.wait()
            return await super().generate_and_store(bucket_id, content)

    async def scenario():
        engine = SlowEngine(manager)
        outbox = EmbeddingOutbox(config, manager, engine)
        await outbox.start(reconcile=False)
        try:
            bucket = await manager.get(manager.bucket_id)
            assert bucket is not None
            desired = bucket_text_for_embedding(bucket)
            started = time.monotonic()
            assert outbox.enqueue(manager.bucket_id, desired) is True
            assert time.monotonic() - started < 0.05
            await asyncio.wait_for(engine.started.wait(), timeout=0.5)
            assert outbox.is_pending(manager.bucket_id) is True
            engine.release.set()
            assert await outbox.wait_until_idle(timeout=1)
        finally:
            engine.release.set()
            await outbox.stop()

    asyncio.run(scenario())


def test_embedding_outbox_reconcile_preserves_newer_pending_content(tmp_path):
    config = _config(tmp_path)
    manager = _Manager(content="stale vault snapshot")
    engine = _Engine(manager)
    outbox = EmbeddingOutbox(config, manager, engine)
    stale_bucket = asyncio.run(manager.get(manager.bucket_id))
    assert stale_bucket is not None
    stale_text = bucket_text_for_embedding(stale_bucket)
    engine.hashes[manager.bucket_id] = content_hash(stale_text)

    manager.content = "newer content already queued"
    newer_bucket = asyncio.run(manager.get(manager.bucket_id))
    assert newer_bucket is not None
    newer_text = bucket_text_for_embedding(newer_bucket)
    outbox.enqueue(manager.bucket_id, newer_text)
    manager.content = "stale vault snapshot"

    assert asyncio.run(outbox.reconcile(include_archive=False)) == 0
    assert outbox._items[manager.bucket_id]["content_hash"] == content_hash(newer_text)


def test_embedding_outbox_reconcile_index_failure_avoids_reindex_storm(tmp_path):
    config = _config(tmp_path)
    manager = _Manager()

    class BrokenIndexEngine(_Engine):
        def list_content_ids(self):
            raise RuntimeError("index unavailable")

    outbox = EmbeddingOutbox(config, manager, BrokenIndexEngine(manager))

    assert asyncio.run(outbox.reconcile()) == 0
    assert outbox.status()["pending"] == 0


def test_embedding_outbox_requeues_content_changed_during_indexing(tmp_path):
    config = _config(tmp_path)
    manager = _Manager()

    class BlockingEngine(_Engine):
        def __init__(self, manager):
            super().__init__(manager)
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def generate_and_store(self, bucket_id, content):
            self.calls.append((bucket_id, content))
            if len(self.calls) == 1:
                self.started.set()
                await self.release.wait()
            self.hashes[bucket_id] = content_hash(content)
            return True

    async def scenario():
        engine = BlockingEngine(manager)
        outbox = EmbeddingOutbox(config, manager, engine)
        first_bucket = await manager.get(manager.bucket_id)
        assert first_bucket is not None
        first_text = bucket_text_for_embedding(first_bucket)
        outbox.enqueue(manager.bucket_id, first_text)

        first_pass = asyncio.create_task(outbox.process_once())
        try:
            await asyncio.wait_for(engine.started.wait(), timeout=0.5)
            manager.content = "changed while indexing"
            second_bucket = await manager.get(manager.bucket_id)
            assert second_bucket is not None
            second_text = bucket_text_for_embedding(second_bucket)
            engine.release.set()
            assert await first_pass is True

            assert outbox.is_pending(manager.bucket_id) is True
            assert await outbox.process_once() is True
            assert outbox.is_pending(manager.bucket_id) is False
            assert engine.calls == [
                (manager.bucket_id, first_text),
                (manager.bucket_id, second_text),
            ]
            assert engine.hashes[manager.bucket_id] == content_hash(second_text)
        finally:
            engine.release.set()
            if not first_pass.done():
                first_pass.cancel()
            with suppress(asyncio.CancelledError):
                await first_pass

    asyncio.run(scenario())


def test_embedding_outbox_failed_item_does_not_block_other_due_item(tmp_path):
    config = _config(tmp_path)

    class MultiManager:
        def __init__(self):
            self.contents = {"bad": "cannot embed", "good": "can embed"}

        async def get(self, bucket_id):
            content = self.contents.get(bucket_id)
            if content is None:
                return None
            return {"id": bucket_id, "content": content, "metadata": {"name": bucket_id}}

        async def list_all(self, include_archive=True):
            return [await self.get(bucket_id) for bucket_id in self.contents]

    class SelectiveEngine:
        enabled = True

        def __init__(self):
            self.calls = []
            self.hashes = {}

        async def generate_and_store(self, bucket_id, content):
            self.calls.append((bucket_id, content))
            if bucket_id == "bad":
                return False
            self.hashes[bucket_id] = content_hash(content)
            return True

        def delete_embedding(self, bucket_id):
            self.hashes.pop(bucket_id, None)

    async def scenario():
        manager = MultiManager()
        engine = SelectiveEngine()
        outbox = EmbeddingOutbox(config, manager, engine)
        for bucket_id in ("bad", "good"):
            bucket = await manager.get(bucket_id)
            assert bucket is not None
            outbox.enqueue(
                bucket_id,
                bucket_text_for_embedding(bucket),
            )

        assert await outbox.process_once() is True
        assert outbox.is_pending("bad") is True
        assert await outbox.process_once() is True
        assert outbox.is_pending("good") is False
        assert "good" in engine.hashes

    asyncio.run(scenario())
