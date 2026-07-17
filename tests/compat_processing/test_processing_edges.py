from __future__ import annotations

from datetime import datetime, timezone

import frontmatter

from decay_engine import DecayEngine
import write_memory


class EmptyBucketManager:
    pass


def test_decay_keeps_historical_timezone_helpers():
    parsed = DecayEngine._parse_datetime("2026-07-16T12:00:00-04:00")

    assert parsed == datetime(2026, 7, 16, 16, 0, 0)
    assert DecayEngine._now_naive_utc().tzinfo is None
    assert abs(
        (datetime.now(timezone.utc).replace(tzinfo=None) - DecayEngine._now_naive_utc()).total_seconds()
    ) < 2


def test_manual_writer_preserves_historical_initial_activation(tmp_path, monkeypatch):
    monkeypatch.setattr(write_memory, "VAULT_DIR", str(tmp_path))
    monkeypatch.setattr(write_memory, "_max_bucket_bytes", lambda: 1024)

    bucket_id = write_memory.write_memory(
        "manual memory",
        "exact body",
        ["life"],
        ["manual"],
    )

    post = frontmatter.load(tmp_path / f"{bucket_id}.md")
    assert post["activation_count"] == 1
