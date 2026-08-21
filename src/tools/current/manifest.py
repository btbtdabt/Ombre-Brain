"""Declarative registration contract for the migration tool surface."""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
from types import MappingProxyType
from typing import Any, Awaitable, Callable, cast

from .legacy import (
    I,
    anchor,
    plan,
    relation_attach,
    relation_detach,
    relation_read,
    relation_restore,
    release,
    source_attach,
    source_detach,
    source_read,
    source_restore,
)
from .memory import (
    breath,
    breath_advanced,
    breath_search,
    dream,
    entity_edge_backfill,
    feel,
    grow,
    hold,
    introspection,
    list_buckets_light,
    profile_fact,
    pulse,
    read_bucket,
    trace,
)
from .services import (
    comment_bucket,
    darkroom_delete,
    darkroom_enter,
    darkroom_release,
    darkroom_rooms,
    darkroom_status,
    darkroom_view,
    delete_bucket_comment,
    letter_read,
    letter_lock_update,
    letter_write,
    reminder_create,
    reminder_list,
    reminder_update,
)


ToolHandler = Callable[..., Awaitable[Any]]
ToolInvoker = Callable[["ToolSpec", tuple[Any, ...], dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One public tool registration independent of a FastMCP instance."""

    name: str
    handler: ToolHandler
    description: str
    advertised_schema: dict[str, Any] | None = None
    forbid_extra_args: bool = True


def _tool(
    handler: ToolHandler,
    *,
    advertised_schema: dict[str, Any] | None = None,
    forbid_extra_args: bool = True,
) -> ToolSpec:
    return ToolSpec(
        name=handler.__name__,
        handler=handler,
        description=inspect.getdoc(handler) or "",
        advertised_schema=advertised_schema,
        forbid_extra_args=forbid_extra_args,
    )


# These are the tools decorated with @mcp.tool() on current main.  Keep this
# list separate from compatibility-only and retained-P0 names so parity audits
# can distinguish source contracts without inspecting implementation modules.
CURRENT_TOOL_NAMES = (
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
)

# Retained P0-compatible surface across the integrated upstream history. The
# latest P0 release splits a smaller main/extra view, while this fork keeps the
# established Source, Relation, Letter, and Dream contracts in one canonical
# main manifest. Overlaps use current handlers; distinct names use focused
# adapters or compatibility handlers in this package.
P0_TOOL_NAMES = (
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
)

COMPATIBILITY_TOOL_NAMES = ("darkroom_status", "darkroom_release", "dream")
EXTRA_TOOL_NAMES = ("letter_write", "letter_lock_update", "letter_read")


TOOL_MANIFEST = (
    _tool(reminder_create),
    _tool(reminder_list),
    _tool(reminder_update),
    _tool(
        breath,
        advertised_schema={
            "properties": {},
            "title": "breathArguments",
            "type": "object",
        },
    ),
    *(
        _tool(handler)
        for handler in (
            breath_search,
            breath_advanced,
            feel,
            read_bucket,
            list_buckets_light,
            letter_write,
            letter_lock_update,
            letter_read,
            comment_bucket,
            delete_bucket_comment,
            hold,
            darkroom_enter,
            darkroom_rooms,
            darkroom_delete,
            darkroom_view,
            darkroom_status,
            darkroom_release,
            grow,
            source_read,
            source_attach,
            source_detach,
            source_restore,
            relation_read,
            relation_attach,
            relation_detach,
            relation_restore,
            profile_fact,
            trace,
            pulse,
            introspection,
            entity_edge_backfill,
            dream,
            anchor,
            release,
            plan,
            I,
        )
    ),
)

TOOL_BY_NAME = MappingProxyType({spec.name: spec for spec in TOOL_MANIFEST})
REGISTERED_TOOL_NAMES = tuple(spec.name for spec in TOOL_MANIFEST)


def _validate_manifest() -> None:
    if len(TOOL_BY_NAME) != len(TOOL_MANIFEST):
        raise RuntimeError("duplicate tool name in current tool manifest")
    missing_current = set(CURRENT_TOOL_NAMES) - set(TOOL_BY_NAME)
    missing_p0 = set(P0_TOOL_NAMES) - set(TOOL_BY_NAME)
    if missing_current or missing_p0:
        raise RuntimeError(
            "incomplete current tool manifest: "
            f"current={sorted(missing_current)}, p0={sorted(missing_p0)}"
        )


_validate_manifest()


def _invocation_handler(spec: ToolSpec, invoker: ToolInvoker) -> ToolHandler:
    @wraps(spec.handler)
    async def invoke(*args: Any, **kwargs: Any) -> Any:
        return await invoker(spec, args, kwargs)

    return invoke


def register_current_tools(
    mcp: Any,
    *,
    invoker: ToolInvoker | None = None,
    tool_names: Iterable[str] | None = None,
) -> dict[str, ToolHandler]:
    """Register the canonical union surface on a FastMCP-compatible object.

    ``invoker`` lets the process assembly apply one logging/error envelope to
    every handler while ``functools.wraps`` preserves the declared MCP schema.
    """
    selected_names = (
        REGISTERED_TOOL_NAMES
        if tool_names is None
        else tuple(str(name) for name in tool_names)
    )
    if len(selected_names) != len(set(selected_names)):
        raise RuntimeError("duplicate tool requested from current tool manifest")
    unknown = set(selected_names) - set(TOOL_BY_NAME)
    if unknown:
        raise RuntimeError(f"unknown current tools requested: {sorted(unknown)}")

    registered: dict[str, ToolHandler] = {}
    for name in selected_names:
        spec = TOOL_BY_NAME[name]
        handler = spec.handler if invoker is None else _invocation_handler(spec, invoker)
        registered[spec.name] = mcp.tool(
            name=spec.name,
            description=spec.description,
        )(handler)
        if spec.advertised_schema is None and not spec.forbid_extra_args:
            continue
        manager = getattr(mcp, "_tool_manager", None)
        get_tool = getattr(manager, "get_tool", None)
        if not callable(get_tool):
            continue
        tool = cast(Any, get_tool(spec.name))
        if tool is None:
            raise RuntimeError(f"registered tool is missing: {spec.name}")
        if spec.forbid_extra_args:
            arg_model = tool.fn_metadata.arg_model
            arg_model.model_config["extra"] = "forbid"
            arg_model.model_rebuild(force=True)
            tool.parameters = arg_model.model_json_schema()
        if spec.advertised_schema is not None:
            tool.parameters = deepcopy(spec.advertised_schema)
    return registered


register_tools = register_current_tools


__all__ = [
    "COMPATIBILITY_TOOL_NAMES",
    "CURRENT_TOOL_NAMES",
    "EXTRA_TOOL_NAMES",
    "P0_TOOL_NAMES",
    "REGISTERED_TOOL_NAMES",
    "TOOL_BY_NAME",
    "TOOL_MANIFEST",
    "ToolSpec",
    "ToolInvoker",
    "register_current_tools",
    "register_tools",
]
