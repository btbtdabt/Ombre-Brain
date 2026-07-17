"""Applicable P0 archive regressions for the root backup implementation."""

import hashlib
import io
import json
import os
import sqlite3
import stat
import tracemalloc
import zipfile
from pathlib import Path

import frontmatter
import pytest

import backup_archive as archive_mod
from backup_archive import (
    BackupArchiveError,
    build_export_archive,
    build_export_archive_file,
    extract_backup_archive_file,
    read_backup_archive,
)
from embedding_engine import EmbeddingEngine


class _Backend:
    @staticmethod
    def vector_dim():
        return 2


def _config(root):
    root.mkdir(parents=True, exist_ok=True)
    return {
        "buckets_dir": str(root),
        "state_dir": str(root / ".state"),
        "embedding": {"enabled": False},
    }


def _engine(config, model="test-embedding"):
    engine = EmbeddingEngine(config)
    engine.model = model
    setattr(engine, "_backend", _Backend())
    return engine


def _write_bucket(root, bucket_id="memory-1", content="important memory"):
    path = root / "dynamic" / "general" / f"memory_{bucket_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(
        content,
        id=bucket_id,
        name="Memory",
        type="dynamic",
        domain=["general"],
        created="2026-07-11T12:00:00",
    )
    path.write_text(frontmatter.dumps(post), encoding="utf-8")
    return path


def test_export_archive_has_verified_manifest_and_sqlite_snapshot(tmp_path):
    vault = tmp_path / "vault"
    bucket = _write_bucket(vault)
    engine = _engine(_config(vault))
    engine._store_embedding(
        "memory-1",
        [0.1, 0.2],
        content_hash="digest",
    )

    payload, manifest = build_export_archive(
        str(vault), engine.db_path, {"exported_at": "now", "version": "test"}
    )
    package = read_backup_archive(payload)

    assert package["integrity_verified"] is True
    assert package["integrity_warning"] == ""
    assert package["manifest"] == manifest
    assert package["files"]["buckets/dynamic/general/memory_memory-1.md"] == (
        bucket.read_bytes()
    )
    assert "embeddings.db" in package["files"]
    assert manifest["file_count"] == 3

    db_file = tmp_path / "snapshot.db"
    db_file.write_bytes(package["files"]["embeddings.db"])
    with sqlite3.connect(db_file) as connection:
        row = connection.execute(
            "SELECT bucket_id, content_hash FROM embeddings WHERE bucket_id = ?",
            ("memory-1",),
        ).fetchone()
    assert row == ("memory-1", "digest")


def test_disk_export_streams_sources_without_path_read_bytes(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    bucket = _write_bucket(vault, content="streamed memory " * 10_000)
    with bucket.open("rb") as handle:
        expected_bucket = handle.read()

    def forbid_materializing(_path):
        raise AssertionError("disk export must stream files instead of Path.read_bytes")

    monkeypatch.setattr(Path, "read_bytes", forbid_materializing)
    archive_path, manifest = build_export_archive_file(
        str(vault),
        "",
        {"exported_at": "now", "version": "test"},
    )
    try:
        with open(archive_path, "rb") as handle:
            package = read_backup_archive(handle.read())
        member = f"buckets/dynamic/general/{bucket.name}"
        assert package["files"][member] == expected_bucket
        assert package["manifest"] == manifest
    finally:
        os.unlink(archive_path)


def test_disk_export_aborts_and_cleans_temp_files_at_compressed_cap(
    tmp_path,
    monkeypatch,
):
    vault = tmp_path / "vault"
    path = vault / "dynamic" / "general" / "large.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(os.urandom(4096))
    created = []
    original_mkstemp = archive_mod.tempfile.mkstemp

    def tracked_mkstemp(*args, **kwargs):
        kwargs["dir"] = tmp_path
        fd, temp_path = original_mkstemp(*args, **kwargs)
        created.append(temp_path)
        return fd, temp_path

    monkeypatch.setattr(archive_mod.tempfile, "mkstemp", tracked_mkstemp)
    monkeypatch.setattr(archive_mod, "MAX_ARCHIVE_BYTES", 512)

    with pytest.raises(BackupArchiveError, match="压缩后"):
        build_export_archive_file(str(vault), "", {"version": "test"})

    assert created
    assert all(not os.path.exists(temp_path) for temp_path in created)


def test_reader_rejects_traversal_and_normalizes_legacy_windows_paths():
    malicious = io.BytesIO()
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("buckets/../../outside.md", b"bad")
    with pytest.raises(BackupArchiveError, match="不安全路径"):
        read_backup_archive(malicious.getvalue())

    legacy = io.BytesIO()
    with zipfile.ZipFile(legacy, "w") as archive:
        archive.writestr("buckets\\dynamic\\general\\old.md", b"legacy")
    package = read_backup_archive(legacy.getvalue())
    assert package["integrity_verified"] is False
    assert package["files"] == {"buckets/dynamic/general/old.md": b"legacy"}
    assert "旧版备份" in package["integrity_warning"]


def test_reader_rejects_symbolic_link_member():
    malicious = io.BytesIO()
    with zipfile.ZipFile(malicious, "w") as archive:
        info = zipfile.ZipInfo("buckets/dynamic/link.md")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"../../outside.md")

    with pytest.raises(BackupArchiveError, match="符号链接"):
        read_backup_archive(malicious.getvalue())


