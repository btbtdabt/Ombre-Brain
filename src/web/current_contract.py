"""Shared contracts for the current-production HTTP compatibility surface."""

from __future__ import annotations

import hmac
import inspect
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from entity_edges import extract_entity_edges_from_bucket
from identity import identity_names
from runtime_values import (
    bool_value as bool_value,
    float_between as float_between,
    int_between as int_between,
    valid_memory_id as valid_memory_id,
)
from self_anchor import is_self_anchor_bucket

from . import _shared as sh


RouteKey = tuple[str, str]
RouteHandler = Callable[[Request], Awaitable[Response]]
ServiceHandler = Callable[..., Any]
AuthGuard = Callable[[Request], Response | None | Awaitable[Response | None]]

@dataclass(frozen=True, slots=True)
class RouteSpec:
    """One method/path pair owned by the compatibility package."""

    method: str
    path: str
    family: str

    @property
    def key(self) -> RouteKey:
        return (self.method, self.path)


def _route(method: str, path: str, family: str) -> RouteSpec:
    return RouteSpec(method.upper(), path, family)


CURRENT_ROUTE_SPECS = (
    _route("GET", "/.well-known/openid-configuration", "discovery"),
    _route("GET", "/mcp/.well-known/oauth-authorization-server", "discovery"),
    _route("GET", "/mcp/.well-known/openid-configuration", "discovery"),
    _route("GET", "/mcp/.well-known/oauth-protected-resource", "discovery"),
    _route(
        "GET",
        "/mcp/.well-known/oauth-protected-resource/{resource_path:path}",
        "discovery",
    ),
    _route("GET", "/mcp/oauth/authorize", "discovery"),
    _route("POST", "/mcp/oauth/token", "discovery"),
    _route("GET", "/dashboard-assets/{path:path}", "discovery"),
    _route("GET", "/dream-hook", "discovery"),
    _route("GET", "/introspection-hook", "discovery"),
    _route("POST", "/api/bucket/{bucket_id}/comments", "memory"),
    _route("DELETE", "/api/bucket/{bucket_id}/comments/{comment_id}", "memory"),
    _route("GET", "/api/buckets/light", "memory"),
    _route("POST", "/api/memories", "memory"),
    _route("PATCH", "/api/bucket/{bucket_id}", "memory"),
    _route("POST", "/api/buckets/delete", "memory"),
    _route("POST", "/api/buckets/bulk-update", "memory"),
    _route("GET", "/api/moments", "memory"),
    _route("GET", "/api/todos", "memory"),
    _route("PATCH", "/api/todos/{todo_id}", "memory"),
    _route("POST", "/api/todos/{todo_id}/writeback", "memory"),
    _route("GET", "/api/reminders", "memory"),
    _route("POST", "/api/reminders", "memory"),
    _route("PATCH", "/api/reminders/{reminder_id}", "memory"),
    _route("GET", "/api/darkroom/status", "memory"),
    _route("POST", "/api/ingest-raw", "memory"),
    _route("GET", "/api/search-raw", "memory"),
    _route("POST", "/api/search-raw", "memory"),
    _route("GET", "/api/edges", "memory"),
    _route("GET", "/api/domain-taxonomy", "profile"),
    _route("GET", "/api/portrait-state", "profile"),
    _route("POST", "/api/portrait-maintain", "profile"),
    _route("DELETE", "/api/portrait-state/items", "profile"),
    _route("POST", "/api/portrait-state/items", "profile"),
    _route("PUT", "/api/portrait-state/items", "profile"),
    _route("PUT", "/api/portrait-state/stable", "profile"),
    _route("POST", "/api/portrait-state/stable/lock", "profile"),
    _route("POST", "/api/portrait-state/stable/rollback", "profile"),
    _route("POST", "/api/portrait-state/reset", "profile"),
    _route("GET", "/api/profile-facts", "profile"),
    _route("PATCH", "/api/profile-facts/{bucket_id}", "profile"),
    _route("DELETE", "/api/profile-facts/{bucket_id}", "profile"),
    _route("POST", "/api/profile-fact-proposals", "profile"),
    _route("POST", "/api/profile-fact-proposals/confirm", "profile"),
    _route("POST", "/api/anchor-proposals", "profile"),
    _route("POST", "/api/anchor-proposals/confirm", "profile"),
    _route("GET", "/api/word-map", "profile"),
    _route("POST", "/api/word-map/rebuild", "profile"),
    _route("GET", "/api/word-map/cards", "profile"),
    _route("GET", "/api/identity-semantics", "profile"),
    _route("POST", "/api/identity-semantics/rebuild", "profile"),
    _route("GET", "/api/persona", "profile"),
    _route("GET", "/api/dreams", "profile"),
    _route("GET", "/api/dreams/{dream_id}", "profile"),
    _route("GET", "/api/diffusion-debug", "operations"),
    _route("GET", "/api/recall-debug", "operations"),
    _route("GET", "/api/gateway-injections", "operations"),
    _route("POST", "/api/reflection/run", "operations"),
    _route("POST", "/api/daily-chat-memory/run", "operations"),
    _route("POST", "/api/daily-activity-summary/run", "operations"),
    _route("GET", "/api/daily-chat-memory/pending", "operations"),
    _route("POST", "/api/daily-chat-memory/confirm", "operations"),
    _route("GET", "/api/config/effective", "operations"),
    _route("POST", "/api/backup/export/prepare", "operations"),
    _route("GET", "/api/backup/export/status", "operations"),
    _route("GET", "/api/backup/export", "operations"),
    _route("POST", "/api/backup/restore", "operations"),
)

