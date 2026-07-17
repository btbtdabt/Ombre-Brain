"""Discovery aliases, current dashboard assets, and compatibility hooks."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from utils import bucket_text_for_embedding

from .current_contract import CurrentWebDependencies, bool_value, maybe_await
from .oauth import (
    authorization_server_metadata,
    fixed_oauth_client_from_env,
    protected_resource_metadata,
)


def _oauth_enabled(config: Mapping[str, Any]) -> bool:
    return bool_value(config.get("mcp_require_auth"), True) and str(
        config.get("mcp_auth_mode", "oauth")
    ).strip().lower() == "oauth"


def _oauth_not_found() -> Response:
    return Response(status_code=404, headers={"Cache-Control": "no-store"})


def _authorization_metadata(
    config: Mapping[str, Any],
    fixed_client: dict[str, object],
    enabled: bool,
    request: Request,
) -> Response:
    if not enabled:
        return _oauth_not_found()
    return JSONResponse(
        authorization_server_metadata(
            request,
            config,
            fixed_client=fixed_client,
        ),
        headers={"Cache-Control": "no-store"},
    )


def _resource_metadata(
    config: Mapping[str, Any],
    fixed_client: dict[str, object],
    enabled: bool,
    request: Request,
) -> Response:
    if not enabled:
        return _oauth_not_found()
    return JSONResponse(
        protected_resource_metadata(
            request,
            config,
            fixed_client=fixed_client,
        ),
        headers={"Cache-Control": "no-store"},
    )


async def _dispatch_oauth_alias(
    dependencies: CurrentWebDependencies,
    request: Request,
    *,
    target_path: str,
) -> Response:
    dispatcher = dependencies.oauth_alias_dispatch
    if callable(dispatcher):
        result = await maybe_await(
            dispatcher(
                request=request,
                method=request.method,
                target_path=target_path,
            )
        )
        if isinstance(result, Response):
            return result
        return JSONResponse(result)

    query = request.url.query
    location = target_path if not query else f"{target_path}?{query}"
    # 307 is intentional: token requests must retain their POST body.
    return RedirectResponse(location, status_code=307)


def _safe_asset(base: Path, requested_path: str) -> Path | None:
    normalized = str(requested_path or "").strip().replace("\\", "/")
    if not normalized or normalized.startswith("/"):
        return None
    try:
        resolved_base = base.resolve()
        target = (resolved_base / normalized).resolve()
    except (OSError, RuntimeError):
        return None
    if target != resolved_base and resolved_base not in target.parents:
        return None
    return target if target.is_file() else None


async def _introspection_text(dependencies: CurrentWebDependencies) -> str:
    manager = dependencies.bucket_mgr
    if manager is None:
        return ""
    try:
        all_buckets = await manager.list_all(include_archive=False)
        candidates = [
            bucket
            for bucket in all_buckets
            if bucket.get("metadata", {}).get("type") not in {"permanent", "feel"}
            and not bucket.get("metadata", {}).get("pinned", False)
            and not bucket.get("metadata", {}).get("protected", False)
        ]
        candidates.sort(
            key=lambda item: str(item.get("metadata", {}).get("created") or ""),
            reverse=True,
        )
        parts: list[str] = []
        for bucket in candidates[:10]:
            metadata = bucket.get("metadata", {})
            resolved = "[已解决]" if metadata.get("resolved") else "[未解决]"
            try:
                valence = float(metadata.get("valence", 0.5))
                arousal = float(metadata.get("arousal", 0.3))
            except (TypeError, ValueError):
                valence, arousal = 0.5, 0.3
            parts.append(
                f"{metadata.get('name', bucket.get('id', ''))} {resolved} "
                f"V{valence:.1f}/A{arousal:.1f}\n"
                f"{bucket_text_for_embedding(bucket)[:200]}"
            )
        if not parts:
            return ""
        return "[Ombre Brain - Introspection]\n" + "\n---\n".join(parts)
    except Exception:
        logger = dependencies.logger
        warning = getattr(logger, "warning", None)
        if callable(warning):
            warning("Introspection compatibility hook failed", exc_info=True)
        return ""


def register(mcp: Any, dependencies: CurrentWebDependencies) -> None:
    """Register discovery and public compatibility routes."""
    oauth_config = dict(dependencies.config)
    oauth_enabled = _oauth_enabled(oauth_config)
    fixed_client = fixed_oauth_client_from_env()

    @mcp.custom_route("/.well-known/openid-configuration", methods=["GET"])
    @mcp.custom_route("/mcp/.well-known/oauth-authorization-server", methods=["GET"])
    @mcp.custom_route("/mcp/.well-known/openid-configuration", methods=["GET"])
    async def oauth_authorization_alias(request: Request) -> Response:
        return _authorization_metadata(
            oauth_config,
            fixed_client,
            oauth_enabled,
            request,
        )

    @mcp.custom_route("/mcp/.well-known/oauth-protected-resource", methods=["GET"])
    @mcp.custom_route(
        "/mcp/.well-known/oauth-protected-resource/{resource_path:path}",
        methods=["GET"],
    )
    async def oauth_resource_alias(request: Request) -> Response:
        resource_path = str(
            request.path_params.get("resource_path", "") or ""
        ).strip("/")
        if resource_path and resource_path != "mcp":
            return _oauth_not_found()
        return _resource_metadata(
            oauth_config,
            fixed_client,
            oauth_enabled,
            request,
        )

    @mcp.custom_route("/mcp/oauth/authorize", methods=["GET"])
    async def oauth_authorize_alias(request: Request) -> Response:
        return await _dispatch_oauth_alias(
            dependencies,
            request,
            target_path="/oauth/authorize",
        )

    @mcp.custom_route("/mcp/oauth/token", methods=["POST"])
    async def oauth_token_alias(request: Request) -> Response:
        return await _dispatch_oauth_alias(
            dependencies,
            request,
            target_path="/oauth/token",
        )

    @mcp.custom_route("/dashboard-assets/{path:path}", methods=["GET"])
    async def dashboard_asset(request: Request) -> Response:
        target = _safe_asset(
            dependencies.frontend_asset_root(),
            request.path_params.get("path", ""),
        )
        if target is None:
            return PlainTextResponse("dashboard asset not found", status_code=404)
        return FileResponse(target, headers={"Cache-Control": "no-cache"})

    @mcp.custom_route("/introspection-hook", methods=["GET"])
    @mcp.custom_route("/dream-hook", methods=["GET"])
    async def introspection_hook(_request: Request) -> Response:
        return PlainTextResponse(await _introspection_text(dependencies))


__all__ = ["register"]
