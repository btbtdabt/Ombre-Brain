from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from backup_archive import (
    build_export_archive,
    build_export_archive_file,
    extract_backup_archive_file,
    read_backup_archive,
)
from media_store import (
    MediaPersistenceError,
    MediaStore,
    media_bucket_directory_name,
)


@pytest.mark.asyncio
async def test_media_paths_are_stable_and_restricted_to_upload_roots(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    allowed = uploads / "photo.png"
    allowed.write_bytes(b"allowed-image")
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    vault = tmp_path / "vault"
    store = MediaStore(
        str(vault),
        str(vault / "_media"),
        allowed_source_dirs=[str(uploads)],
    )

    assert media_bucket_directory_name("character/scene:one") == "character_scene_one"
    persisted = await store.persist("character/scene:one", str(allowed))
    stored_path = vault / persisted[0]["path"]
    assert stored_path.read_bytes() == b"allowed-image"
    assert stored_path.parent.name == "character_scene_one"

    with pytest.raises(MediaPersistenceError, match="data_base64"):
        await store.persist("bucket-2", str(outside))


def test_external_media_is_manifested_verified_and_stream_extractable(tmp_path):
    vault = tmp_path / "vault"
    bucket_path = vault / "dynamic" / "travel" / "memory.md"
    bucket_path.parent.mkdir(parents=True)
    bucket_path.write_text("---\nid: memory-1\n---\nA memory with media.\n", encoding="utf-8")
    media_dir = tmp_path / "persistent-media"
    media_path = media_dir / "memory-1" / "photo.png"
    media_path.parent.mkdir(parents=True)
    media_bytes = b"persistent-photo-bytes"
    media_path.write_bytes(media_bytes)
    metadata = {"version": "compat", "exported_at": "2026-07-16T12:00:00Z"}

    payload, manifest = build_export_archive(
        str(vault),
        "",
        metadata,
        media_dir=str(media_dir),
    )
    package = read_backup_archive(payload)
    assert package["integrity_verified"] is True
    assert package["files"]["media/memory-1/photo.png"] == media_bytes
    media_entry = next(
        item
        for item in manifest["files"]
        if item["path"] == "media/memory-1/photo.png"
    )
    assert media_entry["sha256"] == hashlib.sha256(media_bytes).hexdigest()

    archive_path, streamed_manifest = build_export_archive_file(
        str(vault),
        "",
        metadata,
        media_dir=str(media_dir),
    )
    destination = tmp_path / "extracted"
    try:
        extracted = extract_backup_archive_file(archive_path, str(destination))
    finally:
        Path(archive_path).unlink(missing_ok=True)
    assert extracted["integrity_verified"] is True
    assert streamed_manifest["file_count"] == manifest["file_count"]
    extracted_media = Path(extracted["files"]["media/memory-1/photo.png"])
    assert extracted_media.read_bytes() == media_bytes
