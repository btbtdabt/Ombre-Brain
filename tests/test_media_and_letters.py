import asyncio
import base64
from pathlib import Path

import pytest

from backup_manager import VaultBackupManager
from bucket_manager import BucketManager
from embedding_engine import EmbeddingEngine
from letter_service import LetterService
from media_store import MediaPersistenceError, MediaStore, media_bucket_directory_name
from utils import same_path


def test_shared_path_helpers_preserve_write_restore_identity(tmp_path: Path) -> None:
    bucket_id = "character/scene:one"
    assert media_bucket_directory_name(bucket_id) == "character_scene_one"

    relative = tmp_path / "vault" / "item.md"
    assert same_path(str(relative), str(tmp_path / "vault" / "." / "item.md"))


def _config(tmp_path: Path) -> dict:
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
        "embedding": {"enabled": False, "api_key": ""},
        "identity": {"ai_name": "Ombre", "user_name": "Amy"},
    }


@pytest.mark.asyncio
async def test_server_readable_temporary_file_is_copied(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "client-temp.png"
    source.write_bytes(b"image-bytes")
    store = MediaStore(str(vault), str(vault / "_media"))

    result = await store.persist("bucket-1", str(source))

    stored = vault / result[0]["path"]
    source.unlink()
    assert stored.read_bytes() == b"image-bytes"
    assert result[0]["stored"] is True
    assert result[0]["path"].startswith("_media/bucket-1/")


@pytest.mark.asyncio
async def test_base64_media_is_persisted_with_original_suffix(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    store = MediaStore(str(vault), str(vault / "_media"))
    payload = base64.b64encode(b"sound-bytes").decode("ascii")

    result = await store.persist(
        "bucket-2",
        [{"data_base64": payload, "filename": "voice.ogg", "type": "audio/ogg"}],
    )

    stored = vault / result[0]["path"]
    assert stored.suffix == ".ogg"
    assert stored.read_bytes() == b"sound-bytes"


@pytest.mark.asyncio
async def test_unreadable_client_temporary_path_is_rejected(tmp_path: Path) -> None:
    store = MediaStore(str(tmp_path / "vault"), str(tmp_path / "vault" / "_media"))

    with pytest.raises(MediaPersistenceError, match="data_base64"):
        await store.persist("bucket-3", "/client-only/temporary/photo.png")


@pytest.mark.asyncio
async def test_server_path_outside_configured_upload_root_is_rejected(tmp_path: Path) -> None:
    allowed = tmp_path / "uploads"
    allowed.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    store = MediaStore(
        str(tmp_path / "vault"),
        str(tmp_path / "vault" / "_media"),
        allowed_source_dirs=[str(allowed)],
    )

    with pytest.raises(MediaPersistenceError, match="data_base64"):
        await store.persist("bucket-4", str(outside))


def test_bucket_media_round_trips_through_verified_backup(tmp_path: Path) -> None:
    source_config = _config(tmp_path / "source")
    source_manager = BucketManager(source_config)
    payload = base64.b64encode(b"persistent-photo").decode("ascii")
    bucket_id = asyncio.run(
        source_manager.create(
            content="photo memory",
            media={"data_base64": payload, "filename": "photo.png", "type": "image/png"},
        )
    )
    original = asyncio.run(source_manager.get(bucket_id))
    assert original is not None
    media_path = original["metadata"]["media"][0]["path"]

    backup = VaultBackupManager(
        source_config, source_manager, EmbeddingEngine(source_config)
    )
    archive_path, _ = backup.create_archive()

    target_config = _config(tmp_path / "target")
    target_manager = BucketManager(target_config)
    target_backup = VaultBackupManager(
        target_config, target_manager, EmbeddingEngine(target_config)
    )
    try:
        asyncio.run(target_backup.restore_archive(archive_path, mode="overwrite"))
    finally:
        Path(archive_path).unlink(missing_ok=True)

    restored = asyncio.run(target_manager.get(bucket_id))
    assert restored is not None
    assert restored["metadata"]["media"][0]["path"] == media_path
    assert (Path(target_config["buckets_dir"]) / media_path).read_bytes() == b"persistent-photo"


def test_external_markdown_media_round_trips_without_becoming_a_bucket(tmp_path: Path) -> None:
    source_config = _config(tmp_path / "source")
    source_config["media_dir"] = str(tmp_path / "source-media")
    source_manager = BucketManager(source_config)
    payload = base64.b64encode(b"markdown attachment").decode("ascii")
    bucket_id = asyncio.run(
        source_manager.create(
            content="attached notes",
            media={"data_base64": payload, "filename": "notes.md", "type": "text/markdown"},
        )
    )
    backup = VaultBackupManager(
        source_config, source_manager, EmbeddingEngine(source_config)
    )
    archive_path, _ = backup.create_archive()

    target_config = _config(tmp_path / "target")
    target_config["media_dir"] = str(tmp_path / "target-media")
    target_manager = BucketManager(target_config)
    target_backup = VaultBackupManager(
        target_config, target_manager, EmbeddingEngine(target_config)
    )
    try:
        result = asyncio.run(target_backup.restore_archive(archive_path, mode="overwrite"))
    finally:
        Path(archive_path).unlink(missing_ok=True)

    restored = asyncio.run(target_manager.get(bucket_id))
    assert restored is not None
    restored_path = Path(restored["metadata"]["media"][0]["path"])
    assert result["created"] == 1
    assert restored_path.is_absolute()
    assert restored_path.is_relative_to(Path(target_config["media_dir"]))
    assert restored_path.read_bytes() == b"markdown attachment"


def test_letters_are_isolated_and_preserve_author_semantics(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manager = BucketManager(config)
    letters = LetterService(config, manager, EmbeddingEngine(config))

    ai_result = asyncio.run(letters.write(author="claude", content="from ai"))
    user_result = asyncio.run(
        letters.write(author="user", content="from user", user_name="Amy")
    )
    custom_result = asyncio.run(letters.write(author="Nova", content="from nova"))

    assert "[Ombre]" in ai_result
    assert "[user]" in user_result
    assert "[Nova]" in custom_result
    assert asyncio.run(manager.list_all()) == []

    all_letters = asyncio.run(manager.list_letters())
    assert {item["metadata"]["author"] for item in all_letters} == {
        "Ombre",
        "user",
        "Nova",
    }
    assert "from ai" in asyncio.run(letters.read(author="ai"))
    assert "from user" not in asyncio.run(letters.read(author="ai"))
    assert "from nova" in asyncio.run(letters.read(author="Nova"))


def test_letter_read_falls_back_to_lexical_when_global_vector_hits_are_not_letters(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    manager = BucketManager(config)

    class OrdinaryOnlyEmbedding:
        enabled = True

        async def search_similar(self, query, top_k=5):
            return [("ordinary-memory", 0.99)]

    letters = LetterService(config, manager, OrdinaryOnlyEmbedding())
    asyncio.run(letters.write(author="Amy", content="the silver lighthouse"))

    assert "silver lighthouse" in asyncio.run(letters.read(query="lighthouse"))


def test_letter_explicit_ai_name_overrides_configured_identity(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manager = BucketManager(config)
    letters = LetterService(config, manager, EmbeddingEngine(config))

    result = asyncio.run(
        letters.write(author="ai", content="signed explicitly", ai_name="Nocturne")
    )

    assert "[Nocturne]" in result
    stored = asyncio.run(manager.list_letters())
    assert stored[0]["metadata"]["author"] == "Nocturne"


def test_letter_rejects_empty_author(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manager = BucketManager(config)
    letters = LetterService(config, manager, EmbeddingEngine(config))

    result = asyncio.run(letters.write(author="   ", content="unsigned"))

    assert result == "author 不能为空。"
    assert asyncio.run(manager.list_letters()) == []


def test_letter_ai_filter_includes_legacy_claude_signature(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manager = BucketManager(config)
    letters = LetterService(config, manager, EmbeddingEngine(config))
    asyncio.run(
        manager.create(
            content="legacy Claude letter",
            bucket_type="letter",
            domain=["letter"],
            extra_metadata={"author": "claude", "source_tool": "letter"},
        )
    )
    asyncio.run(letters.write(author="user", content="human letter"))

    result = asyncio.run(letters.read(author="ai"))

    assert "legacy Claude letter" in result
    assert "human letter" not in result


def test_letter_query_filters_lexically_when_embeddings_are_disabled(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    manager = BucketManager(config)
    letters = LetterService(config, manager, EmbeddingEngine(config))
    asyncio.run(letters.write(author="Amy", content="A letter about apples and orchards."))
    asyncio.run(letters.write(author="Amy", content="A letter about trains and stations."))

    missing = asyncio.run(letters.read(query="nonexistent zebra phrase"))
    apples = asyncio.run(letters.read(query="orchards"))

    assert "没有找到匹配的信件" in missing
    assert "apples and orchards" in apples
    assert "trains and stations" not in apples


def test_pulse_counts_and_lists_isolated_letters(tmp_path: Path, monkeypatch) -> None:
    import server

    config = _config(tmp_path)
    manager = BucketManager(config)
    letters = LetterService(config, manager, EmbeddingEngine(config))
    asyncio.run(letters.write(author="Amy", content="a permanent letter", title="Hello"))

    class DecayStub:
        is_running = False

        @staticmethod
        def calculate_score(metadata):
            return 1.0

    monkeypatch.setattr(server, "bucket_mgr", manager)
    monkeypatch.setattr(server, "decay_engine", DecayStub())
    result = asyncio.run(server.pulse())

    assert "独立信件: 1 封" in result
    assert "当前显示桶: 1 个" in result
    assert "✉️" in result
    assert "Hello" in result
