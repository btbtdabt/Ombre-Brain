"""Compatibility coverage for the root runtime's smaller breath tools."""

import pytest


@pytest.mark.asyncio
async def test_breath_search_forwards_its_public_arguments(monkeypatch):
    from tools.current import memory

    seen = {}

    async def fake_breath(**kwargs):
        seen.update(kwargs)
        return "search result"

    monkeypatch.setattr(memory, "breath", fake_breath)

    result = await memory.breath_search(
        query="project boundary",
        domain="work",
        max_results=7,
        quotes=True,
    )

    assert result == "search result"
    assert seen == {
        "query": "project boundary",
        "domain": "work",
        "max_results": 7,
        "quotes": True,
    }


@pytest.mark.asyncio
async def test_breath_advanced_forwards_all_supported_arguments(monkeypatch):
    from tools.current import memory

    seen = {}

    async def fake_breath(**kwargs):
        seen.update(kwargs)
        return "advanced result"

    monkeypatch.setattr(memory, "breath", fake_breath)

    result = await memory.breath_advanced(
        query="specific date",
        max_tokens=4321,
        domain="relationship",
        date="2026-07-16",
        valence=0.4,
        arousal=0.7,
        max_results=9,
        include_related=False,
        retrieval_mode="lexical",
    )

    assert result == "advanced result"
    assert seen == {
        "query": "specific date",
        "max_tokens": 4321,
        "domain": "relationship",
        "date": "2026-07-16",
        "valence": 0.4,
        "arousal": 0.7,
        "max_results": 9,
        "importance_min": -1,
        "tags": "",
        "catalog": False,
        "include_related": False,
        "related_per_memory": 1,
        "edge_min_confidence": 0.55,
        "include_core": True,
        "core_limit": 3,
        "is_session_start": False,
        "debug": False,
        "surface": "manual",
        "direct_render_mode": "auto",
        "retrieval_mode": "lexical",
        "mode": "",
        "session_id": "",
    }


@pytest.mark.asyncio
async def test_breath_search_mcp_schema_is_small():
    import server

    listed = next(
        tool for tool in await server.mcp.list_tools() if tool.name == "breath_search"
    )
    assert set(listed.inputSchema["properties"]) == {
        "query",
        "domain",
        "max_results",
        "quotes",
    }
    assert listed.inputSchema["required"] == ["query"]
