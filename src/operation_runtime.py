"""Shared optional-operation dispatch used by production boundary adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ombrebrain.app.execution import ExecutionEnvelope


def run_optional_operation(
    runtime: Any,
    operation: str,
    payload: dict[str, Any] | None,
    handler: Callable[[], Any],
    *,
    module: str,
    permissions: tuple[str, ...] = (),
    required_permissions: tuple[str, ...] = (),
    actor_name: str,
    source: str,
    capability: str = "",
    writes_memory: bool = False,
    protected_paths: tuple[str, ...] = (),
    feature_flags: tuple[str, ...] = (),
) -> Any:
    """Run through an optional v3 side channel or execute the legacy handler."""

    runner = getattr(runtime, "run_operation", None)
    if not callable(runner):
        return handler()
    envelope = ExecutionEnvelope(
        module=module,
        operation=operation,
        payload=payload or {},
        actor_name=actor_name,
        source=source,
        permissions=permissions,
        required_permissions=required_permissions,
        capability=capability,
        writes_memory=writes_memory,
        protected_paths=protected_paths,
        feature_flags=feature_flags,
    )
    return runner(envelope, handler)
