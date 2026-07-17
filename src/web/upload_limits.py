"""Shared request-stream limits for multipart upload routes."""

from __future__ import annotations

from starlette.requests import Request


DEFAULT_MULTIPART_OVERHEAD_BYTES = 1024 * 1024


async def read_multipart_form_limited(
    request: Request,
    payload_limit: int,
    *,
    overhead_limit: int = DEFAULT_MULTIPART_OVERHEAD_BYTES,
):
    """Parse one file upload while bounding bytes before parser spooling."""

    request_limit = max(1, int(payload_limit)) + max(0, int(overhead_limit))
    raw_length = str(request.headers.get("content-length", "") or "").strip()
    if raw_length:
        try:
            declared_length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if declared_length < 0 or declared_length > request_limit:
            raise ValueError(
                "Upload too large "
                f"({declared_length} bytes > {request_limit} byte request limit)"
            )

    original_receive = request._receive
    received = 0

    async def limited_receive():
        nonlocal received
        message = await original_receive()
        if isinstance(message, dict) and message.get("type") == "http.request":
            received += len(message.get("body", b""))
            if received > request_limit:
                raise ValueError(
                    "Upload too large "
                    f"({received} bytes > {request_limit} byte request limit)"
                )
        return message

    request._receive = limited_receive
    try:
        return await request.form(
            max_files=1,
            max_fields=8,
            max_part_size=64 * 1024,
        )
    finally:
        request._receive = original_receive


__all__ = ["DEFAULT_MULTIPART_OVERHEAD_BYTES", "read_multipart_form_limited"]