CURRENT_ROUTE_KEYS = {spec.key for spec in CURRENT_ROUTE_SPECS}
if len(CURRENT_ROUTE_KEYS) != len(CURRENT_ROUTE_SPECS):
    raise RuntimeError("duplicate current-production compatibility route")

CURRENT_REQUIRED_SERVICES = frozenset(
    {
        "anchor_confirm",
        "anchor_proposals",
        "inspect_diffusion",
        "inspect_recall",
        "profile_fact_confirm",
        "profile_fact_proposals",
    }
)


P0_ROUTE_CONFLICTS = MappingProxyType(
    {
        ("GET", "/"): "P0 serves its dashboard at the root.",
        ("GET", "/auth/status"): "P0 owns dashboard session status.",
        ("POST", "/auth/setup"): "P0 owns credential setup.",
        ("POST", "/auth/login"): "P0 owns session creation.",
        ("POST", "/auth/logout"): "P0 owns session revocation.",
        ("GET", "/health"): "P0 owns the public liveness probe.",
        ("GET", "/breath-hook"): "P0 owns the primary memory hook.",
        ("GET", "/api/buckets"): "P0's bucket list remains canonical.",
        ("GET", "/api/bucket/{bucket_id}"): "P0 owns bucket reads.",
        ("DELETE", "/api/bucket/{bucket_id}"): "P0 owns single-bucket deletion.",
        ("GET", "/api/search"): "P0 owns dashboard search.",
        ("GET", "/api/network"): "P0 owns the dashboard network graph.",
        ("GET", "/api/breath-debug"): "P0 owns base breath diagnostics.",
        ("GET", "/dashboard"): "P0 owns the legacy dashboard page.",
        ("GET", "/dashboard-assets/{name}"): (
            "P0 handles one-segment assets; the compatibility path route must "
            "be registered after it to catch nested paths only."
        ),
        ("GET", "/api/config"): "P0 owns safe configuration reads.",
        ("POST", "/api/config"): "P0 owns configuration mutations.",
        ("GET", "/api/status"): "P0 owns runtime status.",
        ("POST", "/api/import/upload"): "P0 owns import upload.",
        ("GET", "/api/import/status"): "P0 owns import status.",
        ("POST", "/api/import/pause"): "P0 owns import pause.",
        ("GET", "/api/import/patterns"): "P0 owns import pattern review.",
        ("GET", "/api/import/results"): "P0 owns import results.",
        ("POST", "/api/import/review"): "P0 owns import review mutations.",
        ("GET", "/.well-known/oauth-authorization-server"): (
            "P0 OAuth discovery is canonical; only current aliases are added."
        ),
        ("GET", "/.well-known/oauth-protected-resource"): (
            "P0 protected-resource discovery is canonical."
        ),
        ("GET", "/oauth/authorize"): "P0 owns OAuth authorization.",
        ("POST", "/oauth/authorize"): "P0 owns OAuth authorization consent.",
        ("POST", "/oauth/token"): "P0 owns OAuth token exchange.",
    }
)


