"""MCP-boundary regressions for the split breath tool.

The public schema intentionally remains parameter-free so clients auto-load
the default surfacing tool.  A client may nevertheless keep the pre-2.6.8
schema cached across an upgrade and send the former breath arguments.  Those
arguments must reach dispatch instead of being silently discarded by
FastMCP/Pydantic's default ``extra=ignore`` validation.
"""

import pytest
from mcp.server.fastmcp.exceptions import ToolError


QUERY = "两个都是你 怎么还让我选"


@pytest.mark.asyncio
async def test_public_breath_schema_stays_empty_but_cached_query_args_are_forwarded(
    monkeypatch,
):
    import server
    from tools.current import memory

    seen = {}

    async def fake_search(**kwargs):
        seen.update(kwargs)
        return "query-dispatched"

    monkeypatch.setattr(memory, "search_breath", fake_search)
    tool = server.mcp._tool_manager.get_tool("breath")
    assert tool is not None

    listed = next(item for item in await server.mcp.list_tools() if item.name == "breath")
    assert listed.inputSchema["properties"] == {}
    assert {
        "query", "max_tokens", "domain", "date", "valence", "arousal",
        "max_results", "importance_min", "tags", "catalog", "mode",
        "session_id", "retrieval_mode",
    } <= set(tool.fn_metadata.arg_model.model_fields)

    output = await tool.run(
        {
            "query": QUERY,
            "max_results": 1,
            "max_tokens": 6000,
        }
    )

    assert output == "query-dispatched"
    assert seen["query"] == QUERY
    assert seen["max_tokens"] == 6000
    assert seen["max_results"] == 1
    assert seen["retrieval_mode"] == "graph"


@pytest.mark.asyncio
async def test_cached_catalog_arg_reaches_breath_dispatch(monkeypatch):
    import server
    from tools.current import memory

    seen = {}

    async def fake_dispatch(**kwargs):
        seen.update(kwargs)
        return "catalog-dispatched"

    monkeypatch.setattr(memory, "p0_breath_dispatch", fake_dispatch)
    tool = server.mcp._tool_manager.get_tool("breath")
    assert tool is not None

    output = await tool.run(
        {
            "query": QUERY,
            "catalog": True,
            "max_results": 3,
            "max_tokens": 6000,
        }
    )

    assert output == "catalog-dispatched"
    assert seen["query"] == QUERY
    assert seen["catalog"] is True
    assert seen["max_results"] == 3
    assert seen["max_tokens"] == 6000


@pytest.mark.asyncio
async def test_parameter_free_breath_still_dispatches_with_all_defaults(monkeypatch):
    import server
    from tools.current import memory

    seen = {}

    async def fake_surface(**kwargs):
        seen.update(kwargs)
        return "default-dispatched"

    monkeypatch.setattr(memory, "surface_breath", fake_surface)
    tool = server.mcp._tool_manager.get_tool("breath")
    assert tool is not None

    assert await tool.run({}) == "default-dispatched"
    assert seen["max_tokens"] == 10000
    assert seen["max_results"] == 20
    assert seen["include_related"] is True
    assert seen["auto_surface"] is False


@pytest.mark.asyncio
async def test_unknown_cached_breath_argument_is_rejected_instead_of_ignored():
    import server

    tool = server.mcp._tool_manager.get_tool("breath")
    assert tool is not None

    with pytest.raises(ToolError, match="extra_forbidden"):
        await tool.run({"query": QUERY, "max_result": 1})
