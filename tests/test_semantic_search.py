import pytest
from typing import cast

from semantic_search import SemanticSearchEngine, semantic_score_map


class _StrictEngine:
    async def search_similar_strict(self, query: str, top_k: int):
        assert (query, top_k) == ("amy", 3)
        return [("bucket", "0.75")]

    async def search_similar(self, query: str, top_k: int):
        raise AssertionError("strict search should be preferred")


class _FallbackEngine:
    async def search_similar(self, query: str, top_k: int):
        assert (query, top_k) == ("amy", 2)
        return [("fallback", 0.5)]


@pytest.mark.asyncio
async def test_semantic_score_map_prefers_strict_provider_contract() -> None:
    assert await semantic_score_map(_StrictEngine(), "amy", 3) == {"bucket": 0.75}


@pytest.mark.asyncio
async def test_semantic_score_map_uses_declared_fallback_contract() -> None:
    assert await semantic_score_map(_FallbackEngine(), "amy", 2) == {"fallback": 0.5}


@pytest.mark.asyncio
async def test_semantic_score_map_rejects_missing_search_contract() -> None:
    engine = cast(SemanticSearchEngine, object())
    with pytest.raises(TypeError, match="no callable search method"):
        await semantic_score_map(engine, "amy", 2)
