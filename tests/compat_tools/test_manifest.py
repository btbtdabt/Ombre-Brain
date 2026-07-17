from __future__ import annotations

import inspect

import pytest
from mcp.server.fastmcp import FastMCP

from tools import _runtime as runtime
from tools import current


EXPECTED_PARAMETERS = {
    "reminder_create": (
        "title", "content", "next_due_at", "start_at", "end_at",
        "repeat_rule", "interval_rounds", "cooldown_minutes", "daily_limit",
        "max_injections", "channel", "session_id",
    ),
    "reminder_list": ("status", "limit"),
    "reminder_update": (
        "reminder_id", "status", "snooze_minutes", "next_due_at", "title",
        "content", "daily_limit", "max_injections",
    ),
    "breath": (
        "query", "max_tokens", "domain", "date", "valence", "arousal",
        "max_results", "importance_min", "tags", "catalog",
        "include_related", "related_per_memory",
        "edge_min_confidence", "include_core", "core_limit", "is_session_start",
        "debug", "surface", "direct_render_mode", "retrieval_mode", "mode",
        "session_id",
    ),
    "breath_search": ("query", "domain", "max_results"),
    "breath_advanced": (
        "query", "max_tokens", "domain", "date", "valence", "arousal",
        "max_results", "importance_min", "tags", "catalog",
        "include_related", "related_per_memory", "edge_min_confidence",
        "include_core", "core_limit", "is_session_start", "debug", "surface",
        "direct_render_mode", "retrieval_mode", "mode", "session_id",
    ),
    "read_bucket": ("bucket_id",),
    "list_buckets_light": ("include_archive", "limit", "offset"),
    "letter_write": ("author", "content", "user_name", "title", "date", "ai_name"),
    "letter_read": ("query", "limit", "author", "date_from", "date_to"),
    "comment_bucket": ("bucket_id", "content", "kind", "valence", "arousal"),
    "delete_bucket_comment": ("bucket_id", "comment_id"),
    "hold": (
        "content", "tags", "importance", "pinned", "feel", "whisper",
        "source_bucket", "valence", "arousal", "title", "date", "domain", "media",
        "why_remembered", "meaning", "test_data",
    ),
    "darkroom_enter": (
        "note", "mode", "mood", "tags", "source", "visibility", "lock_for", "new_room",
    ),
    "darkroom_rooms": ("limit", "visibility"),
    "darkroom_delete": ("room_id", "confirm"),
    "darkroom_view": ("entry_id",),
    "darkroom_status": (),
    "darkroom_release": ("entry_id", "reason"),
    "grow": ("content", "items", "auto", "source", "title"),
    "profile_fact": (
        "fact", "evidence_bucket_id", "profile_kind", "subject", "predicate",
        "object_value", "evidence_moment_id", "evidence_context", "reflection", "confidence",
    ),
    "trace": (
        "bucket_id", "name", "domain", "valence", "arousal", "importance", "tags",
        "resolved", "pinned", "anchor", "digested", "content", "date",
        "status", "weight", "dont_surface", "why_remembered", "meaning_append",
        "meaning_replace", "media_append", "media_replace", "delete",
        "hard_delete", "delete_reason",
    ),
    "pulse": ("include_archive",),
    "introspection": ("limit", "offset", "created_date", "created_from", "created_to"),
    "entity_edge_backfill": ("limit", "bucket_id", "query", "dry_run", "include_archive"),
    "dream": ("window_hours",),
    "anchor": ("bucket_id",),
    "release": ("bucket_id",),
    "plan": ("content", "status", "related_bucket", "weight", "why_remembered"),
    "I": ("content", "aspect", "read", "limit"),
}

EXPECTED_CURRENT_TOOL_NAMES = {
    "entity_edge_backfill",
    "reminder_create",
    "reminder_list",
    "reminder_update",
    "breath",
    "breath_search",
    "breath_advanced",
    "read_bucket",
    "list_buckets_light",
    "letter_write",
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
    "hold",
    "grow",
    "trace",
    "anchor",
    "release",
    "pulse",
    "plan",
    "letter_write",
    "letter_read",
    "I",
    "dream",
}


DESCRIPTION_MARKERS = {
    "reminder_create": "创建独立照顾备忘",
    "breath": "只读检索记忆",
    "read_bucket": "按 bucket_id 精确读取完整记忆桶",
    "hold": "写一条长期记忆",
    "darkroom_delete": "保留本地私密备份",
    "grow": "把筛过的长片段拆成少量长期记忆",
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
