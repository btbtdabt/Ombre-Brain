"""Durable write-behind queue for the derived embedding index."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any, cast

from utils import atomic_write_text, bucket_text_for_embedding, now_iso


logger = logging.getLogger("ombre_brain.embedding_outbox")

_OUTBOX_VERSION = 1
_OUTBOX_FILENAME = ".embedding_outbox.json"
_IDLE_POLL_SECONDS = 30.0


def content_hash(content: str) -> str:
    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


class EmbeddingOutbox:
    """Persist and retry embedding work without duplicating memory content."""

    def __init__(self, config: dict, bucket_mgr: Any, embedding_engine: Any) -> None:
        self.config = config
        self.bucket_mgr = bucket_mgr
        self.embedding_engine = embedding_engine
        state_dir = str(config.get("state_dir") or config["buckets_dir"])
        self.path = os.path.join(state_dir, _OUTBOX_FILENAME)
        embed_cfg = config.get("embedding", {}) or {}
        self.background_enabled = _as_bool(embed_cfg.get("background_indexing", True), True)
        self.retry_base_seconds = _positive_float(embed_cfg.get("retry_base_seconds"), 5.0)
        self.retry_max_seconds = max(
            self.retry_base_seconds,
            _positive_float(embed_cfg.get("retry_max_seconds"), 300.0),
        )

        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] = self._load_items()
        self._event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._starting = False
        self._processed = 0
        self._last_success = ""

    @property
    def running(self) -> bool:
        return self._running

    def enqueue(self, bucket_id: str, content: str, *, reset_retry: bool = True) -> bool:
        bucket_id = str(bucket_id or "").strip()
        content = str(content or "")
        if not bucket_id:
            return False
        if not content.strip():
            self.discard(bucket_id)
            return False
        now = now_iso()
        digest = content_hash(content)
        with self._lock:
            current = self._items.get(bucket_id) or {}
            same_content = current.get("content_hash") == digest
            attempts = int(current.get("attempts") or 0) if same_content else 0
            queued_at = str(current.get("queued_at") or now) if same_content else now
            if reset_retry:
                attempts = 0
            self._items[bucket_id] = {
                "content_hash": digest,
                "queued_at": queued_at,
                "updated_at": now,
                "attempts": attempts,
                "next_attempt_at": 0.0 if reset_retry else float(current.get("next_attempt_at") or 0.0),
                "last_attempt_at": str(current.get("last_attempt_at") or ""),
                "last_error": "" if reset_retry else str(current.get("last_error") or ""),
            }
            self._persist_locked()
        self._wake()
        return True

    def ensure_pending(self, bucket_id: str, content: str) -> bool:
        bucket_id = str(bucket_id or "").strip()
        if not bucket_id or not str(content or "").strip():
            return False
        with self._lock:
            if bucket_id in self._items:
                return True
        return self.enqueue(bucket_id, content)

    def discard(self, bucket_id: str) -> bool:
        bucket_id = str(bucket_id or "").strip()
        with self._lock:
            if bucket_id not in self._items:
                return False
            self._items.pop(bucket_id, None)
            self._persist_locked()
        return True

    def is_pending(self, bucket_id: str) -> bool:
        with self._lock:
            return str(bucket_id or "") in self._items

    def pending_ids(self) -> set[str]:
        with self._lock:
            return set(self._items)

    def status(self) -> dict[str, Any]:
        with self._lock:
            items = [dict(item) for item in self._items.values()]
        failed = [item for item in items if int(item.get("attempts") or 0) > 0]
        latest = max(failed, key=lambda item: str(item.get("last_attempt_at") or ""), default={})
        return {
            "running": self._running,
            "background_enabled": self.background_enabled,
            "provider_ready": bool(self.embedding_engine and getattr(self.embedding_engine, "enabled", False)),
            "pending": len(items),
            "retrying": len(failed),
            "processed": self._processed,
            "last_success": self._last_success,
            "last_error": str(latest.get("last_error") or ""),
            "path": self.path,
        }

    def retry_now(self) -> int:
        changed = 0
        with self._lock:
            for item in self._items.values():
                if float(item.get("next_attempt_at") or 0.0) > 0:
                    item["next_attempt_at"] = 0.0
                    changed += 1
            if changed:
                self._persist_locked()
        self._wake()
        return changed

    def ensure_started(self) -> bool:
        if not self.background_enabled or self._running or self._starting:
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._starting = True

        async def start_once() -> None:
            try:
                await self.start()
            finally:
                self._starting = False

        loop.create_task(start_once())
        return True

    async def start(self, *, reconcile: bool = True) -> bool:
        if self._running or not self.background_enabled:
            return False
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._event = asyncio.Event()
        if reconcile:
            try:
                await self.reconcile(include_archive=True)
            except Exception as exc:
                logger.warning("Embedding outbox startup reconciliation failed: %s", exc)
        self._task = asyncio.create_task(self._run(), name="ombre-embedding-outbox")
        self._wake()
        return True

    async def stop(self) -> None:
        self._running = False
        self._wake()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._event = None
        self._loop = None

    async def reconcile(self, *, include_archive: bool = True) -> int:
        engine = self.embedding_engine
        id_reader = getattr(engine, "list_content_ids", None)
        hash_reader = getattr(engine, "list_content_hashes", None)
        if not callable(id_reader):
            return 0
        try:
            indexed_ids = set(cast(Iterable[str], id_reader()))
            indexed_hashes = (
                dict(cast(Mapping[str, str], hash_reader()))
                if callable(hash_reader)
                else {}
            )
        except Exception as exc:
            logger.warning("Embedding outbox index reconciliation skipped: %s", exc)
            return 0

        queued = 0
        buckets = await self.bucket_mgr.list_all(include_archive=include_archive)
        list_letters = getattr(self.bucket_mgr, "list_letters", None)
        if callable(list_letters):
            read_letters = cast(
                Callable[[], Awaitable[list[dict[str, Any]]]], list_letters
            )
            buckets.extend(await read_letters())
        seen_ids: set[str] = set()
        for bucket in buckets:
            metadata = bucket.get("metadata") or {}
            if metadata.get("deleted_at") or metadata.get("tombstone"):
                continue
            bucket_id = str(bucket.get("id") or "").strip()
            if bucket_id in seen_ids:
                continue
            seen_ids.add(bucket_id)
            text = bucket_text_for_embedding(bucket)
            if not bucket_id or not text.strip():
                continue
            digest = content_hash(text)
            if bucket_id in indexed_ids and (
                not indexed_hashes or indexed_hashes.get(bucket_id) == digest
            ):
                continue
            if self.ensure_pending(bucket_id, text):
                queued += 1
        return queued

    async def process_once(self) -> bool:
        engine = self.embedding_engine
        if not engine or not getattr(engine, "enabled", False):
            return False
        bucket_id, item, _ = self._next_due()
        if not bucket_id or item is None:
            return False
        await self._process(bucket_id, item, engine)
        return True

    async def wait_until_idle(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if self.status()["pending"] == 0:
                return True
            await asyncio.sleep(0.02)
        return self.status()["pending"] == 0

    async def _run(self) -> None:
        while self._running:
            if self._event:
                self._event.clear()
            try:
                processed = await self.process_once()
            except Exception as exc:
                logger.exception("Embedding outbox worker error: %s", exc)
                processed = False
            if not processed:
                _, _, delay = self._next_due()
                await self._wait(delay)

    async def _process(self, bucket_id: str, item: dict[str, Any], engine: Any) -> None:
        bucket = await self.bucket_mgr.get(bucket_id)
        if not bucket:
            self.discard(bucket_id)
            return
        text = bucket_text_for_embedding(bucket)
        if not text.strip():
            self.discard(bucket_id)
            return
        digest = content_hash(text)
        if digest != item.get("content_hash"):
            self.enqueue(bucket_id, text)
            return
        try:
            ok = bool(await engine.generate_and_store(bucket_id, text))
        except Exception as exc:
            self._fail(bucket_id, digest, exc)
            return
        if not ok:
            self._fail(bucket_id, digest, "generate_and_store returned false")
            return

        latest = await self.bucket_mgr.get(bucket_id)
        if not latest:
            try:
                engine.delete_embedding(bucket_id)
            except Exception:
                pass
            self.discard(bucket_id)
            return
        latest_text = bucket_text_for_embedding(latest)
        if content_hash(latest_text) != digest:
            self.enqueue(bucket_id, latest_text)
            return
        self._complete(bucket_id, digest)

    def _complete(self, bucket_id: str, digest: str) -> None:
        with self._lock:
            current = self._items.get(bucket_id)
            if not current or current.get("content_hash") != digest:
                return
            self._items.pop(bucket_id, None)
            self._processed += 1
            self._last_success = now_iso()
            self._persist_locked()

    def _fail(self, bucket_id: str, digest: str, error: Any) -> None:
        with self._lock:
            current = self._items.get(bucket_id)
            if not current or current.get("content_hash") != digest:
                return
            attempts = int(current.get("attempts") or 0) + 1
            delay = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** min(attempts - 1, 16)))
            current.update(
                {
                    "attempts": attempts,
                    "last_attempt_at": now_iso(),
                    "next_attempt_at": time.time() + delay,
                    "last_error": str(error)[:240],
                }
            )
            self._persist_locked()
        logger.warning("Embedding refresh queued for retry: bucket=%s attempts=%s", bucket_id, attempts)

    def _next_due(self) -> tuple[str, dict[str, Any] | None, float]:
        now = time.time()
        with self._lock:
            if not self._items:
                return "", None, _IDLE_POLL_SECONDS
            bucket_id, item = min(
                self._items.items(),
                key=lambda pair: (
                    float(pair[1].get("next_attempt_at") or 0.0),
                    str(pair[1].get("queued_at") or ""),
                    pair[0],
                ),
            )
            due_at = float(item.get("next_attempt_at") or 0.0)
            if due_at <= now:
                return bucket_id, dict(item), 0.0
            return "", None, min(_IDLE_POLL_SECONDS, max(0.01, due_at - now))

    async def _wait(self, timeout: float) -> None:
        if not self._event:
            await asyncio.sleep(timeout)
            return
        try:
            await asyncio.wait_for(self._event.wait(), timeout=max(0.01, timeout))
        except asyncio.TimeoutError:
            pass

    def _wake(self) -> None:
        if not self._event or not self._loop or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._event.set)

    def _load_items(self) -> dict[str, dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as source:
                payload = json.load(source)
            raw_items = payload.get("items", {}) if isinstance(payload, dict) else {}
            if not isinstance(raw_items, dict):
                return {}
            return {
                str(bucket_id): dict(item)
                for bucket_id, item in raw_items.items()
                if bucket_id and isinstance(item, dict) and item.get("content_hash")
            }
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.warning("Embedding outbox is unreadable; rebuilding from buckets: %s", exc)
            return {}

    def _persist_locked(self) -> None:
        payload = {
            "version": _OUTBOX_VERSION,
            "updated_at": now_iso(),
            "items": self._items,
        }
        atomic_write_text(
            self.path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
