"""Bounded reverse line reader shared by log and error diagnostics."""

from __future__ import annotations

import os
from collections.abc import Iterator


DEFAULT_TAIL_CHUNK_BYTES = 64 * 1024


def iter_tail_lines(
    path: str,
    *,
    max_bytes: int,
    chunk_bytes: int = DEFAULT_TAIL_CHUNK_BYTES,
) -> Iterator[str]:
    """Yield complete UTF-8 lines newest-first from a bounded file tail."""

    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        remaining = max(0, int(max_bytes))
        carry = b""
        while position > 0 and remaining > 0:
            chunk_size = min(max(1, int(chunk_bytes)), position, remaining)
            position -= chunk_size
            remaining -= chunk_size
            handle.seek(position)
            block = handle.read(chunk_size) + carry
            parts = block.split(b"\n")
            carry = parts.pop(0)
            for raw_line in reversed(parts):
                yield raw_line.decode("utf-8", errors="replace")
        if position == 0 and carry:
            yield carry.decode("utf-8", errors="replace")
