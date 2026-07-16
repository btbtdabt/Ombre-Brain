import asyncio
import json
import threading
import time
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
    source_path = Path(manager._find_bucket_file(bucket_id))
    original_atomic_write = __import__("bucket_manager").atomic_write_text

    def fail_archive_write(path, text):
        if str(path).startswith(str(Path(manager.archive_dir))):
            raise OSError("simulated rewrite failure")
        return original_atomic_write(path, text)

    monkeypatch.setattr("bucket_manager.atomic_write_text", fail_archive_write)
    assert asyncio.run(manager.archive(bucket_id)) is False
    assert source_path.exists()
    assert list(Path(manager.archive_dir).rglob("*.md")) == []
    assert asyncio.run(manager.get(bucket_id))["content"] == "original"


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
    desired = bucket_text_for_embedding(asyncio.run(manager.get(manager.bucket_id)))

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
    old_text = bucket_text_for_embedding(asyncio.run(manager.get(manager.bucket_id)))
    outbox.enqueue(manager.bucket_id, old_text)
    old_item = dict(outbox._items[manager.bucket_id])

    manager.content = "newer"
    new_text = bucket_text_for_embedding(asyncio.run(manager.get(manager.bucket_id)))
    outbox.enqueue(manager.bucket_id, new_text)
    outbox._complete(manager.bucket_id, old_item["content_hash"])

    assert outbox.is_pending(manager.bucket_id) is True
    assert outbox._items[manager.bucket_id]["content_hash"] == content_hash(new_text)


def test_embedding_outbox_discards_deleted_bucket(tmp_path):
    config = _config(tmp_path)
    manager = _Manager()
    engine = _Engine(manager)
    outbox = EmbeddingOutbox(config, manager, engine)
    desired = bucket_text_for_embedding(asyncio.run(manager.get(manager.bucket_id)))
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
            desired = bucket_text_for_embedding(await manager.get(manager.bucket_id))
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
