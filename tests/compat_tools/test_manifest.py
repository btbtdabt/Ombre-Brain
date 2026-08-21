from __future__ import annotations

import inspect

import pytest
from mcp.server.fastmcp import FastMCP

from tests.mcp_contract_snapshot import EXPECTED_PARAMETERS
from tools import _runtime as runtime
from tools import current


EXPECTED_CURRENT_TOOL_NAMES = {
    "entity_edge_backfill",
    "reminder_create",
    "reminder_list",
    "reminder_update",
    "breath",
    "breath_search",
    "breath_advanced",
    "feel",
    "read_bucket",
    "list_buckets_light",
    "letter_write",
    "letter_lock_update",
    "letter_read",
    "comment_bucket",
    "delete_bucket_comment",
    "hold",
    "darkroom_enter",
    "darkroom_rooms",
    "darkroom_delete",
    "darkroom_view",
    "grow",
    "profile_fact",
    "trace",
    "pulse",
    "introspection",
}

EXPECTED_P0_TOOL_NAMES = {
    "breath",
    "breath_search",
    "breath_advanced",
    "feel",
    "hold",
    "grow",
    "source_read",
    "source_attach",
    "source_detach",
    "source_restore",
    "relation_read",
    "relation_attach",
    "relation_detach",
    "relation_restore",
    "trace",
    "anchor",
    "release",
    "pulse",
    "plan",
    "letter_write",
    "letter_lock_update",
    "letter_read",
    "I",
    "dream",
}


DESCRIPTION_MARKERS = {
    "reminder_create": "创建独立照顾备忘",
    "breath": "只读检索记忆",
    "feel": "按关键词检索旧感受",
    "read_bucket": "按 bucket_id 精确读取完整记忆桶",
    "hold": "写一条长期记忆",
    "darkroom_delete": "保留本地私密备份",
    "grow": "只有多个已筛选长期记忆点才用 grow",
    "profile_fact": "强制关联证据桶",
    "trace": "修改已有记忆，不创建新桶",
    "introspection": "读取最近普通记忆供自省",
    "entity_edge_backfill": "默认 dry-run",
    "dream": "无参数进入当前 introspection",
}


def test_manifest_matches_current_production_contract():
    assert tuple(spec.name for spec in current.TOOL_MANIFEST) == tuple(EXPECTED_PARAMETERS)
    assert len({spec.name for spec in current.TOOL_MANIFEST}) == len(current.TOOL_MANIFEST)

    for spec in current.TOOL_MANIFEST:
        assert inspect.iscoroutinefunction(spec.handler)
        assert tuple(inspect.signature(spec.handler).parameters) == EXPECTED_PARAMETERS[spec.name]
        assert spec.description == inspect.getdoc(spec.handler)
        assert spec.description

    for name, marker in DESCRIPTION_MARKERS.items():
        assert marker in current.TOOL_BY_NAME[name].description


def test_current_main_and_p0_tool_names_are_explicitly_enumerated():
    assert set(current.CURRENT_TOOL_NAMES) == EXPECTED_CURRENT_TOOL_NAMES
    assert set(current.P0_TOOL_NAMES) == EXPECTED_P0_TOOL_NAMES
    assert EXPECTED_CURRENT_TOOL_NAMES | EXPECTED_P0_TOOL_NAMES <= set(current.TOOL_BY_NAME)
    assert "i" not in current.TOOL_BY_NAME


def test_registration_helper_uses_declarative_names_and_descriptions():
    registrations = []

    class FakeMCP:
        def tool(self, *, name: str, description: str):
            def register(handler):
                registrations.append((name, description, handler))
                return handler

            return register

    result = current.register_current_tools(FakeMCP())

    assert tuple(name for name, _, _ in registrations) == tuple(EXPECTED_PARAMETERS)
    assert result == {name: handler for name, _, handler in registrations}


def test_registration_helper_registers_union_on_real_fastmcp():
    probe = FastMCP("current-tools-contract")

    registered = current.register_current_tools(probe)

    assert set(registered) == set(EXPECTED_PARAMETERS)
    assert set(probe._tool_manager._tools) == set(EXPECTED_PARAMETERS)
    for spec in current.TOOL_MANIFEST:
        tool = probe._tool_manager.get_tool(spec.name)
        assert tool is not None
        assert tool.description == spec.description
        expected = () if spec.name == "breath" else EXPECTED_PARAMETERS[spec.name]
        assert tuple(tool.parameters.get("properties", {})) == expected

    breath = probe._tool_manager.get_tool("breath")
    assert breath is not None
    assert set(breath.fn_metadata.arg_model.model_fields) == set(
        EXPECTED_PARAMETERS["breath"]
    )
    assert breath.fn_metadata.arg_model.model_config.get("extra") == "forbid"


@pytest.mark.asyncio
async def test_registration_invoker_preserves_schema_and_wraps_execution():
    probe = FastMCP("current-tools-invoker")
    calls = []

    async def invoker(spec, args, kwargs):
        calls.append((spec.name, args, kwargs))
        return {"wrapped": spec.name}

    registered = current.register_current_tools(probe, invoker=invoker)
    result = await registered["reminder_list"](status="active", limit=4)

    assert result == {"wrapped": "reminder_list"}
    assert calls == [("reminder_list", (), {"status": "active", "limit": 4})]
    tool = probe._tool_manager.get_tool("reminder_list")
    assert tool is not None
    assert tuple(tool.parameters.get("properties", {})) == EXPECTED_PARAMETERS[
        "reminder_list"
    ]


def test_runtime_declares_all_current_manager_slots():
    expected = {
        "reminder_store",
        "letter_service",
        "darkroom_store",
        "memory_edge_store",
        "memory_moment_store",
        "entity_edge_store",
        "memory_write_gate",
    }
    assert expected <= set(vars(runtime))

    sentinel = object()
    previous = runtime.reminder_store
    runtime.init(reminder_store=sentinel)
    assert runtime.reminder_store is sentinel
    runtime.init(reminder_store=previous)
