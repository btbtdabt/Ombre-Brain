import asyncio
import zipfile
from pathlib import Path

import pytest
import frontmatter

from ombrebrain.storage.backup_archive import (
    BackupArchiveError,
    build_export_archive_file,
    extract_backup_archive_file,
)
from backup_manager import VaultBackupManager
from bucket_manager import BucketManager
from embedding_engine import EmbeddingEngine
from utils import get_version


def _config(tmp_path):
    buckets_dir = tmp_path / "buckets"
    state_dir = tmp_path / "state"
    buckets_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    return {
        "buckets_dir": str(buckets_dir),
        "state_dir": str(state_dir),
        "matching": {},
        "scoring_weights": {},
        "wikilink": {},
        "dehydration": {"api_key": ""},
        "embedding": {"enabled": False, "api_key": ""},
    }


def test_backup_archive_round_trip_has_verified_manifest(tmp_path):
    config = _config(tmp_path)
    manager = BucketManager(config)
    bucket_id = asyncio.run(manager.create(content="remember this", name="Memory"))
    engine = EmbeddingEngine(config)

    archive_path, manifest = build_export_archive_file(
        config["buckets_dir"],
        engine.db_path,
        {"exported_at": "2026-07-16T00:00:00", "version": "test"},
    )
    extraction = tmp_path / "extracted"
    try:
        parsed = extract_backup_archive_file(archive_path, str(extraction))
    finally:
        Path(archive_path).unlink(missing_ok=True)

    assert parsed["integrity_verified"] is True
    assert parsed["manifest"] == manifest
    assert any(path.startswith("buckets/") and bucket_id in path for path in parsed["files"])
    assert "embeddings.db" in parsed["files"]
    assert "export_meta.json" in parsed["files"]


def test_backup_archive_rejects_tampered_member(tmp_path):
    config = _config(tmp_path)
    manager = BucketManager(config)
    asyncio.run(manager.create(content="original", name="Memory"))
    engine = EmbeddingEngine(config)
    archive_path, _ = build_export_archive_file(
        config["buckets_dir"], engine.db_path, {"exported_at": "now", "version": "test"}
    )
    tampered = tmp_path / "tampered.zip"
    try:
        with zipfile.ZipFile(archive_path, "r") as source, zipfile.ZipFile(tampered, "w") as target:
            for info in source.infolist():
                payload = source.read(info)
                if info.filename.startswith("buckets/"):
                    payload = bytes([payload[0] ^ 1]) + payload[1:]
                target.writestr(info, payload)
    finally:
        Path(archive_path).unlink(missing_ok=True)

    with pytest.raises(BackupArchiveError, match="SHA-256"):
        extract_backup_archive_file(str(tampered), str(tmp_path / "rejected"))


def test_restore_overwrite_replaces_bucket_and_skip_preserves_existing(tmp_path):
    source_config = _config(tmp_path / "source")
    source_manager = BucketManager(source_config)
    bucket_id = asyncio.run(
        source_manager.create(content="source truth", name="Shared", bucket_id="shared-id")
    )
    source_backup = VaultBackupManager(
        source_config,
        source_manager,
        EmbeddingEngine(source_config),
    )
    archive_path, _ = source_backup.create_archive()

    target_config = _config(tmp_path / "target")
    target_manager = BucketManager(target_config)
    asyncio.run(target_manager.create(content="local version", name="Shared", bucket_id=bucket_id))
    target_backup = VaultBackupManager(
        target_config,
        target_manager,
        EmbeddingEngine(target_config),
    )
    try:
        skipped = asyncio.run(target_backup.restore_archive(archive_path, mode="skip"))
        assert skipped["skipped"] == 1
        current = asyncio.run(target_manager.get(bucket_id))
        assert current is not None
        assert current["content"] == "local version"

        restored = asyncio.run(target_backup.restore_archive(archive_path, mode="overwrite"))
        assert restored["overwritten"] == 1
        current = asyncio.run(target_manager.get(bucket_id))
        assert current is not None
        assert current["content"] == "source truth"
    finally:
        Path(archive_path).unlink(missing_ok=True)


def test_backup_contains_no_runtime_secrets(tmp_path):
    config = _config(tmp_path)
    config["dehydration"]["api_key"] = "secret-dehydration-key"
    manager = BucketManager(config)
    asyncio.run(manager.create(content="ordinary memory"))
    backup = VaultBackupManager(config, manager, EmbeddingEngine(config))
    archive_path, _ = backup.create_archive()
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            names = set(archive.namelist())
            combined = b"\n".join(archive.read(name) for name in names)
    finally:
        Path(archive_path).unlink(missing_ok=True)

    assert ".env" not in names
    assert "config.yaml" not in names
    assert b"secret-dehydration-key" not in combined


def test_create_archive_uses_runtime_version_when_config_omits_version(tmp_path):
    config = _config(tmp_path)
    manager = BucketManager(config)
    asyncio.run(manager.create(content="ordinary memory"))
    backup = VaultBackupManager(config, manager, EmbeddingEngine(config))

    archive_path, manifest = backup.create_archive()
    try:
        assert manifest["version"] == get_version()
    finally:
        Path(archive_path).unlink(missing_ok=True)


def test_create_archive_prefers_explicit_config_version(tmp_path):
    config = _config(tmp_path)
    config["version"] = "custom-export-version"
    manager = BucketManager(config)
    asyncio.run(manager.create(content="ordinary memory"))
    backup = VaultBackupManager(config, manager, EmbeddingEngine(config))

    archive_path, manifest = backup.create_archive()
    try:
        assert manifest["version"] == "custom-export-version"
    finally:
        Path(archive_path).unlink(missing_ok=True)


def test_restore_rejects_target_path_owned_by_a_different_bucket(tmp_path):
    source_config = _config(tmp_path / "source")
    source_manager = BucketManager(source_config)
    asyncio.run(
        source_manager.create(
            content="source",
            name="Collision",
            bucket_id="source-id",
        )
    )
    source_backup = VaultBackupManager(
        source_config, source_manager, EmbeddingEngine(source_config)
    )
    archive_path, _ = source_backup.create_archive()

    target_config = _config(tmp_path / "target")
    target_manager = BucketManager(target_config)
    target_backup = VaultBackupManager(
        target_config, target_manager, EmbeddingEngine(target_config)
    )
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            member = next(
                name
                for name in archive.namelist()
                if name.startswith("buckets/") and name.endswith(".md")
            )
        target_path = Path(target_config["buckets_dir"]).joinpath(*member.split("/")[1:])
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            frontmatter.dumps(
                frontmatter.Post(
                    "keep me",
                    id="different-id",
                    name="Different",
                    type="dynamic",
                    domain=["未分类"],
                )
            ),
            encoding="utf-8",
        )

        with pytest.raises(BackupArchiveError, match="其他 bucket"):
            asyncio.run(target_backup.restore_archive(archive_path, mode="overwrite"))
        assert "keep me" in target_path.read_text(encoding="utf-8")
    finally:
        Path(archive_path).unlink(missing_ok=True)
