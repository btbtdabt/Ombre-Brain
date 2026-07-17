"""Applicable P0 atomic text-write regressions for the shared helper."""

import pytest

import utils


def test_atomic_write_text_writes_content(tmp_path):
    path = tmp_path / "sub" / "memory.md"
    utils.atomic_write_text(path, "hello memory")
    assert path.read_text(encoding="utf-8") == "hello memory"


def test_atomic_write_text_leaves_no_temporary_file(tmp_path):
    path = tmp_path / "memory.md"
    utils.atomic_write_text(path, "content")
    assert list(tmp_path.glob(".memory.md.tmp.*")) == []


def test_atomic_write_text_overwrites_fully(tmp_path):
    path = tmp_path / "memory.md"
    utils.atomic_write_text(path, "a much longer original value")
    utils.atomic_write_text(path, "new")
    assert path.read_text(encoding="utf-8") == "new"


def test_atomic_write_failure_preserves_original_and_cleans_temp(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "memory.md"
    utils.atomic_write_text(path, "original memory")
    monkeypatch.setattr(
        utils.os,
        "replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        utils.atomic_write_text(path, "replacement")

    assert path.read_text(encoding="utf-8") == "original memory"
    assert list(tmp_path.glob(".memory.md.tmp.*")) == []