def test_disk_extractor_does_not_materialize_large_member_in_memory(tmp_path):
    archive_path = tmp_path / "large.zip"
    chunk = b"z" * (1024 * 1024)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        with archive.open("embeddings.db", "w") as member:
            for _ in range(32):
                member.write(chunk)

    destination = tmp_path / "extracted"
    tracemalloc.start()
    try:
        package = extract_backup_archive_file(str(archive_path), str(destination))
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    extracted = package["files"]["embeddings.db"]
    assert os.path.getsize(extracted) == 32 * 1024 * 1024
    assert peak < 8 * 1024 * 1024


@pytest.mark.parametrize("with_manifest", [False, True], ids=["legacy", "manifest"])
def test_production_extractor_rejects_every_unused_member(tmp_path, with_manifest):
    archive_path = tmp_path / "junk.zip"
    files = {
        "buckets/dynamic/valid.md": b"---\nid: valid\n---\nbody\n",
        "junk.bin": b"0" * (2 * 1024 * 1024),
    }
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        if with_manifest:
            manifest = {
                "schema_version": 1,
                "kind": "ombre-brain-backup",
                "created_at": "now",
                "version": "test",
                "file_count": len(files),
                "total_bytes": sum(len(data) for data in files.values()),
                "files": [
                    {
                        "path": name,
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                    for name, data in files.items()
                ],
            }
            archive.writestr("backup_manifest.json", json.dumps(manifest))

    destination = tmp_path / "extracted"
    with pytest.raises(BackupArchiveError, match="不支持的成员"):
        extract_backup_archive_file(str(archive_path), str(destination))

    assert not list(destination.glob("*"))


def test_production_extractor_enforces_path_specific_member_caps(
    tmp_path,
    monkeypatch,
):
    archive_path = tmp_path / "oversized-bucket.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("buckets/dynamic/too-large.md", b"x" * 65)

    monkeypatch.setattr(archive_mod, "MIGRATE_MAX_BUCKET_BYTES", 64)
    with pytest.raises(BackupArchiveError, match="成员过大"):
        extract_backup_archive_file(str(archive_path), str(tmp_path / "extracted"))


def test_production_extractor_does_not_fsync_each_temporary_member(
    tmp_path,
    monkeypatch,
):
    archive_path = tmp_path / "valid.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "buckets/dynamic/valid.md",
            b"---\nid: valid\n---\nbody\n",
        )

    monkeypatch.setattr(
        archive_mod.os,
        "fsync",
        lambda _fd: pytest.fail("temporary extraction must not fsync per member"),
    )
    package = extract_backup_archive_file(
        str(archive_path),
        str(tmp_path / "extracted"),
    )

    assert set(package["files"]) == {"buckets/dynamic/valid.md"}
