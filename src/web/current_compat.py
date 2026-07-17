"""Registration entry point for the current-production web compatibility layer.

Call :func:`register_current_routes` after ``web.register_all(mcp)``.  That
ordering preserves every P0 route and lets the path-aware dashboard asset route
act only as a fallback for nested asset paths.
"""

from __future__ import annotations

from typing import Any

from . import current_discovery, current_memory, current_operations, current_profile
from .current_contract import (
    CURRENT_REQUIRED_SERVICES,
    CURRENT_ROUTE_KEYS,
    CURRENT_ROUTE_SPECS,
    P0_ROUTE_CONFLICTS,
    CurrentWebDependencies,
    CurrentWebServices,
    RegistrationReport,
)


_CURRENT_MODULES = (
    current_discovery.register,
    current_memory.register,
    current_profile.register,
    current_operations.register,
)


def register_current_routes(
    mcp: Any,
    dependencies: CurrentWebDependencies,
) -> RegistrationReport:
    """Register current-only routes against explicitly supplied runtime objects."""
    for register_module in _CURRENT_MODULES:
        register_module(mcp, dependencies)
    missing_services = frozenset(
        name
        for name in CURRENT_REQUIRED_SERVICES
        if not callable(getattr(dependencies.services, name, None))
    )
    return RegistrationReport(
        registered=frozenset(CURRENT_ROUTE_KEYS),
        preserved_conflicts=P0_ROUTE_CONFLICTS,
        required_services=CURRENT_REQUIRED_SERVICES,
        missing_required_services=missing_services,
    )


register = register_current_routes


__all__ = [
    "CURRENT_REQUIRED_SERVICES",
    "CURRENT_ROUTE_KEYS",
    "CURRENT_ROUTE_SPECS",
    "P0_ROUTE_CONFLICTS",
    "CurrentWebDependencies",
    "CurrentWebServices",
    "RegistrationReport",
    "register",
    "register_current_routes",
]