@dataclass(slots=True)
class CurrentWebServices:
    """High-level legacy operations that do not belong to a standalone store."""

    create_memory: ServiceHandler | None = None
    inspect_moments: ServiceHandler | None = None
    inspect_diffusion: ServiceHandler | None = None
    inspect_recall: ServiceHandler | None = None
    fetch_gateway_injections: ServiceHandler | None = None
    portrait_state: ServiceHandler | None = None
    profile_facts: ServiceHandler | None = None
    profile_fact_update: ServiceHandler | None = None
    profile_fact_delete: ServiceHandler | None = None
    profile_fact_proposals: ServiceHandler | None = None
    profile_fact_confirm: ServiceHandler | None = None
    anchor_proposals: ServiceHandler | None = None
    anchor_confirm: ServiceHandler | None = None
    effective_config: ServiceHandler | None = None
    refresh_restore_indexes: ServiceHandler | None = None


@dataclass(slots=True)
class CurrentWebDependencies:
    """Explicit runtime objects consumed by current-production HTTP adapters."""

    config: Mapping[str, Any]
    auth_guard: AuthGuard | None = None
    asset_root: str | os.PathLike[str] | None = None
    bucket_mgr: Any = None
    decay_engine: Any = None
    embedding_engine: Any = None
    embedding_outbox: Any = None
    backup_manager: Any = None
    source_store: Any = None
    darkroom_store: Any = None
    dream_engine: Any = None
    memory_edge_store: Any = None
    memory_moment_store: Any = None
    memory_node_store: Any = None
    entity_edge_store: Any = None
    identity_semantic_store: Any = None
    word_map_store: Any = None
    raw_event_store: Any = None
    reminder_store: Any = None
    letter_service: Any = None
    reflection_engine: Any = None
    persona_engine: Any = None
    portrait_engine: Any = None
    gateway_state_store: Any = None
    queue_embedding_refresh: ServiceHandler | None = None
    refresh_bucket_indexes: ServiceHandler | None = None
    oauth_alias_dispatch: ServiceHandler | None = None
    logger: Any = None
    services: CurrentWebServices = field(default_factory=CurrentWebServices)

    def frontend_asset_root(self) -> Path:
        if self.asset_root is not None:
            return Path(self.asset_root)
        if sh.repo_root:
            return Path(sh.repo_root) / "frontend" / "dashboard-assets"
        return Path(__file__).resolve().parents[2] / "frontend" / "dashboard-assets"


@dataclass(frozen=True, slots=True)
class RegistrationReport:
    registered: frozenset[RouteKey]
    preserved_conflicts: Mapping[RouteKey, str]
    required_services: frozenset[str]
    missing_required_services: frozenset[str]


class MissingDependency(RuntimeError):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def dashboard_auth(
    dependencies: CurrentWebDependencies,
    request: Request,
) -> Response | None:
    guard = dependencies.auth_guard or sh._require_auth
    return await maybe_await(guard(request))


def require_dependency(dependencies: CurrentWebDependencies, name: str) -> Any:
    value = getattr(dependencies, name, None)
    if value is None:
        raise MissingDependency(name)
    return value


def require_service(dependencies: CurrentWebDependencies, name: str) -> ServiceHandler:
    value = getattr(dependencies.services, name, None)
    if not callable(value):
        raise MissingDependency(f"services.{name}")
    return value


def dependency_error(name: str) -> JSONResponse:
    return JSONResponse(
        {"error": f"current web dependency unavailable: {name}"},
        status_code=503,
    )


def exception_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, MissingDependency):
        return dependency_error(exc.name)
    return JSONResponse({"error": str(exc)}, status_code=500)


async def read_json_object(
    request: Request,
    *,
    allow_empty: bool = False,
) -> dict[str, Any]:
    if allow_empty and not (await request.body()).strip():
        return {}
    try:
        body = await request.json()
    except Exception:
        raise ValueError("invalid json body") from None
    if body is None and allow_empty:
        return {}
    if not isinstance(body, dict):
        raise TypeError("json body must be an object")
    return body


def json_body_error(exc: Exception) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=400)


def memory_write_token(dependencies: CurrentWebDependencies) -> str:
    gateway = dependencies.config.get("gateway", {})
    if not isinstance(gateway, Mapping):
        gateway = {}
    return str(
        os.environ.get("OMBRE_MEMORY_WRITE_TOKEN")
        or os.environ.get("OMBRE_GATEWAY_TOKEN")
        or gateway.get("token")
        or ""
    )


def authorized_memory_write(
    dependencies: CurrentWebDependencies,
    request: Request,
) -> bool:
    token = memory_write_token(dependencies)
    if not token:
        return False
    candidates: list[str] = []
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        candidates.append(authorization.split(" ", 1)[1].strip())
    for header_name in ("x-ombre-token", "x-api-key"):
        candidate = request.headers.get(header_name)
        if candidate:
            candidates.append(candidate.strip())
    return any(hmac.compare_digest(candidate, token) for candidate in candidates)


