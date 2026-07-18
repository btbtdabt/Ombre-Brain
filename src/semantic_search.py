"""Shared semantic-search invocation for MCP and Dashboard adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Iterable
from typing import Any, Protocol, cast


class _AsyncSemanticSearch(Protocol):
    def __call__(
        self,
        query: str,
        top_k: int,
    ) -> Awaitable[Iterable[tuple[Any, Any]]]: ...


class SemanticSearchEngine(Protocol):
    def search_similar(
        self,
        query: str,
        top_k: int,
    ) -> Awaitable[Iterable[tuple[Any, Any]]]: ...


async def semantic_score_map(
    engine: SemanticSearchEngine,
    query: str,
    top_k: int,
) -> dict[str, float]:
    strict_search = getattr(engine, "search_similar_strict", None)
    if callable(strict_search):
        search = cast(_AsyncSemanticSearch, strict_search)
    else:
        fallback_search = getattr(engine, "search_similar", None)
        if not callable(fallback_search):
            raise TypeError("semantic search engine has no callable search method")
        search = cast(_AsyncSemanticSearch, fallback_search)
    pairs = await search(query, top_k=top_k)
    return {str(bucket_id): float(score) for bucket_id, score in pairs}
