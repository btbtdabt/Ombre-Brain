"""Compatibility coverage for the root runtime's smaller breath tools."""

import pytest


@pytest.mark.asyncio
async def test_breath_search_forwards_its_public_arguments(monkeypatch):
    import server

    seen = {}

    async def fake_breath(**kwargs):
        seen.update(kwargs)
        return "search result"

    monkeypatch.setattr(server, "breath", fake_breath)

    result = await server.breath_search(
        query="project boundary",
        domain="work",
        max_results=7,
    )

    assert result == "search result"
    assert seen == {
        "query": "project boundary",
        "domain": "work",
        "max_results": 7,
    }


@pytest.mark.asyncio
async def test_breath_advanced_forwards_all_supported_arguments(monkeypatch):
    import server

    seen = {}

    async def fake_breath(**kwargs):
        seen.update(kwargs)
        return "advanced result"

    monkeypatch.setattr(server, "breath", fake_breath)

    result = await server.breath_advanced(
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
        "include_related": False,
        "retrieval_mode": "lexical",
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
    }
    assert listed.inputSchema["required"] == ["query"]