async def raw_api_auth(
    dependencies: CurrentWebDependencies,
    request: Request,
) -> Response | None:
    if authorized_memory_write(dependencies, request):
        return None
    return await dashboard_auth(dependencies, request)


async def queue_embedding(
    dependencies: CurrentWebDependencies,
    bucket_id: str,
) -> bool:
    callback = dependencies.queue_embedding_refresh
    if callable(callback):
        return bool(await maybe_await(callback(bucket_id)))

    outbox = dependencies.embedding_outbox
    manager = dependencies.bucket_mgr
    if outbox is None or manager is None:
        return False
    bucket = await manager.get(bucket_id)
    if not bucket:
        return False
    try:
        from utils import bucket_text_for_embedding

        queued = outbox.enqueue(bucket_id, bucket_text_for_embedding(bucket))
        outbox.ensure_started()
        return bool(queued)
    except Exception:
        return False


async def refresh_bucket_indexes(
    dependencies: CurrentWebDependencies,
    bucket: dict[str, Any] | None,
) -> None:
    if not bucket:
        return
    callback = dependencies.refresh_bucket_indexes
    if callable(callback):
        await maybe_await(callback(bucket))
        return

    operations: list[tuple[str, Any]] = []
    for dependency_name in ("memory_moment_store", "memory_node_store"):
        target = getattr(dependencies, dependency_name, None)
        operation = getattr(target, "upsert_bucket", None)
        if callable(operation):
            operations.append((dependency_name, operation))
    entity_store = dependencies.entity_edge_store
    replace_edges = getattr(entity_store, "replace_bucket_edges", None)
    if callable(replace_edges) and not is_self_anchor_bucket(bucket):
        operations.append(
            (
                "entity_edge_store",
                lambda value: replace_edges(
                    str(value.get("id") or ""),
                    extract_entity_edges_from_bucket(
                        value,
                        identity_names(dict(dependencies.config)),
                    ),
                ),
            )
        )

    logger = dependencies.logger or sh.logger
    for name, operation in operations:
        try:
            await maybe_await(operation(bucket))
        except Exception as exc:
            logger.warning("current web index refresh failed for %s: %s", name, exc)


async def cleanup_bucket_indexes(
    dependencies: CurrentWebDependencies,
    bucket_id: str,
) -> None:
    for dependency_name, method_name in (
        ("memory_moment_store", "delete_bucket"),
        ("memory_edge_store", "delete_for_bucket"),
        ("entity_edge_store", "delete_for_bucket"),
        ("memory_node_store", "delete"),
    ):
        target = getattr(dependencies, dependency_name, None)
        cleanup = getattr(target, method_name, None)
        if callable(cleanup):
            await maybe_await(cleanup(bucket_id))


def public_reminder(item: Mapping[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {}
    fields = (
        "id",
        "title",
        "content",
        "status",
        "source",
        "channel",
        "session_id",
        "start_at",
        "end_at",
        "next_due_at",
        "repeat_rule",
        "interval_rounds",
        "cooldown_minutes",
        "daily_limit",
        "daily_reminder_date",
        "daily_reminder_count",
        "max_injections",
        "last_reminded_at",
        "last_reminded_round",
        "reminder_count",
        "created_at",
        "updated_at",
        "resolved_at",
    )
    return {name: item.get(name) for name in fields}


def service_json(value: Any, *, status_code: int = 200) -> Response:
    if isinstance(value, Response):
        return value
    return JSONResponse(value, status_code=status_code)


__all__ = [
    "CURRENT_REQUIRED_SERVICES",
    "CURRENT_ROUTE_KEYS",
    "CURRENT_ROUTE_SPECS",
    "P0_ROUTE_CONFLICTS",
    "CurrentWebDependencies",
    "CurrentWebServices",
    "MissingDependency",
    "RegistrationReport",
    "RouteKey",
    "authorized_memory_write",
    "bool_value",
    "cleanup_bucket_indexes",
    "dashboard_auth",
    "dependency_error",
    "exception_response",
    "float_between",
    "int_between",
    "json_body_error",
    "maybe_await",
    "memory_write_token",
    "public_reminder",
    "queue_embedding",
    "raw_api_auth",
    "read_json_object",
    "refresh_bucket_indexes",
    "require_dependency",
    "require_service",
    "service_json",
    "valid_memory_id",
]
