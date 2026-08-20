"""Runtime-scoped idempotency for public ``grow`` retries.

``grow`` may outlive an MCP/client response deadline because dehydration and
bucket creation are intentionally completed before the tool returns.  A user
who retries the same payload must therefore join/reuse the original operation
instead of starting a second dehydration pass and creating duplicate buckets.

The guard is deliberately bounded and process-local: it only recognizes exact
payload retries for a short window.  A later intentional grow of the same text
remains possible, and a failed operation is never cached.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from collections.abc import Awaitable, Callable


RETRY_WINDOW_SECONDS = 30 * 60

_IN_PROGRESS_MESSAGE = (
    "⏳ 相同的 grow 仍在后台处理中；无需重复提交，完成后会自动入库。"
)
_REUSED_RESULT_PREFIX = "✅ 已识别为刚才 grow 的重试；未重复写入。\n"


_state_lock = threading.RLock()
_inflight: set[str] = set()
_completed: dict[str, tuple[float, str]] = {}


def request_fingerprint(
    *,
    content: str,
    items: list | None,
    test_data: bool,
    extra: dict | None = None,
) -> str:
    """Return a privacy-preserving fingerprint for the exact public request."""

    normalized_content = (content or "").replace("\r\n", "\n").strip()
    payload = {
        "content": normalized_content,
        "items": items,
        "test_data": bool(test_data),
        "extra": extra or {},
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _consume_background_exception(task: asyncio.Task[str]) -> None:
    """Avoid an unobserved-task warning if the original client disconnected."""

    if not task.cancelled():
        task.exception()


async def run_once(
    fingerprint: str,
    operation: Callable[[], Awaitable[str]],
    *,
    retry_window_seconds: float = RETRY_WINDOW_SECONDS,
) -> str:
    """Run one grow request and safely recognize exact retries.

    The operation is shielded from cancellation by the request handler.  An
    identical retry while it is running receives an immediate progress result;
    a retry after completion reuses the prior result.  Exceptions remove the
    entry so a genuine failure can be retried normally.
    """

    now = time.monotonic()
    with _state_lock:
        expired = [
            key
            for key, (finished_at, _result) in _completed.items()
            if now - finished_at > retry_window_seconds
        ]
        for key in expired:
            _completed.pop(key, None)

        completed = _completed.get(fingerprint)
        if completed is not None:
            return _REUSED_RESULT_PREFIX + completed[1]

        if fingerprint in _inflight:
            return _IN_PROGRESS_MESSAGE

        # Reserve before creating the loop-local Task so a request entering on
        # another FastMCP loop/thread cannot start the same write concurrently.
        _inflight.add(fingerprint)

    async def execute() -> str:
        try:
            result = await operation()
        except BaseException:
            with _state_lock:
                _inflight.discard(fingerprint)
            raise
        with _state_lock:
            _inflight.discard(fingerprint)
            _completed[fingerprint] = (time.monotonic(), result)
        return result

    try:
        task = asyncio.create_task(execute(), name=f"grow:{fingerprint[:12]}")
    except BaseException:
        with _state_lock:
            _inflight.discard(fingerprint)
        raise
    task.add_done_callback(_consume_background_exception)

    return await asyncio.shield(task)


def reset_for_tests() -> None:
    """Clear process-local state.  Tests only; production never calls this."""

    with _state_lock:
        _inflight.clear()
        _completed.clear()
