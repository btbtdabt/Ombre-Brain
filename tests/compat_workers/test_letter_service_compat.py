import pytest

from letter_service import LetterService
from tools import _runtime as tool_runtime
from tools.plan.core import letter_read
from tests.compat_workers.support import LegacyBucketManager


class DisabledEmbedding:
    enabled = False

    async def search_similar(self, query, top_k=5):
        return []


@pytest.mark.asyncio
async def test_letters_preserve_author_metadata_and_filtering(worker_config):
    manager = LegacyBucketManager(worker_config)
    letters = LetterService(worker_config, manager, DisabledEmbedding())

    ai_result = await letters.write(author="claude", content="from ai")
    user_result = await letters.write(author="user", content="from user", user_name="Amy")
    custom_result = await letters.write(author="Nova", content="from nova")

    assert "[Ombre]" in ai_result
    assert "[user]" in user_result
    assert "[Nova]" in custom_result
    stored = await manager.list_letters()
    assert {bucket["metadata"].get("author") for bucket in stored} == {
        "Ombre",
        "user",
        "Nova",
    }
    assert "from ai" in await letters.read(author="ai")
    assert "from user" not in await letters.read(author="ai")
    assert "from nova" in await letters.read(author="Nova")


@pytest.mark.asyncio
async def test_letter_ai_filter_includes_legacy_claude_signature(worker_config):
    manager = LegacyBucketManager(worker_config)
    legacy_id = await manager.create(
        content="legacy Claude letter",
        bucket_type="letter",
        domain=["letter"],
    )
    await manager.update(legacy_id, author="claude", source_tool="letter")
    letters = LetterService(worker_config, manager, DisabledEmbedding())
    await letters.write(author="user", content="human letter")

    result = await letters.read(author="ai")

    assert "legacy Claude letter" in result
    assert "human letter" not in result


@pytest.mark.asyncio
async def test_letter_read_uses_lexical_fallback_and_rejects_empty_author(worker_config):
    manager = LegacyBucketManager(worker_config)
    letters = LetterService(worker_config, manager, DisabledEmbedding())
    await letters.write(author="Amy", content="A letter about apples and orchards.")
    await letters.write(author="Amy", content="A letter about trains and stations.")

    missing = await letters.read(query="nonexistent zebra phrase")
    apples = await letters.read(query="orchards")
    rejected = await letters.write(author="   ", content="unsigned")

    assert "没有找到匹配的信件" in missing
    assert "apples and orchards" in apples
    assert "trains and stations" not in apples
    assert rejected == "author 不能为空。"


@pytest.mark.asyncio
async def test_letter_read_falls_back_when_vector_hits_are_not_letters(worker_config):
    manager = LegacyBucketManager(worker_config)
    ordinary_id = await manager.create(content="ordinary memory")

    class OrdinaryOnlyEmbedding:
        enabled = True

        async def search_similar(self, query, top_k=5):
            return [(ordinary_id, 0.99)]

    letters = LetterService(worker_config, manager, OrdinaryOnlyEmbedding())
    await letters.write(author="Amy", content="the silver lighthouse")

    assert "silver lighthouse" in await letters.read(query="lighthouse")


@pytest.mark.asyncio
async def test_mcp_letter_read_supports_legacy_list_all_only_manager(monkeypatch):
    class ListAllOnlyManager:
        def __init__(self):
            self.include_archive = None

        async def list_all(self, *, include_archive=False):
            self.include_archive = include_archive
            return [
                {
                    "id": "legacy-letter",
                    "content": "A letter preserved through the legacy manager.",
                    "metadata": {
                        "type": "letter",
                        "author": "Amy",
                        "letter_date": "2026-07-16",
                    },
                },
                {
                    "id": "ordinary-memory",
                    "content": "Not a letter.",
                    "metadata": {"type": "permanent", "author": "Amy"},
                },
            ]

    manager = ListAllOnlyManager()
    monkeypatch.setattr(tool_runtime, "bucket_mgr", manager)
    monkeypatch.setattr(tool_runtime, "embedding_engine", None)

    result = await letter_read(author="Amy")

    assert manager.include_archive is False
    assert "legacy manager" in result
    assert "Not a letter" not in result
