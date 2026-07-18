from __future__ import annotations

import ast
import inspect

import pytest

from tools import _runtime as tool_runtime
from tools.current.manifest import P0_TOOL_NAMES, REGISTERED_TOOL_NAMES, TOOL_BY_NAME
from web import _shared as web_runtime
from web.current_contract import CURRENT_ROUTE_KEYS


@pytest.mark.asyncio
async def test_server_exposes_the_exact_current_and_p0_tool_union() -> None:
    import server

    tools = await server.mcp.list_tools()

    assert {tool.name for tool in tools} == set(REGISTERED_TOOL_NAMES)
    assert set(server._current_registered_tools) == set(REGISTERED_TOOL_NAMES)
    breath = next(tool for tool in tools if tool.name == "breath")
    assert breath.inputSchema["properties"] == {}
    advanced = next(tool for tool in tools if tool.name == "breath_advanced")
    assert "mode" in advanced.inputSchema["properties"]
    assert "session_id" in advanced.inputSchema["properties"]
    assert "catalog" in advanced.inputSchema["properties"]


def test_server_registers_tools_only_from_the_canonical_manifest() -> None:
    import server

    tree = ast.parse(inspect.getsource(server))
    decorated_tools = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            func = call.func if call is not None else decorator
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "tool"
                and isinstance(func.value, ast.Name)
                and func.value.id in {"mcp", "mcp_extra"}
            ):
                decorated_tools.append(node.name)

    assert decorated_tools == []
    assert not hasattr(server, "mcp_extra")
    for name in P0_TOOL_NAMES:
        assert getattr(server, name) is TOOL_BY_NAME[name].handler


def test_server_registers_current_routes_after_the_p0_surface() -> None:
    import server

    routes = server.mcp._custom_starlette_routes
    observed = {
        (method, route.path)
        for route in routes
        for method in (route.methods or ())
        if method != "HEAD"
    }

    assert CURRENT_ROUTE_KEYS <= observed
    assert server._current_web_report.registered == frozenset(CURRENT_ROUTE_KEYS)
    assert not server._current_web_report.missing_required_services
    paths = [route.path for route in routes]
    assert paths.index("/dashboard-assets/{name}") < paths.index(
        "/dashboard-assets/{path:path}"
    )


def test_server_shares_one_current_runtime_across_tools_and_web() -> None:
    import server

    runtime = server.current_runtime
    for name in (
        "reminder_store",
        "darkroom_store",
        "memory_edge_store",
        "memory_moment_store",
        "memory_node_store",
        "entity_edge_store",
        "persona_engine",
        "portrait_engine",
        "dream_engine",
        "raw_event_store",
        "gateway_state_store",
        "identity_semantic_store",
        "word_map_store",
        "reflection_engine",
    ):
        expected = getattr(runtime, name)
        assert getattr(tool_runtime, name) is expected
        assert getattr(web_runtime, name) is expected

    assert tool_runtime.letter_service is runtime.letter_service
    assert getattr(web_runtime, "services") is runtime.web_services
