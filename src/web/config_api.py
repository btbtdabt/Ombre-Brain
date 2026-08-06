"""
========================================
web/config_api.py — Dashboard 配置 / 环境变量 / API Key 测试 / 模型列表
========================================

- /dashboard：重定向到根
- /api/env-vars：环境变量只读概览
- /api/config (GET/POST)：运行期配置读取 / 热更新（含 embedding 热替换）
- /api/test/dehydration、/api/test/embedding：压缩 / 向量化连通性自检
- /api/models：列目标 provider 可用模型
- /api/env-config (GET/POST)：四块 env（compress/embed/webhook/password）热更新；
  embedding 改动会原子替换所有 Web/MCP/写入/迁移运行时引用。
  webhook 不再回写模块全局——_fire_webhook 每次读 os.environ。

对外暴露：register(mcp)。
========================================
"""

import asyncio
import ipaddress
import math
import os
import re
import secrets
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from concurrent.futures import Future, InvalidStateError
from contextlib import asynccontextmanager
from copy import deepcopy
from functools import wraps
from types import SimpleNamespace
from typing import Any, ParamSpec, TypeVar, cast
from urllib.parse import urlsplit

import httpx

from starlette.requests import Request
from starlette.responses import Response

from config_modes import (
    normalize_direct_render_mode,
    normalize_retrieval_mode,
    normalize_thinking_mode,
)
from ombrebrain.security.deployment_profile import (
    assess_mcp_network_safety,
    current_mcp_network_security,
    mcp_network_safety_issue,
    normalize_public_https_origin,
)
from ombrebrain.security.public_origin import configured_public_origin

from . import _shared as sh

try:
    from utils import (  # type: ignore
        BOOT_ENV_CONFIG,
        get_ai_name as _get_ai_name,
        get_owner_name as _get_owner_name,
        get_owner_count as _get_owner_count,
        positive_float as _positive_float,
        parse_bool as _parse_bool,
        atomic_update_config_yaml,
        read_config_yaml,
    )
except ImportError:  # pragma: no cover
    from ..utils import (  # type: ignore
        BOOT_ENV_CONFIG,
        get_ai_name as _get_ai_name,
        get_owner_name as _get_owner_name,
        get_owner_count as _get_owner_count,
        positive_float as _positive_float,
        parse_bool as _parse_bool,
        atomic_update_config_yaml,
        read_config_yaml,
    )

logger = sh.logger
_MAX_PROVIDER_KEY_CHARS = 8192
_MAX_PROVIDER_URL_CHARS = 2048
_MAX_PROVIDER_FORMAT_CHARS = 64
_MAX_ENV_VALUE_CHARS = 8192
_MAX_CONFIG_TEXT_CHARS = 2048
_MAX_MODEL_TEXT_CHARS = 512
_MAX_UPSTREAMS = 32
_MAX_UPSTREAM_MODELS = 256
_MAX_UPSTREAM_KEY_SLOTS = 32
_MAX_GATEWAY_ADMIN_RESPONSE_BYTES = 64 * 1024
_ConfigArgs = ParamSpec("_ConfigArgs")
_ConfigResult = TypeVar("_ConfigResult")


class _ConfigUpdateCoordinator:
    """Serialize config transactions across FastMCP loops and threads.

    Each caller owns a process-wide ``concurrent.futures.Future`` turn.  Unlike
    ``asyncio.Lock``, these futures can safely bridge the independent event
    loops FastMCP may create for different request threads.  A cancelled waiter
    completes its turn after its predecessor, so cancellation cannot strand
    later config writes behind an abandoned queue entry.
    """

    def __init__(self) -> None:
        self._tail: Future[None] | None = None
        self._tail_guard = threading.Lock()

    def _complete(self, turn: Future[None]) -> None:
        # Future callbacks can synchronously advance a chain of cancelled
        # waiters.  Complete the turn outside the non-reentrant guard so that
        # those callbacks can safely inspect/update the tail.
        with self._tail_guard:
            if self._tail is turn:
                self._tail = None
        if turn.done():
            return
        try:
            turn.set_result(None)
        except InvalidStateError:
            # Another cancellation/completion path won after ``done``.
            pass

    @asynccontextmanager
    async def turn(self) -> AsyncIterator[None]:
        current: Future[None] = Future()
        with self._tail_guard:
            previous = self._tail
            self._tail = current

        acquired = previous is None
        try:
            if previous is not None:
                # Shield the shared predecessor: cancelling this request must
                # not cancel the barrier every later request is waiting on.
                await asyncio.shield(asyncio.wrap_future(previous))
                acquired = True
            yield
        finally:
            if acquired:
                self._complete(current)
            elif previous is not None:
                # Cancellation before admission still leaves a turn in the
                # queue.  Advance it as soon as its predecessor releases.
                previous.add_done_callback(
                    lambda _completed: self._complete(current)
                )


_CONFIG_UPDATE_COORDINATOR = _ConfigUpdateCoordinator()


def _serialize_config_updates(
    handler: Callable[_ConfigArgs, Awaitable[_ConfigResult]],
) -> Callable[_ConfigArgs, Coroutine[Any, Any, _ConfigResult]]:
    """Serialize each process's full config read/validate/commit transaction."""

    @wraps(handler)
    async def wrapped(
        *args: _ConfigArgs.args,
        **kwargs: _ConfigArgs.kwargs,
    ) -> _ConfigResult:
        async with _CONFIG_UPDATE_COORDINATOR.turn():
            return await handler(*args, **kwargs)

    return wrapped


_CURRENT_SECTION_FIELDS: dict[str, frozenset[str]] = {
    "reranker": frozenset(
        {
            "enabled",
            "model",
            "base_url",
            "api_key",
            "timeout_seconds",
            "candidate_limit",
            "score_weight",
        }
    ),
    "persona": frozenset(
        {
            "enabled",
            "event_recording_enabled",
            "conflict_nudge_enabled",
            "json_response_format",
            "model",
            "base_url",
            "api_key",
        }
    ),
    "dream": frozenset(
        {
            "enabled",
            "auto_enabled",
            "surface_enabled",
            "inject_enabled",
            "retain_after_inject",
            "model",
            "base_url",
            "api_key",
            "daily_hour",
            "daily_probability",
            "min_material_count",
            "material_window_hours",
            "identity_anchor_id",
        }
    ),
    "reflection": frozenset(
        {
            "enabled",
            "auto_enabled",
            "daily_enabled",
            "daily_min_memory_items",
            "daily_conversation_turn_limit",
            "daily_chat_memory_mode",
            "daily_chat_memory_turn_limit",
            "memory_affect_anchor_enabled",
            "relationship_weather_affect_anchor_enabled",
            "model",
            "thinking_mode",
            "base_url",
            "api_key",
        }
    ),
    "portrait": frozenset(
        {
            "enabled",
            "auto_enabled",
            "auto_initial_enabled",
            "daily_enabled",
            "material_limit",
            "first_run_material_limit",
            "user_rewrite_evidence_delta",
            "manual_suppress_days",
        }
    ),
    "self_anchor": frozenset({"entry_bucket_id"}),
    "recall": frozenset({"query_resurface_enabled"}),
    "memory_diffusion": frozenset(
        {
            "enabled",
            "top_k",
            "min_activation",
            "chain_walk_enabled",
            "chain_max_hops",
            "chain_min_confidence",
            "chain_max_frontier",
        }
    ),
    "gateway": frozenset(
        {
            "upstreams",
            "cooldown_hours",
            "skip_recent_rounds",
            "recent_context_cooldown_hours",
            "recent_context_reentry_idle_hours",
            "recent_context_budget",
            "recalled_memory_budget",
            "related_memory_budget",
            "memory_detail_recall_enabled",
            "memory_detail_recall_max_ids",
            "memory_detail_recall_budget",
            "current_inner_state_interval_rounds",
            "direct_render_mode",
            "retrieval_mode",
            "operit_context_rewrite_enabled",
            "word_map_hint_enabled",
            "query_planner_enabled",
            "domain_sentinel_enabled",
            "domain_sentinel_model",
            "domain_sentinel_base_url",
            "domain_sentinel_api_key",
        }
    ),
}

_LEGACY_SECTION_FIELDS: dict[str, frozenset[str]] = {
    "dehydration": frozenset(
        {
            "model",
            "base_url",
            "api_key",
            "max_tokens",
            "temperature",
            "api_format",
            "timeout_seconds",
        }
    ),
    "embedding": frozenset(
        {
            "enabled",
            "model",
            "base_url",
            "api_key",
            "api_format",
            "timeout_seconds",
            "backend",
        }
    ),
    "surfacing": frozenset(
        {
            "breath_max_results",
            "breath_max_tokens",
            "feel_max_tokens",
            "sampling",
        }
    ),
    "deployment": frozenset({"public_url"}),
}

_SURFACING_SAMPLING_FIELDS = frozenset(
    {"enabled", "top_k", "sample_k", "temperature"}
)

_CURRENT_SECRET_ENV_FIELDS = {
    ("dehydration", "api_key"): "OMBRE_COMPRESS_API_KEY",
    ("embedding", "api_key"): "OMBRE_EMBED_API_KEY",
    ("reranker", "api_key"): "OMBRE_RERANKER_API_KEY",
    ("persona", "api_key"): "OMBRE_PERSONA_API_KEY",
    ("dream", "api_key"): "OMBRE_DREAM_API_KEY",
    ("reflection", "api_key"): "OMBRE_REFLECTION_API_KEY",
    ("gateway", "domain_sentinel_api_key"): "OMBRE_DOMAIN_SENTINEL_API_KEY",
}

_CONFIG_POST_ROOT_FIELDS = frozenset(
    {
        "persist",
        "persist_env",
        "dehydration",
        "embedding",
        "reranker",
        "persona",
        "dream",
        "reflection",
        "portrait",
        "self_anchor",
        "gateway",
        "recall",
        "memory_diffusion",
        "surfacing",
        "merge_threshold",
        "mcp_require_auth",
        "mcp_auth_mode",
        "deployment",
        "host_port",
    }
)

_GATEWAY_OWNED_ROOT_FIELDS = frozenset(
    {
        "dehydration",
        "embedding",
        "gateway",
        "reranker",
        "persona",
        "dream",
        "self_anchor",
        "memory_diffusion",
    }
)

_GATEWAY_ENV_ONLY_FIELDS: dict[str, frozenset[str]] = {
    "dehydration": frozenset({"api_key"}),
    "embedding": frozenset({"api_key"}),
    "reranker": frozenset({"api_key"}),
    "persona": frozenset({"api_key"}),
    "dream": frozenset({"api_key"}),
    "reflection": frozenset({"api_key"}),
    "gateway": frozenset({"domain_sentinel_api_key"}),
}

_GATEWAY_LIVE_APPLY_FIELDS: dict[str, frozenset[str]] = {
    "dehydration": frozenset(
        {"model", "base_url", "api_key", "max_tokens", "temperature"}
    ),
    "gateway": _CURRENT_SECTION_FIELDS["gateway"],
    "reranker": _CURRENT_SECTION_FIELDS["reranker"],
    "persona": _CURRENT_SECTION_FIELDS["persona"],
    "dream": _CURRENT_SECTION_FIELDS["dream"] - {"api_key"},
    "memory_diffusion": _CURRENT_SECTION_FIELDS["memory_diffusion"],
}

_GATEWAY_LIVE_SECRET_FIELDS = frozenset(
    {
        ("dehydration", "api_key"),
        ("reranker", "api_key"),
        ("persona", "api_key"),
        ("gateway", "domain_sentinel_api_key"),
    }
)


def _gateway_persistence_required(body: Mapping[str, Any]) -> bool:
    """Return whether a request contains Gateway config not durable in .env."""
    for section in set(body) & _GATEWAY_OWNED_ROOT_FIELDS:
        payload = body.get(section)
        if not isinstance(payload, Mapping):
            return True
        if set(payload) - _GATEWAY_ENV_ONLY_FIELDS.get(section, frozenset()):
            return True
    return False


def _build_gateway_live_apply_plan(
    body: Mapping[str, Any],
    env_updates: Mapping[str, str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Build the redacted subset the external Gateway can hot-apply.

    The Gateway process cannot observe newly written provider-key environment
    variables until it restarts.  Keep those values in the managed env file;
    only forward their already-public env *names* and report the key rotation
    as pending restart work.
    """
    live_payload: dict[str, dict[str, Any]] = {}
    restart_fields: set[str] = set()
    removed_upstream_write_only_secret = False

    for section in sorted(set(body) & _GATEWAY_OWNED_ROOT_FIELDS):
        section_payload = body.get(section)
        if not isinstance(section_payload, Mapping):
            restart_fields.add(section)
            continue
        supported_fields = _GATEWAY_LIVE_APPLY_FIELDS.get(section)
        if supported_fields is None:
            restart_fields.update(
                f"{section}.{field}" for field in section_payload
            )
            continue

        live_section: dict[str, Any] = {}
        for field, value in section_payload.items():
            # An empty secret is a documented no-op, not a request to clear it.
            if (
                (section, field) in _GATEWAY_LIVE_SECRET_FIELDS
                and not str(value or "").strip()
            ):
                continue
            if field in supported_fields:
                if section == "gateway" and field == "upstreams" and isinstance(value, list):
                    redacted_upstreams: list[Any] = []
                    removed_write_only_secret = False
                    for upstream in value:
                        if not isinstance(upstream, Mapping):
                            redacted_upstreams.append(deepcopy(upstream))
                            continue
                        redacted = deepcopy(dict(upstream))
                        raw_values = redacted.pop("api_key_values", None)
                        if isinstance(raw_values, str):
                            removed_write_only_secret = (
                                removed_write_only_secret
                                or bool(raw_values.strip())
                            )
                        elif isinstance(raw_values, list):
                            removed_write_only_secret = (
                                removed_write_only_secret
                                or any(str(item or "").strip() for item in raw_values)
                            )
                        redacted_upstreams.append(redacted)
                    live_section[field] = redacted_upstreams
                    if removed_write_only_secret:
                        removed_upstream_write_only_secret = True
                        restart_fields.add("gateway.upstreams.api_key_values")
                else:
                    live_section[field] = deepcopy(value)
            elif not (
                field == "api_key" and not str(value or "").strip()
            ):
                restart_fields.add(f"{section}.{field}")
        if live_section:
            live_payload[section] = live_section

    gateway_payload = body.get("gateway")
    raw_upstreams = (
        gateway_payload.get("upstreams")
        if isinstance(gateway_payload, Mapping)
        else None
    )
    referenced_key_was_updated = False
    if isinstance(raw_upstreams, list) and env_updates:
        referenced_envs: set[str] = set()
        for upstream in raw_upstreams:
            if not isinstance(upstream, Mapping):
                continue
            raw_envs = upstream.get(
                "api_key_envs", upstream.get("api_key_env", [])
            )
            if isinstance(raw_envs, str):
                raw_envs = [
                    part.strip() for part in re.split(r"[\r\n,]+", raw_envs)
                ]
            if isinstance(raw_envs, list):
                referenced_envs.update(
                    str(name or "").strip()
                    for name in raw_envs
                    if str(name or "").strip()
                )
        referenced_key_was_updated = bool(referenced_envs & set(env_updates))
        if referenced_key_was_updated:
            restart_fields.add("gateway.upstreams.api_key_values")

    # A separate Gateway process cannot see a just-written managed-env value.
    # Replacing its upstream table with env refs now would resolve empty/stale
    # keys and could take healthy traffic down.  Leave the active table intact
    # and require an external restart, while still live-applying unrelated
    # Gateway fields from the same durable request.
    if removed_upstream_write_only_secret or referenced_key_was_updated:
        live_gateway = live_payload.get("gateway")
        if live_gateway is not None:
            live_gateway.pop("upstreams", None)
            if not live_gateway:
                live_payload.pop("gateway", None)

    return live_payload, sorted(restart_fields)


def _gateway_admin_endpoint() -> tuple[str, str]:
    raw_url = str(os.environ.get("OMBRE_GATEWAY_ADMIN_URL", "") or "").strip()
    if not raw_url:
        return "", "not_configured"
    if len(raw_url) > _MAX_PROVIDER_URL_CHARS or any(
        ord(char) < 32 for char in raw_url
    ):
        return "", "invalid_admin_url"
    try:
        parsed = urlsplit(raw_url)
        _ = parsed.port
    except ValueError:
        return "", "invalid_admin_url"
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/api/config"
    ):
        return "", "invalid_admin_url"
    if parsed.scheme == "http" and not _is_explicit_local_admin_host(
        parsed.hostname
    ):
        return "", "invalid_admin_url"
    return raw_url, ""


def _is_explicit_local_admin_host(hostname: str) -> bool:
    """Allow plaintext admin traffic only to literal local/private targets.

    Hostnames are never resolved here: accepting a host because DNS currently
    maps it to a private address would expose the bearer token to rebinding.
    """
    host = hostname.strip().lower().rstrip(".")
    if host in {"localhost", "ombre-gateway"} or host.endswith(".localhost"):
        return True
    if not host or "%" in host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    local_networks = (
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("fc00::/7"),
        ipaddress.ip_network("fe80::/10"),
    )
    return any(
        address.version == network.version and address in network
        for network in local_networks
    )


def _gateway_expected_live_updates(
    payload: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    return {
        f"{section}.{field}"
        for section, section_payload in payload.items()
        for field in section_payload
    }


async def _post_gateway_live_config(
    payload: Mapping[str, Mapping[str, Any]],
) -> tuple[bool, str]:
    """Apply persisted config to the separate Gateway without leaking secrets."""
    admin_url, url_error = _gateway_admin_endpoint()
    if url_error:
        return False, url_error
    gateway_token = str(os.environ.get("OMBRE_GATEWAY_TOKEN", "") or "").strip()
    if not gateway_token:
        return False, "missing_gateway_token"
    if any(char in gateway_token for char in ("\r", "\n", "\0")):
        return False, "invalid_gateway_token"

    timeout = httpx.Timeout(5.0, connect=2.0, pool=2.0)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(
                admin_url,
                headers={"Authorization": f"Bearer {gateway_token}"},
                json=payload,
            )
    except httpx.TimeoutException:
        return False, "timeout"
    except Exception:
        return False, "request_failed"

    if not 200 <= int(response.status_code) < 300:
        return False, "gateway_rejected"
    if len(response.content) > _MAX_GATEWAY_ADMIN_RESPONSE_BYTES:
        return False, "invalid_response"
    try:
        result = response.json()
    except Exception:
        return False, "invalid_response"
    if not isinstance(result, Mapping) or result.get("ok") is not True:
        return False, "invalid_response"
    updated = result.get("updated")
    if not isinstance(updated, list) or any(
        not isinstance(item, str) for item in updated
    ):
        return False, "invalid_response"
    if not _gateway_expected_live_updates(payload).issubset(set(updated)):
        return False, "incomplete_response"
    return True, ""


def _mask_secret(value: object) -> str:
    secret = str(value or "")
    if not secret:
        return ""
    if len(secret) <= 8:
        return "***"
    return f"{secret[:4]}...{secret[-4:]}"


def _clean_text(
    value: object,
    field: str,
    *,
    max_chars: int = _MAX_CONFIG_TEXT_CHARS,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = value.strip()
    if len(cleaned) > max_chars:
        raise ValueError(f"{field} exceeds {max_chars} characters")
    if any(ord(char) < 32 for char in cleaned):
        raise ValueError(f"{field} contains control characters")
    return cleaned


def _clean_secret(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = value.strip()
    if len(cleaned) > _MAX_ENV_VALUE_CHARS:
        raise ValueError(f"{field} exceeds {_MAX_ENV_VALUE_CHARS} characters")
    if "\r" in cleaned or "\n" in cleaned or "\0" in cleaned:
        raise ValueError(f"{field} contains unsafe characters")
    return cleaned


def _clean_http_url(value: object, field: str, *, required: bool = False) -> str:
    cleaned = _clean_text(value, field, max_chars=_MAX_PROVIDER_URL_CHARS)
    if not cleaned:
        if required:
            raise ValueError(f"{field} is required")
        return ""
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be an http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{field} must not include credentials")
    return cleaned.rstrip("/")


def _clean_bool(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{field} must be a boolean")


def _clean_int(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, str) and not re.fullmatch(r"[+-]?\d+", value.strip()):
        raise ValueError(f"{field} must be an integer")
    try:
        normalized = int(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return normalized


def _clean_float(
    value: object,
    field: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    try:
        normalized = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{field} must be a finite number")
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return normalized


def _clean_enum(
    value: object,
    field: str,
    allowed: frozenset[str],
) -> str:
    normalized = _clean_text(value, field, max_chars=_MAX_PROVIDER_FORMAT_CHARS)
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{field} must be one of: {choices}")
    return normalized


def _safe_env_name(value: object, field: str) -> str:
    return _provider_api_key_env_name(value, field)


def _env_name(value: object, field: str) -> str:
    """Validate an existing Gateway env reference without authorizing writes."""
    name = _clean_text(value, field, max_chars=160)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"{field} must be an environment variable name")
    return name


_RESERVED_PROVIDER_ENV_SEGMENTS = frozenset(
    {
        "ADMIN",
        "AUTH",
        "CONTROL",
        "COOKIE",
        "DASHBOARD",
        "DATABASE",
        "DB",
        "INFRA",
        "INTERNAL",
        "MCP",
        "OAUTH",
        "PASSWORD",
        "SESSION",
        "SIGNING",
        "TOKEN",
        "WEBHOOK",
    }
)


def _provider_api_key_env_name(value: object, field: str) -> str:
    """Accept only dedicated outbound-provider API key references.

    Gateway upstream URLs are Dashboard-controlled. Allowing arbitrary env
    names here would let a configured upstream forward auth, OAuth, database,
    or infrastructure secrets to that URL. Existing nonconforming references
    are handled separately and only survive an unchanged round trip.
    """
    name = _env_name(value, field)
    if not re.fullmatch(
        r"OMBRE_GATEWAY_[A-Z0-9]+(?:_[A-Z0-9]+)*_API_KEY(?:_[0-9]+)?",
        name,
    ):
        raise ValueError(
            f"{field} must name an OMBRE_GATEWAY_*_API_KEY provider variable"
        )
    stem = re.sub(r"_API_KEY(?:_[0-9]+)?$", "", name).removeprefix(
        "OMBRE_GATEWAY_"
    )
    if set(stem.split("_")) & _RESERVED_PROVIDER_ENV_SEGMENTS:
        raise ValueError(
            f"{field} must name an OMBRE_GATEWAY_*_API_KEY provider variable"
        )
    return name


def _existing_gateway_upstreams(
    config: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    gateway = config.get("gateway")
    if not isinstance(gateway, Mapping):
        return {}
    raw_upstreams = gateway.get("upstreams")
    if not isinstance(raw_upstreams, list):
        return {}
    existing: dict[str, Mapping[str, Any]] = {}
    for item in raw_upstreams:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            existing[name] = item
    return existing


def _upstream_env_names(value: Mapping[str, Any]) -> list[str]:
    raw_envs = value.get("api_key_envs", value.get("api_key_env", []))
    if isinstance(raw_envs, str):
        raw_envs = [part.strip() for part in re.split(r"[\r\n,]+", raw_envs)]
    if not isinstance(raw_envs, list):
        return []
    names: list[str] = []
    for raw_name in raw_envs:
        name = str(raw_name or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _upstream_transport_signature(value: Mapping[str, Any]) -> tuple[str, ...]:
    """Return every upstream field that can redirect a referenced secret."""
    return (
        _normalize_upstream_protocol(
            value.get("protocol") or value.get("api_format") or value.get("type"),
            "gateway.upstreams.protocol",
        ),
        str(value.get("base_url") or "").strip().rstrip("/"),
        str(
            value.get("gemini_base_url")
            or value.get("native_base_url")
            or value.get("gemini_native_base_url")
            or ""
        )
        .strip()
        .rstrip("/"),
        str(value.get("gemini_auth") or "").strip().lower(),
    )


def _normalize_upstream_protocol(value: object, field: str) -> str:
    protocol = _clean_text(
        value or "openai", field, max_chars=_MAX_PROVIDER_FORMAT_CHARS
    ).lower()
    if protocol in {"anthropic", "claude"}:
        return "anthropic"
    if protocol in {
        "openai",
        "openai-compatible",
        "chat_completions",
        "chat-completions",
    }:
        return "openai"
    return "openai"


def _safe_model_entry(value: object, field: str) -> str | dict[str, str]:
    if isinstance(value, str):
        model_id = _clean_text(value, field, max_chars=_MAX_MODEL_TEXT_CHARS)
        if not model_id:
            raise ValueError(f"{field} cannot be empty")
        return model_id
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a string or object")
    allowed = {
        "id",
        "alias",
        "name",
        "model",
        "upstream_model",
        "provider_model",
        "target_model",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{field} contains unknown fields: {', '.join(sorted(unknown))}")
    model_id = _clean_text(
        value.get("id")
        or value.get("alias")
        or value.get("name")
        or value.get("model")
        or value.get("upstream_model")
        or "",
        f"{field}.id",
        max_chars=_MAX_MODEL_TEXT_CHARS,
    )
    upstream_model = _clean_text(
        value.get("upstream_model")
        or value.get("provider_model")
        or value.get("target_model")
        or value.get("model")
        or model_id,
        f"{field}.upstream_model",
        max_chars=_MAX_MODEL_TEXT_CHARS,
    )
    if not model_id:
        raise ValueError(f"{field}.id cannot be empty")
    if upstream_model and upstream_model != model_id:
        return {"id": model_id, "upstream_model": upstream_model}
    return model_id


def _existing_upstream_secrets(config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    preserved: dict[str, dict[str, Any]] = {}
    for name, item in _existing_gateway_upstreams(config).items():
        secret_fields = {
            key: deepcopy(item[key])
            for key in ("api_key", "api_keys")
            if key in item
        }
        if secret_fields:
            preserved[name] = secret_fields
    return preserved


def _normalize_upstreams(
    value: object,
    *,
    current_config: Mapping[str, Any],
    persist_env: bool,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("gateway.upstreams must be a list")
    if len(value) > _MAX_UPSTREAMS:
        raise ValueError(f"gateway.upstreams supports at most {_MAX_UPSTREAMS} providers")
    existing_secrets = _existing_upstream_secrets(current_config)
    existing_upstreams = _existing_gateway_upstreams(current_config)
    seen_names: set[str] = set()
    normalized: list[dict[str, Any]] = []
    env_updates: dict[str, str] = {}
    allowed_fields = {
        "name",
        "protocol",
        "api_format",
        "type",
        "base_url",
        "api_key_envs",
        "api_key_env",
        "api_key_values",
        "default_model",
        "prompt_cache",
        "prompt_cache_retention",
        "anthropic_version",
        "anthropic_beta",
        "gemini_base_url",
        "native_base_url",
        "gemini_native_base_url",
        "gemini_auth",
        "models",
        # Read-only status fields returned by GET /api/config. Accept and drop
        # them so generic round-trip clients do not fail validation.
        "has_direct_api_key",
        "key_count",
        "ready",
    }
    for index, raw in enumerate(value):
        field = f"gateway.upstreams[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{field} must be an object")
        unknown = set(raw) - allowed_fields
        if unknown:
            raise ValueError(f"{field} contains unknown fields: {', '.join(sorted(unknown))}")
        name = _clean_text(raw.get("name"), f"{field}.name", max_chars=128)
        if not name:
            raise ValueError(f"{field}.name is required")
        if name in seen_names:
            raise ValueError(f'duplicate gateway upstream name "{name}"')
        seen_names.add(name)
        protocol = _normalize_upstream_protocol(
            raw.get("protocol") or raw.get("api_format") or raw.get("type"),
            f"{field}.protocol",
        )
        base_url = _clean_http_url(
            raw.get("base_url", ""), f"{field}.base_url"
        )
        gemini_base_url = _clean_http_url(
            raw.get("gemini_base_url")
            or raw.get("native_base_url")
            or raw.get("gemini_native_base_url")
            or "",
            f"{field}.gemini_base_url",
        )
        gemini_auth = _clean_enum(
            raw.get("gemini_auth", ""),
            f"{field}.gemini_auth",
            frozenset(
                {
                    "",
                    "bearer",
                    "google",
                    "x-goog-api-key",
                    "api-key",
                    "api_key",
                    "both",
                }
            ),
        )
        raw_envs = raw.get("api_key_envs", raw.get("api_key_env", []))
        if isinstance(raw_envs, str):
            raw_envs = [part.strip() for part in re.split(r"[\r\n,]+", raw_envs)]
        if not isinstance(raw_envs, list):
            raise ValueError(f"{field}.api_key_envs must be a list")
        if len(raw_envs) > _MAX_UPSTREAM_KEY_SLOTS:
            raise ValueError(
                f"{field} supports at most {_MAX_UPSTREAM_KEY_SLOTS} key slots"
            )
        env_names: list[str] = []
        for env_index, raw_name in enumerate(raw_envs):
            env_name = _env_name(
                raw_name, f"{field}.api_key_envs[{env_index}]"
            )
            if env_name not in env_names:
                env_names.append(env_name)
        unsafe_env_names: list[str] = []
        for env_index, env_name in enumerate(env_names):
            try:
                _provider_api_key_env_name(
                    env_name, f"{field}.api_key_envs[{env_index}]"
                )
            except ValueError:
                unsafe_env_names.append(env_name)
        if unsafe_env_names:
            existing = existing_upstreams.get(name)
            unchanged_legacy_reference = bool(
                existing
                and env_names == _upstream_env_names(existing)
                and _upstream_transport_signature(raw)
                == _upstream_transport_signature(existing)
            )
            if not unchanged_legacy_reference:
                raise ValueError(
                    f"{field}.api_key_envs must contain "
                    "OMBRE_GATEWAY_*_API_KEY provider variables; an existing "
                    "legacy environment reference can only be preserved with "
                    "its provider target unchanged"
                )

        raw_values = raw.get("api_key_values", [])
        if isinstance(raw_values, str):
            raw_values = raw_values.splitlines()
        if not isinstance(raw_values, list):
            raise ValueError(f"{field}.api_key_values must be a list")
        if len(raw_values) > _MAX_UPSTREAM_KEY_SLOTS:
            raise ValueError(
                f"{field} supports at most {_MAX_UPSTREAM_KEY_SLOTS} key slots"
            )
        secret_values: list[str] = []
        for secret_index, secret in enumerate(raw_values):
            if secret is None or (isinstance(secret, str) and not secret.strip()):
                secret_values.append("")
                continue
            secret_values.append(
                _clean_secret(secret, f"{field}.api_key_values[{secret_index}]")
            )
        if any(secret_values) and not persist_env:
            raise ValueError(
                f"{field}.api_key_values requires persist_env=true"
            )
        if any(secret_values[len(env_names):]):
            raise ValueError(f"{field} has more key values than key env names")
        for env_index, (env_name, secret) in enumerate(
            zip(env_names, secret_values, strict=False)
        ):
            if secret:
                writable_name = _safe_env_name(
                    env_name, f"{field}.api_key_envs[{env_index}]"
                )
                if (
                    writable_name in env_updates
                    and env_updates[writable_name] != secret
                ):
                    raise ValueError(
                        f"{field}.api_key_values contains conflicting values "
                        f"for {writable_name}"
                    )
                env_updates[writable_name] = secret

        raw_models = raw.get("models", [])
        if isinstance(raw_models, str):
            raw_models = [part.strip() for part in raw_models.split(",")]
        if not isinstance(raw_models, list):
            raise ValueError(f"{field}.models must be a list")
        if len(raw_models) > _MAX_UPSTREAM_MODELS:
            raise ValueError(
                f"{field}.models supports at most {_MAX_UPSTREAM_MODELS} models"
            )
        models: list[str | dict[str, str]] = []
        seen_models: set[str] = set()
        for model_index, model in enumerate(raw_models):
            if model in (None, ""):
                continue
            normalized_model = _safe_model_entry(
                model, f"{field}.models[{model_index}]"
            )
            public_model = (
                normalized_model["id"]
                if isinstance(normalized_model, dict)
                else normalized_model
            )
            if public_model in seen_models:
                continue
            seen_models.add(public_model)
            models.append(normalized_model)
        default_model = _clean_text(
            raw.get("default_model", ""),
            f"{field}.default_model",
            max_chars=_MAX_MODEL_TEXT_CHARS,
        )
        prompt_cache = _clean_enum(
            raw.get("prompt_cache", ""),
            f"{field}.prompt_cache",
            frozenset({"", "openai", "anthropic", "anthropic_explicit"}),
        )
        retention_allowed = {"": {""}, "openai": {"", "24h"}}
        if prompt_cache in {"anthropic", "anthropic_explicit"}:
            allowed_retention = {"", "1h"}
        else:
            allowed_retention = retention_allowed.get(prompt_cache, {""})
        prompt_cache_retention = _clean_enum(
            raw.get("prompt_cache_retention", ""),
            f"{field}.prompt_cache_retention",
            frozenset(allowed_retention),
        )
        upstream: dict[str, Any] = {
            "name": name,
            "protocol": protocol,
            "base_url": base_url,
            "api_key_envs": env_names,
            "default_model": default_model,
            "prompt_cache": prompt_cache,
            "prompt_cache_retention": prompt_cache_retention,
            "anthropic_version": _clean_text(
                raw.get("anthropic_version", ""),
                f"{field}.anthropic_version",
                max_chars=128,
            ),
            "anthropic_beta": _clean_text(
                raw.get("anthropic_beta", ""),
                f"{field}.anthropic_beta",
                max_chars=512,
            ),
            "gemini_base_url": gemini_base_url,
            "gemini_auth": gemini_auth,
            "models": models,
        }
        preserved_direct_secret = existing_secrets.get(name)
        existing_upstream = existing_upstreams.get(name)
        if (
            preserved_direct_secret
            and existing_upstream
            and _upstream_transport_signature(raw)
            != _upstream_transport_signature(existing_upstream)
        ):
            raise ValueError(
                f'{field} cannot retarget provider "{name}" while it carries '
                "a legacy direct API key; migrate it to api_key_envs first"
            )
        upstream.update(preserved_direct_secret or {})
        normalized.append(upstream)
    missing_direct_secrets = sorted(set(existing_secrets) - seen_names)
    if missing_direct_secrets:
        names = ", ".join(f'"{name}"' for name in missing_direct_secrets)
        raise ValueError(
            "gateway.upstreams cannot rename or remove providers carrying "
            f"legacy direct API keys ({names}); migrate them to api_key_envs first"
        )
    return normalized, env_updates


def _normalize_current_section(
    section: str,
    value: object,
    *,
    current_config: Mapping[str, Any],
    persist_env: bool,
) -> tuple[dict[str, Any], dict[str, str]]:
    if not isinstance(value, dict):
        raise ValueError(f"{section} must be an object")
    allowed = _CURRENT_SECTION_FIELDS[section]
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(
            f"{section} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    result: dict[str, Any] = {}
    env_updates: dict[str, str] = {}

    bool_fields = {
        "reranker": {"enabled"},
        "persona": {
            "enabled",
            "event_recording_enabled",
            "conflict_nudge_enabled",
            "json_response_format",
        },
        "dream": {
            "enabled",
            "auto_enabled",
            "surface_enabled",
            "inject_enabled",
            "retain_after_inject",
        },
        "reflection": {
            "enabled",
            "auto_enabled",
            "daily_enabled",
            "memory_affect_anchor_enabled",
            "relationship_weather_affect_anchor_enabled",
        },
        "portrait": {
            "enabled",
            "auto_enabled",
            "auto_initial_enabled",
            "daily_enabled",
        },
        "recall": {"query_resurface_enabled"},
        "memory_diffusion": {"enabled", "chain_walk_enabled"},
        "gateway": {
            "memory_detail_recall_enabled",
            "operit_context_rewrite_enabled",
            "word_map_hint_enabled",
            "query_planner_enabled",
            "domain_sentinel_enabled",
        },
    }.get(section, set())
    int_ranges: dict[tuple[str, str], tuple[int, int]] = {
        ("reranker", "candidate_limit"): (1, 100),
        ("dream", "daily_hour"): (0, 23),
        ("dream", "min_material_count"): (1, 20),
        ("dream", "material_window_hours"): (1, 168),
        ("reflection", "daily_min_memory_items"): (0, 100),
        ("reflection", "daily_conversation_turn_limit"): (0, 80),
        ("reflection", "daily_chat_memory_turn_limit"): (0, 10000),
        ("portrait", "material_limit"): (1, 100),
        ("portrait", "first_run_material_limit"): (1, 500),
        ("portrait", "user_rewrite_evidence_delta"): (1, 100),
        ("portrait", "manual_suppress_days"): (1, 90),
        ("memory_diffusion", "top_k"): (1, 100),
        ("memory_diffusion", "chain_max_hops"): (1, 20),
        ("memory_diffusion", "chain_max_frontier"): (1, 1000),
        ("gateway", "skip_recent_rounds"): (0, 10000),
        ("gateway", "recent_context_budget"): (0, 50000),
        ("gateway", "recalled_memory_budget"): (0, 50000),
        ("gateway", "related_memory_budget"): (0, 50000),
        ("gateway", "memory_detail_recall_max_ids"): (1, 50),
        ("gateway", "memory_detail_recall_budget"): (0, 50000),
        ("gateway", "current_inner_state_interval_rounds"): (0, 10000),
    }
    float_ranges: dict[tuple[str, str], tuple[float, float]] = {
        ("reranker", "timeout_seconds"): (1, 120),
        ("reranker", "score_weight"): (0, 1),
        ("dream", "daily_probability"): (0, 1),
        ("memory_diffusion", "min_activation"): (0, 1),
        ("memory_diffusion", "chain_min_confidence"): (0, 1),
        ("gateway", "cooldown_hours"): (0, 720),
        ("gateway", "recent_context_cooldown_hours"): (0, 720),
        ("gateway", "recent_context_reentry_idle_hours"): (0, 8760),
    }
    url_fields = {
        ("reranker", "base_url"),
        ("persona", "base_url"),
        ("dream", "base_url"),
        ("reflection", "base_url"),
        ("gateway", "domain_sentinel_base_url"),
    }
    secret_fields = {"api_key", "domain_sentinel_api_key"}

    for key, raw in value.items():
        field = f"{section}.{key}"
        if key == "upstreams":
            upstreams, upstream_env = _normalize_upstreams(
                raw,
                current_config=current_config,
                persist_env=persist_env,
            )
            result[key] = upstreams
            env_updates.update(upstream_env)
        elif key in bool_fields:
            result[key] = _clean_bool(raw, field)
        elif (section, key) in int_ranges:
            minimum, maximum = int_ranges[(section, key)]
            result[key] = _clean_int(raw, field, minimum, maximum)
        elif (section, key) in float_ranges:
            minimum, maximum = float_ranges[(section, key)]
            result[key] = _clean_float(raw, field, minimum, maximum)
        elif (section, key) in url_fields:
            result[key] = _clean_http_url(raw, field)
        elif key in secret_fields:
            secret = _clean_secret(raw, field)
            if secret:
                result[key] = secret
                if persist_env:
                    env_name = _CURRENT_SECRET_ENV_FIELDS[(section, key)]
                    env_updates[env_name] = secret
        elif (section, key) == ("reflection", "daily_chat_memory_mode"):
            result[key] = _clean_enum(
                raw, field, frozenset({"auto", "review", "off"})
            )
        elif key == "thinking_mode":
            result[key] = _clean_enum(
                raw, field, frozenset({"", "disabled", "enabled"})
            )
        elif (section, key) == ("gateway", "direct_render_mode"):
            result[key] = _clean_enum(
                raw, field, frozenset({"auto", "compact", "full"})
            )
        elif (section, key) == ("gateway", "retrieval_mode"):
            result[key] = _clean_enum(
                raw, field, frozenset({"graph", "bucket"})
            )
        else:
            max_chars = (
                _MAX_MODEL_TEXT_CHARS
                if key in {"model", "domain_sentinel_model"}
                else _MAX_CONFIG_TEXT_CHARS
            )
            result[key] = _clean_text(raw, field, max_chars=max_chars)
    return result, env_updates


def _normalize_legacy_section(
    section: str,
    value: object,
    *,
    persist_env: bool,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate config sections retained from the original Dashboard API."""
    if not isinstance(value, dict):
        raise ValueError(f"{section} must be an object")
    unknown = set(value) - _LEGACY_SECTION_FIELDS[section]
    if unknown:
        raise ValueError(
            f"{section} contains unknown fields: {', '.join(sorted(unknown))}"
        )

    result: dict[str, Any] = {}
    env_updates: dict[str, str] = {}
    for key, raw in value.items():
        field = f"{section}.{key}"
        if key == "api_key":
            secret = _clean_secret(raw, field)
            result[key] = secret
            if secret and persist_env:
                env_updates[_CURRENT_SECRET_ENV_FIELDS[(section, key)]] = secret
        elif key == "base_url":
            result[key] = _clean_http_url(raw, field)
        elif key == "model":
            result[key] = _clean_text(
                raw, field, max_chars=_MAX_MODEL_TEXT_CHARS
            )
        elif (section, key) == ("dehydration", "max_tokens"):
            result[key] = _clean_int(raw, field, 1, 32000)
        elif (section, key) == ("dehydration", "temperature"):
            result[key] = _clean_float(raw, field, 0, 2)
        elif key == "timeout_seconds":
            result[key] = _clean_float(raw, field, 1, 300)
        elif (section, key) == ("dehydration", "api_format"):
            result[key] = _clean_enum(
                raw,
                field,
                frozenset({"openai_compat", "anthropic", "gemini"}),
            )
        elif (section, key) == ("embedding", "api_format"):
            result[key] = _clean_enum(
                raw,
                field,
                frozenset({"openai_compat", "gemini", "ollama", "local"}),
            )
        elif (section, key) == ("embedding", "enabled"):
            result[key] = _clean_bool(raw, field)
        elif (section, key) == ("embedding", "backend"):
            result[key] = _clean_enum(
                raw, field, frozenset({"api", "gemini"})
            )
        elif section == "surfacing" and key == "breath_max_results":
            result[key] = _clean_int(raw, field, 1, 50)
        elif section == "surfacing" and key in {
            "breath_max_tokens",
            "feel_max_tokens",
        }:
            result[key] = _clean_int(raw, field, 500, 20000)
        elif section == "surfacing" and key == "sampling":
            if not isinstance(raw, dict):
                raise ValueError("surfacing.sampling must be an object")
            sampling_unknown = set(raw) - _SURFACING_SAMPLING_FIELDS
            if sampling_unknown:
                raise ValueError(
                    "surfacing.sampling contains unknown fields: "
                    + ", ".join(sorted(sampling_unknown))
                )
            sampling: dict[str, Any] = {}
            for sampling_key, sampling_raw in raw.items():
                sampling_field = f"surfacing.sampling.{sampling_key}"
                if sampling_key == "enabled":
                    sampling[sampling_key] = _clean_bool(
                        sampling_raw, sampling_field
                    )
                elif sampling_key in {"top_k", "sample_k"}:
                    sampling[sampling_key] = _clean_int(
                        sampling_raw, sampling_field, 1, 100
                    )
                else:
                    sampling[sampling_key] = _clean_float(
                        sampling_raw, sampling_field, 0.01, 100
                    )
            result[key] = sampling
        elif (section, key) == ("deployment", "public_url"):
            result[key] = _clean_text(
                raw, field, max_chars=_MAX_PROVIDER_URL_CHARS
            )
        else:  # pragma: no cover - schemas above are intentionally exhaustive
            raise ValueError(f"unsupported config field: {field}")
    return result, env_updates


def _normalize_current_config_request(
    body: dict[str, Any], current_config: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, str], bool]:
    unknown_root = set(body) - _CONFIG_POST_ROOT_FIELDS
    if unknown_root:
        raise ValueError(
            f"config contains unknown fields: {', '.join(sorted(unknown_root))}"
        )
    persist_env = _clean_bool(body.get("persist_env", False), "persist_env")
    if not persist_env:
        for section, secret_fields in _GATEWAY_ENV_ONLY_FIELDS.items():
            payload = body.get(section)
            if not isinstance(payload, Mapping):
                continue
            for secret_field in secret_fields:
                raw_secret = payload.get(secret_field)
                if isinstance(raw_secret, str) and raw_secret.strip():
                    raise ValueError(
                        f"{section}.{secret_field} requires persist_env=true"
                    )
    normalized = dict(body)
    normalized["persist_env"] = persist_env
    if "persist" in body:
        normalized["persist"] = _clean_bool(body["persist"], "persist")
    env_updates: dict[str, str] = {}
    for section in _LEGACY_SECTION_FIELDS:
        if section not in body:
            continue
        patch, section_env = _normalize_legacy_section(
            section,
            body[section],
            persist_env=persist_env,
        )
        normalized[section] = patch
        env_updates.update(section_env)
    for section in _CURRENT_SECTION_FIELDS:
        if section not in body:
            continue
        patch, section_env = _normalize_current_section(
            section,
            body[section],
            current_config=current_config,
            persist_env=persist_env,
        )
        normalized[section] = patch
        env_updates.update(section_env)
    if "merge_threshold" in body:
        normalized["merge_threshold"] = _clean_int(
            body["merge_threshold"], "merge_threshold", 0, 100
        )
    if "host_port" in body:
        normalized["host_port"] = _clean_int(
            body["host_port"], "host_port", 1, 65535
        )
    if "mcp_require_auth" in body:
        normalized["mcp_require_auth"] = _clean_bool(
            body["mcp_require_auth"], "mcp_require_auth"
        )
    if "mcp_auth_mode" in body:
        mode = _clean_text(
            body["mcp_auth_mode"],
            "mcp_auth_mode",
            max_chars=_MAX_PROVIDER_FORMAT_CHARS,
        ).lower()
        if mode not in {"oauth", "token", "hybrid"}:
            raise ValueError("mcp_auth_mode must be one of: oauth, token, hybrid")
        normalized["mcp_auth_mode"] = mode
    return normalized, env_updates, persist_env


def _atomic_update_env_vars(updates: Mapping[str, str]) -> None:
    """Compatibility wrapper around the shared cross-route transaction."""
    sh._atomic_update_env_vars(dict(updates))


def _serialize_env_value(value: str, *, shell_source: bool | None = None) -> str:
    return sh._serialize_env_value(value, shell_source=shell_source)


def _mapping_section(config: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    return dict(value) if isinstance(value, Mapping) else {}


def _configured_value(section: Mapping[str, Any], key: str, default: Any) -> Any:
    value = section.get(key)
    return default if value is None or value == "" else value


def _secret_value(config_value: object, env_name: str) -> str:
    return str(os.environ.get(env_name, "") or config_value or "").strip()


def _public_gateway_upstreams(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    gateway = _mapping_section(config, "gateway")
    raw_upstreams = gateway.get("upstreams")
    if not isinstance(raw_upstreams, list):
        return []
    payload: list[dict[str, Any]] = []
    for upstream_index, raw in enumerate(raw_upstreams, start=1):
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or f"upstream-{upstream_index}").strip()
        raw_envs = raw.get("api_key_envs", raw.get("api_key_env", []))
        if isinstance(raw_envs, str):
            raw_envs = [part.strip() for part in re.split(r"[\r\n,]+", raw_envs)]
        env_names = [
            str(item or "").strip()
            for item in raw_envs
            if str(item or "").strip()
        ] if isinstance(raw_envs, list) else []
        direct_keys: list[str] = []
        direct_key = str(raw.get("api_key") or "").strip()
        if direct_key:
            direct_keys.append(direct_key)
        raw_direct_keys = raw.get("api_keys")
        if isinstance(raw_direct_keys, str):
            direct_keys.extend(
                item.strip() for item in raw_direct_keys.split(",") if item.strip()
            )
        elif isinstance(raw_direct_keys, list):
            direct_keys.extend(
                str(item or "").strip()
                for item in raw_direct_keys
                if str(item or "").strip()
            )
        env_key_count = sum(bool(sh._read_env_var(env_name)) for env_name in env_names)
        models: list[str | dict[str, str]] = []
        raw_models = raw.get("models")
        if isinstance(raw_models, str):
            raw_models = [item.strip() for item in raw_models.split(",")]
        if isinstance(raw_models, list):
            for index, model in enumerate(raw_models[:_MAX_UPSTREAM_MODELS]):
                try:
                    models.append(_safe_model_entry(model, f"gateway.upstreams.models[{index}]"))
                except ValueError:
                    continue
        base_url = str(raw.get("base_url") or "").strip()
        key_count = len(direct_keys) + env_key_count
        payload.append(
            {
                "name": name,
                "protocol": _normalize_upstream_protocol(
                    raw.get("protocol")
                    or raw.get("api_format")
                    or raw.get("type"),
                    f"gateway.upstreams[{upstream_index - 1}].protocol",
                ),
                "base_url": base_url,
                "api_key_envs": env_names,
                "has_direct_api_key": bool(direct_keys),
                "key_count": key_count,
                "ready": bool(base_url and key_count),
                "default_model": str(raw.get("default_model") or "").strip(),
                "prompt_cache": str(raw.get("prompt_cache") or "").strip(),
                "prompt_cache_retention": str(
                    raw.get("prompt_cache_retention") or ""
                ).strip(),
                "anthropic_version": str(
                    raw.get("anthropic_version") or ""
                ).strip(),
                "anthropic_beta": str(raw.get("anthropic_beta") or "").strip(),
                "gemini_base_url": str(
                    raw.get("gemini_base_url")
                    or raw.get("native_base_url")
                    or raw.get("gemini_native_base_url")
                    or ""
                ).strip(),
                "gemini_auth": str(raw.get("gemini_auth") or "").strip(),
                "models": models,
            }
        )
    return payload


def _current_public_config(config: Mapping[str, Any]) -> dict[str, Any]:
    reranker = _mapping_section(config, "reranker")
    persona = _mapping_section(config, "persona")
    dream = _mapping_section(config, "dream")
    reflection = _mapping_section(config, "reflection")
    portrait = _mapping_section(config, "portrait")
    self_anchor = _mapping_section(config, "self_anchor")
    recall = _mapping_section(config, "recall")
    diffusion = _mapping_section(config, "memory_diffusion")
    gateway = _mapping_section(config, "gateway")
    embedding = _mapping_section(config, "embedding")
    dehydration = _mapping_section(config, "dehydration")

    reranker_key = _secret_value(
        reranker.get("api_key"), "OMBRE_RERANKER_API_KEY"
    )
    persona_key = _secret_value(persona.get("api_key"), "OMBRE_PERSONA_API_KEY")
    dream_key = _secret_value(dream.get("api_key"), "OMBRE_DREAM_API_KEY")
    reflection_key = _secret_value(
        reflection.get("api_key"), "OMBRE_REFLECTION_API_KEY"
    )
    domain_sentinel_key = _secret_value(
        gateway.get("domain_sentinel_api_key"), "OMBRE_DOMAIN_SENTINEL_API_KEY"
    )
    embedding_key = _secret_value(
        embedding.get("api_key"), "OMBRE_EMBED_API_KEY"
    )
    dehydration_key = _secret_value(
        dehydration.get("api_key"), "OMBRE_COMPRESS_API_KEY"
    )
    reranker_effective_url = str(
        reranker.get("base_url")
        or embedding.get("base_url")
        or dehydration.get("base_url")
        or ""
    ).strip()
    reranker_effective_key = reranker_key or embedding_key or dehydration_key
    persona_effective_url = str(
        os.environ.get("OMBRE_PERSONA_BASE_URL")
        or persona.get("base_url")
        or "https://api.deepseek.com/v1"
    ).strip()
    persona_effective_model = str(
        os.environ.get("OMBRE_PERSONA_MODEL")
        or persona.get("model")
        or "deepseek-chat"
    ).strip()
    persona_effective_key = persona_key or dehydration_key
    dream_effective_url = str(
        os.environ.get("OMBRE_DREAM_BASE_URL")
        or dream.get("base_url")
        or "https://api.deepseek.com"
    ).strip()
    dream_effective_model = str(
        os.environ.get("OMBRE_DREAM_MODEL")
        or dream.get("model")
        or "deepseek-v4-flash"
    ).strip()
    reflection_effective_url = str(
        reflection.get("base_url")
        or embedding.get("base_url")
        or persona.get("base_url")
        or dehydration.get("base_url")
        or ""
    ).strip()
    reflection_effective_model = str(
        reflection.get("model")
        or reflection.get("daily_chat_memory_candidate_model")
        or persona.get("model")
        or dehydration.get("model")
        or "deepseek-chat"
    ).strip()
    reflection_effective_key = (
        reflection_key
        or str(os.environ.get("OMBRE_EMBEDDING_API_KEY") or "").strip()
        or embedding_key
        or str(persona.get("api_key") or "").strip()
        or str(os.environ.get("OMBRE_PERSONA_API_KEY") or "").strip()
        or dehydration_key
    )

    return {
        "reranker": {
            "enabled": _parse_bool(reranker.get("enabled", True), default=True),
            "model": str(reranker.get("model") or "Qwen/Qwen3-Reranker-4B"),
            "base_url": str(reranker.get("base_url") or ""),
            "effective_base_url": reranker_effective_url,
            "api_key_masked": _mask_secret(reranker_key),
            "has_own_api_key": bool(reranker_key),
            "api_ready": bool(reranker_effective_key and reranker_effective_url),
            "timeout_seconds": float(
                _configured_value(reranker, "timeout_seconds", 12)
            ),
            "candidate_limit": int(
                _configured_value(reranker, "candidate_limit", 20)
            ),
            "score_weight": float(
                _configured_value(reranker, "score_weight", 0.65)
            ),
        },
        "persona": {
            "enabled": _parse_bool(persona.get("enabled", True), default=True),
            "event_recording_enabled": _parse_bool(
                persona.get("event_recording_enabled", True), default=True
            ),
            "conflict_nudge_enabled": _parse_bool(
                persona.get("conflict_nudge_enabled", False), default=False
            ),
            "json_response_format": _parse_bool(
                persona.get("json_response_format", True), default=True
            ),
            "model": str(persona.get("model") or ""),
            "base_url": str(persona.get("base_url") or ""),
            "effective_model": persona_effective_model,
            "effective_base_url": persona_effective_url,
            "api_key_masked": _mask_secret(persona_key),
            "has_own_api_key": bool(persona_key),
            "api_ready": bool(persona_effective_key and persona_effective_url),
        },
        "dream": {
            "enabled": _parse_bool(dream.get("enabled", True), default=True),
            "auto_enabled": _parse_bool(
                dream.get("auto_enabled", True), default=True
            ),
            "surface_enabled": _parse_bool(
                dream.get("surface_enabled", True), default=True
            ),
            "inject_enabled": _parse_bool(
                dream.get("inject_enabled", False), default=False
            ),
            "retain_after_inject": _parse_bool(
                dream.get("retain_after_inject", True), default=True
            ),
            "model": str(dream.get("model") or ""),
            "base_url": str(dream.get("base_url") or ""),
            "effective_model": dream_effective_model,
            "effective_base_url": dream_effective_url,
            "api_key_masked": _mask_secret(dream_key),
            "has_own_api_key": bool(dream_key),
            "api_ready": bool(dream_key and dream_effective_url),
            "daily_hour": int(_configured_value(dream, "daily_hour", 3)),
            "daily_probability": float(
                _configured_value(dream, "daily_probability", 0.4)
            ),
            "min_material_count": int(
                _configured_value(dream, "min_material_count", 5)
            ),
            "material_window_hours": int(
                _configured_value(dream, "material_window_hours", 48)
            ),
            "identity_anchor_id": str(dream.get("identity_anchor_id") or ""),
        },
        "reflection": {
            "enabled": _parse_bool(reflection.get("enabled", True), default=True),
            "auto_enabled": _parse_bool(
                reflection.get("auto_enabled", True), default=True
            ),
            "daily_enabled": _parse_bool(
                reflection.get("daily_enabled", True), default=True
            ),
            "daily_min_memory_items": int(
                _configured_value(reflection, "daily_min_memory_items", 5)
            ),
            "daily_conversation_turn_limit": int(
                _configured_value(reflection, "daily_conversation_turn_limit", 12)
            ),
            "daily_chat_memory_mode": str(
                reflection.get("daily_chat_memory_mode") or "off"
            ),
            "daily_chat_memory_turn_limit": int(
                _configured_value(reflection, "daily_chat_memory_turn_limit", 0)
            ),
            "memory_affect_anchor_enabled": _parse_bool(
                reflection.get("memory_affect_anchor_enabled", False), default=False
            ),
            "relationship_weather_affect_anchor_enabled": _parse_bool(
                reflection.get(
                    "relationship_weather_affect_anchor_enabled", False
                ),
                default=False,
            ),
            "model": str(reflection.get("model") or ""),
            "effective_model": reflection_effective_model,
            "thinking_mode": normalize_thinking_mode(
                reflection.get("thinking_mode")
            ),
            "base_url": str(reflection.get("base_url") or ""),
            "effective_base_url": reflection_effective_url,
            "api_key_masked": _mask_secret(reflection_key),
            "has_own_api_key": bool(reflection_key),
            "api_ready": bool(
                reflection_effective_key and reflection_effective_url
            ),
        },
        "portrait": {
            "enabled": _parse_bool(portrait.get("enabled", True), default=True),
            "auto_enabled": _parse_bool(
                portrait.get("auto_enabled", True), default=True
            ),
            "auto_initial_enabled": _parse_bool(
                portrait.get("auto_initial_enabled", False), default=False
            ),
            "daily_enabled": _parse_bool(
                portrait.get("daily_enabled", True), default=True
            ),
            "material_limit": int(
                _configured_value(portrait, "material_limit", 18)
            ),
            "first_run_material_limit": int(
                _configured_value(portrait, "first_run_material_limit", 160)
            ),
            "user_rewrite_evidence_delta": int(
                _configured_value(portrait, "user_rewrite_evidence_delta", 10)
            ),
            "manual_suppress_days": int(
                _configured_value(portrait, "manual_suppress_days", 14)
            ),
        },
        "self_anchor": {
            "entry_bucket_id": str(self_anchor.get("entry_bucket_id") or "")
        },
        "recall": {
            "query_resurface_enabled": _parse_bool(
                recall.get("query_resurface_enabled", False), default=False
            )
        },
        "memory_diffusion": {
            "enabled": _parse_bool(diffusion.get("enabled", True), default=True),
            "top_k": int(_configured_value(diffusion, "top_k", 4)),
            "min_activation": float(
                _configured_value(diffusion, "min_activation", 0.18)
            ),
            "chain_walk_enabled": _parse_bool(
                diffusion.get("chain_walk_enabled", False), default=False
            ),
            "chain_max_hops": int(
                _configured_value(diffusion, "chain_max_hops", 6)
            ),
            "chain_min_confidence": float(
                _configured_value(diffusion, "chain_min_confidence", 0.72)
            ),
            "chain_max_frontier": int(
                _configured_value(diffusion, "chain_max_frontier", 24)
            ),
        },
        "gateway": {
            "upstreams": _public_gateway_upstreams(config),
            "cooldown_hours": float(
                _configured_value(gateway, "cooldown_hours", 6)
            ),
            "skip_recent_rounds": int(
                _configured_value(gateway, "skip_recent_rounds", 5)
            ),
            "recent_context_cooldown_hours": float(
                _configured_value(gateway, "recent_context_cooldown_hours", 6)
            ),
            "recent_context_reentry_idle_hours": float(
                _configured_value(
                    gateway, "recent_context_reentry_idle_hours", 24
                )
            ),
            "recent_context_budget": int(
                _configured_value(gateway, "recent_context_budget", 300)
            ),
            "recalled_memory_budget": int(
                _configured_value(gateway, "recalled_memory_budget", 900)
            ),
            "related_memory_budget": int(
                _configured_value(gateway, "related_memory_budget", 220)
            ),
            "memory_detail_recall_enabled": _parse_bool(
                gateway.get("memory_detail_recall_enabled", False), default=False
            ),
            "memory_detail_recall_max_ids": int(
                _configured_value(gateway, "memory_detail_recall_max_ids", 3)
            ),
            "memory_detail_recall_budget": int(
                _configured_value(gateway, "memory_detail_recall_budget", 1200)
            ),
            "current_inner_state_interval_rounds": int(
                _configured_value(
                    gateway, "current_inner_state_interval_rounds", 15
                )
            ),
            "direct_render_mode": normalize_direct_render_mode(
                gateway.get("direct_render_mode")
            ),
            "retrieval_mode": normalize_retrieval_mode(
                gateway.get("retrieval_mode")
            ),
            "operit_context_rewrite_enabled": _parse_bool(
                gateway.get("operit_context_rewrite_enabled", False), default=False
            ),
            "word_map_hint_enabled": _parse_bool(
                gateway.get("word_map_hint_enabled", False), default=False
            ),
            "query_planner_enabled": _parse_bool(
                gateway.get("query_planner_enabled", False), default=False
            ),
            "domain_sentinel_enabled": _parse_bool(
                gateway.get("domain_sentinel_enabled", True), default=True
            ),
            "domain_sentinel_model": str(
                gateway.get("domain_sentinel_model") or ""
            ),
            "domain_sentinel_base_url": str(
                gateway.get("domain_sentinel_base_url") or ""
            ),
            "domain_sentinel_api_key_masked": _mask_secret(domain_sentinel_key),
            "domain_sentinel_api_ready": bool(domain_sentinel_key),
        },
        "embedding_secret": {
            "api_key_masked": _mask_secret(embedding_key),
            "has_own_api_key": bool(embedding_key),
        },
        "dehydration_secret": {
            "api_key_masked": _mask_secret(dehydration_key),
        },
    }


def _persist_current_sections(save_config: dict[str, Any], body: Mapping[str, Any]) -> None:
    for section in _CURRENT_SECTION_FIELDS:
        patch = body.get(section)
        if not isinstance(patch, Mapping):
            continue
        existing = save_config.get(section)
        if not isinstance(existing, dict):
            existing = {}
            save_config[section] = existing
        for key, value in patch.items():
            if key in {"api_key", "domain_sentinel_api_key"}:
                continue
            existing[key] = deepcopy(value)


def _refresh_current_engine_clients(changed_sections: set[str]) -> None:
    """Rebuild direct engine clients or fail the surrounding transaction.

    A config POST must never report success while an active engine still owns a
    client for the previous provider tuple.  Callers snapshot every mutable
    runtime object before reaching this function, so constructor failures are
    intentionally allowed to propagate into that rollback path.
    """
    from openai import AsyncOpenAI

    for section, name, timeout in (
        ("persona", "persona_engine", 30.0),
        ("reflection", "reflection_engine", 45.0),
        ("portrait", "portrait_engine", 45.0),
        ("dream", "dream_engine", 60.0),
    ):
        if section not in changed_sections:
            continue
        engine = getattr(sh, name, None)
        if engine is None:
            continue
        enabled = bool(getattr(engine, "enabled", True))
        if section == "persona":
            enabled = enabled and str(getattr(engine, "mode", "llm")) == "llm"
        api_key = str(getattr(engine, "api_key", "") or "")
        base_url = str(getattr(engine, "base_url", "") or "")
        engine.client = (
            AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
            if enabled and api_key and base_url
            else None
        )


_RUNTIME_DEPENDENCY_CASCADE: dict[str, frozenset[str]] = {
    "embedding": frozenset({"reranker", "reflection"}),
    "dehydration": frozenset({"reranker", "persona", "reflection"}),
    "reranker": frozenset({"reranker"}),
    "persona": frozenset({"persona", "reflection"}),
    "reflection": frozenset({"reflection"}),
}


def _build_dependency_runtime_engine(
    section: str,
    config: dict[str, Any],
    pending_env: Mapping[str, str] | None = None,
) -> object:
    """Build normalized provider state without starting side-effectful engines.

    Persona construction migrates its SQLite schema, so using the production
    constructor for a dashboard hot refresh would make validation itself write
    to disk.  Reflection has a similarly large initialization surface.  Their
    provider/client state is resolved here with the same precedence as their
    constructors, leaving databases, queues, and scheduler state on the active
    objects untouched.
    """
    def env_value(name: str) -> str:
        if pending_env is not None and name in pending_env:
            return str(pending_env[name] or "")
        return str(os.environ.get(name) or "")

    if section == "reranker":
        from reranker_engine import RerankerEngine

        return RerankerEngine(config)
    if section == "persona":
        persona = _mapping_section(config, "persona")
        dehydration = _mapping_section(config, "dehydration")
        enabled = bool(persona.get("enabled", True))
        mode = str(persona.get("mode") or "llm")
        base_url = str(
            env_value("OMBRE_PERSONA_BASE_URL")
            or persona.get("base_url")
            or "https://api.deepseek.com/v1"
        )
        model = str(
            env_value("OMBRE_PERSONA_MODEL")
            or persona.get("model")
            or "deepseek-chat"
        )
        api_key = str(
            env_value("OMBRE_PERSONA_API_KEY")
            or persona.get("api_key")
            or dehydration.get("api_key")
            or ""
        )
        from openai import AsyncOpenAI

        client = (
            AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=30.0)
            if enabled and mode == "llm" and api_key
            else None
        )
        return SimpleNamespace(
            enabled=enabled,
            event_recording_enabled=_parse_bool(
                persona.get("event_recording_enabled", True), default=True
            ),
            conflict_nudge_enabled=_parse_bool(
                persona.get("conflict_nudge_enabled", False), default=False
            ),
            model=model,
            base_url=base_url,
            api_key=api_key,
            client=client,
        )
    if section == "reflection":
        from config_modes import normalize_thinking_mode
        from openai import AsyncOpenAI

        reflection = _mapping_section(config, "reflection")
        embedding = _mapping_section(config, "embedding")
        persona = _mapping_section(config, "persona")
        dehydration = _mapping_section(config, "dehydration")
        enabled = bool(reflection.get("enabled", True))
        base_url = str(
            reflection.get("base_url")
            or embedding.get("base_url")
            or persona.get("base_url")
            or dehydration.get("base_url")
            or ""
        )
        model = str(
            reflection.get("model")
            or reflection.get("daily_chat_memory_candidate_model")
            or persona.get("model")
            or dehydration.get("model")
            or "deepseek-chat"
        )
        api_key = str(
            env_value("OMBRE_REFLECTION_API_KEY")
            or reflection.get("api_key")
            or env_value("OMBRE_EMBEDDING_API_KEY")
            or embedding.get("api_key")
            or persona.get("api_key")
            or env_value("OMBRE_PERSONA_API_KEY")
            or dehydration.get("api_key")
            or ""
        )
        client = (
            AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=45.0)
            if enabled and api_key and base_url
            else None
        )
        dehydration_base_url = str(
            dehydration.get("base_url") or ""
        ).strip().rstrip("/")
        dehydration_model = str(dehydration.get("model") or "").strip()
        dehydration_api_key = str(
            dehydration.get("api_key") or env_value("OMBRE_API_KEY") or ""
        ).strip()
        dehydration_client = (
            AsyncOpenAI(
                api_key=dehydration_api_key,
                base_url=dehydration_base_url,
                timeout=45.0,
            )
            if (
                enabled
                and dehydration_api_key
                and dehydration_base_url
                and dehydration_model
            )
            else None
        )
        daily_chat_memory_mode = str(
            reflection.get("daily_chat_memory_mode") or "off"
        ).strip().lower()
        if daily_chat_memory_mode not in {"auto", "review", "off"}:
            daily_chat_memory_mode = "off"
        return SimpleNamespace(
            enabled=enabled,
            auto_enabled=bool(reflection.get("auto_enabled", True)),
            daily_enabled=bool(reflection.get("daily_enabled", True)),
            daily_min_memory_items=max(
                0, int(reflection.get("daily_min_memory_items", 5))
            ),
            daily_conversation_turn_limit=max(
                0,
                min(
                    80,
                    int(reflection.get("daily_conversation_turn_limit", 12)),
                ),
            ),
            daily_chat_memory_mode=daily_chat_memory_mode,
            daily_chat_memory_turn_limit=max(
                0,
                min(
                    10000,
                    int(reflection.get("daily_chat_memory_turn_limit", 0)),
                ),
            ),
            memory_affect_anchor_enabled=bool(
                reflection.get("memory_affect_anchor_enabled", False)
            ),
            relationship_weather_affect_anchor_enabled=bool(
                reflection.get(
                    "relationship_weather_affect_anchor_enabled", False
                )
            ),
            model=model,
            thinking_mode=normalize_thinking_mode(
                reflection.get("thinking_mode")
                or persona.get("thinking_mode")
                or ""
            ),
            base_url=base_url,
            api_key=api_key,
            client=client,
            dehydration_base_url=dehydration_base_url,
            dehydration_model=dehydration_model,
            dehydration_api_key=dehydration_api_key,
            dehydration_client=dehydration_client,
            daily_activity_summary_dehydration_client=dehydration_client,
        )
    raise ValueError(f"unsupported runtime dependency engine: {section}")


def _commit_dependency_runtime_engine(
    section: str,
    target: object,
    staged: object,
) -> None:
    """Publish normalized state while preserving references held by consumers."""
    del section  # retained in the signature for diagnostics/failure injection
    for attribute, value in vars(staged).items():
        setattr(target, attribute, value)


def _refresh_runtime_dependency_cascade(
    changed_sections: set[str],
    engines: Mapping[str, object | None],
    pending_env: Mapping[str, str] | None = None,
) -> list[str]:
    """Stage then atomically publish every active inherited-config consumer.

    All constructors run before the first active object is touched.  A commit
    failure can still occur (for example a custom runtime proxy), but the POST
    handler's pre-update snapshot restores all already-touched objects.
    """
    affected: set[str] = set()
    for changed_section in changed_sections:
        affected.update(_RUNTIME_DEPENDENCY_CASCADE.get(changed_section, ()))

    staged: list[tuple[str, object, object]] = []
    for section in ("reranker", "persona", "reflection"):
        target = engines.get(section)
        if section not in affected or target is None:
            continue
        replacement = _build_dependency_runtime_engine(
            section,
            sh.config,
            pending_env,
        )
        staged.append((section, target, replacement))

    refreshed: list[str] = []
    for section, target, replacement in staged:
        _commit_dependency_runtime_engine(section, target, replacement)
        refreshed.append(f"{section}.runtime_refreshed")
    return refreshed


def _apply_current_runtime_sections(
    body: Mapping[str, Any],
    *,
    pending_env: Mapping[str, str] | None = None,
) -> list[str]:
    updated: list[str] = []
    for section in _CURRENT_SECTION_FIELDS:
        patch = body.get(section)
        if not isinstance(patch, Mapping):
            continue
        target = sh.config.get(section)
        if not isinstance(target, dict):
            target = {}
            sh.config[section] = target
        for key, value in patch.items():
            target[key] = deepcopy(value)
            updated.append(f"{section}.{key}")

    engine_fields: dict[str, dict[str, str]] = {
        "dream": {
            "enabled": "enabled",
            "auto_enabled": "auto_enabled",
            "surface_enabled": "surface_enabled",
            "retain_after_inject": "retain_after_surface",
            "model": "model",
            "base_url": "base_url",
            "api_key": "api_key",
            "daily_hour": "daily_hour",
            "daily_probability": "daily_probability",
            "min_material_count": "min_material_count",
            "material_window_hours": "material_window_hours",
            "identity_anchor_id": "identity_anchor_id",
        },
        "portrait": {
            "enabled": "enabled",
            "auto_enabled": "auto_enabled",
            "auto_initial_enabled": "auto_initial_enabled",
            "daily_enabled": "daily_enabled",
            "material_limit": "material_limit",
            "first_run_material_limit": "first_run_material_limit",
            "user_rewrite_evidence_delta": "user_rewrite_evidence_delta",
            "manual_suppress_days": "manual_suppress_days",
        },
    }
    try:
        from tools import _runtime as tools_runtime
    except ImportError:  # pragma: no cover
        tools_runtime = None
    engines = {
        "reranker": getattr(tools_runtime, "reranker_engine", None)
        if tools_runtime is not None
        else None,
        "persona": getattr(sh, "persona_engine", None),
        "dream": getattr(sh, "dream_engine", None),
        "reflection": getattr(sh, "reflection_engine", None),
        "portrait": getattr(sh, "portrait_engine", None),
    }
    for section, field_map in engine_fields.items():
        patch = body.get(section)
        engine = engines.get(section)
        if not isinstance(patch, Mapping) or engine is None:
            continue
        for config_key, attr_name in field_map.items():
            if config_key in patch:
                setattr(engine, attr_name, patch[config_key])
    dream_patch = body.get("dream")
    dream_engine = engines.get("dream")
    if isinstance(dream_patch, Mapping) and dream_engine is not None:
        public_dream = _current_public_config(sh.config).get("dream")
        if isinstance(public_dream, Mapping):
            if "model" in dream_patch:
                setattr(dream_engine, "model", public_dream["effective_model"])
            if "base_url" in dream_patch:
                setattr(
                    dream_engine,
                    "base_url",
                    public_dream["effective_base_url"],
                )
    _refresh_current_engine_clients(set(body) & set(engine_fields))
    updated.extend(
        _refresh_runtime_dependency_cascade(set(body), engines, pending_env)
    )
    return updated


_ConfigPath = tuple[str, ...]
_MISSING = object()


def _runtime_config_paths(body: Mapping[str, Any]) -> tuple[_ConfigPath, ...]:
    """Return only the live config leaves owned by one normalized request."""
    paths: list[_ConfigPath] = []
    for section in _CURRENT_SECTION_FIELDS:
        patch = body.get(section)
        if isinstance(patch, Mapping):
            paths.extend((section, str(key)) for key in patch)

    for section, keys in (
        (
            "dehydration",
            (
                "model",
                "base_url",
                "max_tokens",
                "temperature",
                "api_format",
                "timeout_seconds",
            ),
        ),
        (
            "embedding",
            (
                "enabled",
                "model",
                "base_url",
                "timeout_seconds",
                "api_format",
                "backend",
            ),
        ),
    ):
        patch = body.get(section)
        if not isinstance(patch, Mapping):
            continue
        paths.extend((section, key) for key in keys if key in patch)
        if patch.get("api_key"):
            paths.append((section, "api_key"))

    surfacing = body.get("surfacing")
    if isinstance(surfacing, Mapping):
        paths.extend(
            ("surfacing", key)
            for key in (
                "breath_max_results",
                "breath_max_tokens",
                "feel_max_tokens",
            )
            if key in surfacing
        )
    paths.extend(
        (key,)
        for key in ("merge_threshold", "host_port")
        if key in body
    )
    return tuple(dict.fromkeys(paths))


def _persisted_config_paths(body: Mapping[str, Any]) -> tuple[_ConfigPath, ...]:
    """Return the YAML leaves written by one normalized config request."""
    paths: list[_ConfigPath] = []
    for section in _CURRENT_SECTION_FIELDS:
        patch = body.get(section)
        if not isinstance(patch, Mapping):
            continue
        paths.extend(
            (section, str(key))
            for key in patch
            if key not in {"api_key", "domain_sentinel_api_key"}
        )

    dehydration = body.get("dehydration")
    if isinstance(dehydration, Mapping):
        paths.extend(
            ("dehydration", key)
            for key in (
                "model",
                "base_url",
                "max_tokens",
                "temperature",
                "api_format",
                "timeout_seconds",
            )
            if key in dehydration
        )
    embedding = body.get("embedding")
    if isinstance(embedding, Mapping):
        paths.extend(
            ("embedding", key)
            for key in (
                "model",
                "base_url",
                "api_format",
                "timeout_seconds",
                "enabled",
                "backend",
            )
            if key in embedding
        )
    surfacing = body.get("surfacing")
    if isinstance(surfacing, Mapping):
        paths.extend(
            ("surfacing", key)
            for key in (
                "breath_max_results",
                "breath_max_tokens",
                "feel_max_tokens",
            )
            if key in surfacing
        )
        sampling = surfacing.get("sampling")
        if isinstance(sampling, Mapping):
            paths.extend(
                ("surfacing", "sampling", str(key)) for key in sampling
            )
    deployment = body.get("deployment")
    if isinstance(deployment, Mapping) and "public_url" in deployment:
        paths.append(("deployment", "public_url"))
    paths.extend(
        (key,)
        for key in (
            "merge_threshold",
            "mcp_require_auth",
            "mcp_auth_mode",
            "host_port",
        )
        if key in body
    )
    return tuple(dict.fromkeys(paths))


def _env_runtime_config_paths(
    accepted: Mapping[str, str],
    fields: Mapping[str, Mapping[str, Any]],
) -> tuple[_ConfigPath, ...]:
    paths: list[_ConfigPath] = []
    for env_name in accepted:
        in_memory = fields[env_name]["in_memory"]
        if in_memory:
            section, key = in_memory
            paths.append((str(section), str(key)))
    return tuple(dict.fromkeys(paths))


def _path_state(
    source: Mapping[str, Any], path: _ConfigPath
) -> tuple[bool, Any]:
    value: Any = source
    for part in path:
        if not isinstance(value, Mapping) or part not in value:
            return False, _MISSING
        value = value[part]
    return True, value


def _values_match(left: Any, right: Any) -> bool:
    if left is right:
        return True
    try:
        result = left == right
    except Exception:
        return False
    return result if isinstance(result, bool) else False


def _path_states_match(left: tuple[bool, Any], right: tuple[bool, Any]) -> bool:
    if left[0] != right[0]:
        return False
    return not left[0] or _values_match(left[1], right[1])


def _write_mapping_path(
    current: dict[str, Any],
    before: Mapping[str, Any],
    path: _ConfigPath,
    state: tuple[bool, Any],
) -> None:
    container = current
    parents: list[tuple[dict[str, Any], str, _ConfigPath]] = []
    for depth, part in enumerate(path[:-1], start=1):
        child = container.get(part)
        if not isinstance(child, dict):
            if not state[0] and part not in container:
                return
            child = {}
            container[part] = child
        parents.append((container, part, path[:depth]))
        container = child

    leaf = path[-1]
    if state[0]:
        container[leaf] = deepcopy(state[1])
        return
    container.pop(leaf, None)
    for parent, key, prefix in reversed(parents):
        child = parent.get(key)
        before_parent_present, _before_parent = _path_state(before, prefix)
        if isinstance(child, dict) and not child and not before_parent_present:
            parent.pop(key, None)
            continue
        break


def _restore_mapping_paths(
    current: dict[str, Any],
    before: Mapping[str, Any],
    expected: Mapping[str, Any],
    paths: tuple[_ConfigPath, ...],
) -> None:
    """Compare-and-swap rollback for transaction-owned config leaves."""
    for path in paths:
        expected_state = _path_state(expected, path)
        if not _path_states_match(_path_state(current, path), expected_state):
            continue
        _write_mapping_path(current, before, path, _path_state(before, path))


def _snapshot_config_runtime() -> dict[str, Any]:
    """Capture mutable runtime state touched by POST /api/config."""
    candidates = [
        getattr(sh, name, None)
        for name in (
            "dehydrator",
            "embedding_engine",
            "persona_engine",
            "dream_engine",
            "reflection_engine",
            "portrait_engine",
        )
    ]
    try:
        from tools import _runtime as tools_runtime
    except ImportError:  # pragma: no cover
        tools_runtime = None
    if tools_runtime is not None:
        candidates.append(getattr(tools_runtime, "reranker_engine", None))

    object_states: list[tuple[object, dict[str, Any]]] = []
    seen: set[int] = set()
    for candidate in candidates:
        if candidate is None or id(candidate) in seen:
            continue
        try:
            state = dict(vars(candidate))
        except TypeError:
            continue
        seen.add(id(candidate))
        object_states.append((candidate, state))
    return {
        "config": deepcopy(sh.config),
        "embedding_engine": getattr(sh, "embedding_engine", None),
        "object_states": object_states,
    }


def _restore_config_runtime(
    snapshot: Mapping[str, Any],
    expected: Mapping[str, Any],
    config_paths: tuple[_ConfigPath, ...],
) -> None:
    """Conditionally roll back only state still owned by this transaction."""
    if isinstance(sh.config, dict):
        _restore_mapping_paths(
            sh.config,
            cast(Mapping[str, Any], snapshot["config"]),
            cast(Mapping[str, Any], expected["config"]),
            config_paths,
        )

    expected_states = {
        id(candidate): state
        for candidate, state in cast(
            list[tuple[object, dict[str, Any]]], expected["object_states"]
        )
    }
    restored_object_ids: set[int] = set()
    for candidate, before_state in cast(
        list[tuple[object, dict[str, Any]]], snapshot["object_states"]
    ):
        expected_state = expected_states.get(id(candidate))
        if expected_state is None:
            continue
        try:
            current_state = vars(candidate)
        except TypeError:
            continue
        for key in set(before_state) | set(expected_state):
            before_value = before_state.get(key, _MISSING)
            expected_value = expected_state.get(key, _MISSING)
            if _values_match(before_value, expected_value):
                continue
            current_value = current_state.get(key, _MISSING)
            if not _values_match(current_value, expected_value):
                continue
            if before_value is _MISSING:
                current_state.pop(key, None)
            else:
                current_state[key] = before_value
            restored_object_ids.add(id(candidate))

    previous_embedding = snapshot["embedding_engine"]
    expected_embedding = expected["embedding_engine"]
    if (
        getattr(sh, "embedding_engine", None) is expected_embedding
        and (
            previous_embedding is not expected_embedding
            or id(previous_embedding) in restored_object_ids
        )
    ):
        sh.replace_embedding_engine(previous_embedding)


def _bounded_config_int(value, field: str, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer in [{low},{high}]")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"{field} must be an integer in [{low},{high}]"
        ) from exc
    if isinstance(value, float) and (
        not math.isfinite(value) or value != parsed
    ):
        raise ValueError(f"{field} must be an integer in [{low},{high}]")
    if not low <= parsed <= high:
        raise ValueError(f"{field} must be in [{low},{high}]")
    return parsed


def _bounded_config_float(value, field: str, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number in [{low},{high}]")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be a finite number in [{low},{high}]") from exc
    if not math.isfinite(parsed) or not low <= parsed <= high:
        raise ValueError(f"{field} must be a finite number in [{low},{high}]")
    return parsed


def _rebuild_embedding_runtime():
    """Rebuild and publish one embedding engine to every runtime holder."""
    try:
        from embedding_engine import EmbeddingEngine  # type: ignore
    except ImportError:  # pragma: no cover
        from ..embedding_engine import EmbeddingEngine  # type: ignore

    engine = EmbeddingEngine(sh.config)
    sh.replace_embedding_engine(engine)
    return engine


def _mcp_auth_mode(config: Mapping[str, object] | object) -> str:
    """规范化一个配置快照中的 MCP 鉴权模式。"""
    raw = (
        str(config.get("mcp_auth_mode", "oauth")).strip().lower()
        if isinstance(config, Mapping)
        else "oauth"
    )
    return raw if raw in ("oauth", "token", "hybrid") else "oauth"


def _current_mcp_token() -> str:
    """Live static MCP token — env wins over config.yaml, same priority as validation."""
    return (
        os.environ.get("OMBRE_MCP_TOKEN", "").strip()
        or str(sh.config.get("mcp_token", "") or "").strip()
    )


def _mask_mcp_token(token: str) -> str | None:
    if not token:
        return None
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def register(mcp) -> None:
    # 该锁只保护本次路由注册实例，不绑定 asyncio 事件循环；这样同一处理器
    # 被测试客户端从多个事件循环调用时，也能串行提交而不会触发跨循环错误。
    mcp_token_commit_lock = threading.Lock()

    # MCP 鉴权在进程启动时绑定到中间件和 OAuth 路由可见性。有效值与期望持久值
    # 必须分开，避免 Dashboard 错称启动期切换已经热生效。
    runtime_mcp_auth_required = _parse_bool(
        sh.config.get("mcp_require_auth", True), default=True
    )
    runtime_mcp_auth_mode = _mcp_auth_mode(sh.config)
    runtime_transport = str(sh.config.get("transport") or "stdio")
    # deployment.public_url 参与 OAuth resource/audience 绑定，同样是启动快照。
    # Dashboard 往返使用独立期望值；重启前发布到 sh.config 会让已绑定的 OAuth
    # 路由与 MCP 中间件看到不同配置。
    runtime_public_url = configured_public_origin(sh.config)

    def _desired_startup_state(persisted: Mapping[str, object]) -> dict[str, object]:
        persisted_deployment = persisted.get("deployment")
        has_persisted_deployment = isinstance(persisted_deployment, Mapping)
        return {
            "transport": runtime_transport
            if "OMBRE_TRANSPORT" in BOOT_ENV_CONFIG
            else (
                str(persisted.get("transport") or runtime_transport)
                if "transport" in persisted
                else runtime_transport
            ),
            "mcp_require_auth": runtime_mcp_auth_required
            if "OMBRE_MCP_REQUIRE_AUTH" in BOOT_ENV_CONFIG
            else (
                _parse_bool(
                    persisted.get("mcp_require_auth"),
                    default=runtime_mcp_auth_required,
                )
                if "mcp_require_auth" in persisted
                else runtime_mcp_auth_required
            ),
            "mcp_auth_mode": runtime_mcp_auth_mode
            if "OMBRE_MCP_AUTH_MODE" in BOOT_ENV_CONFIG
            else (
                _mcp_auth_mode(persisted)
                if "mcp_auth_mode" in persisted
                else runtime_mcp_auth_mode
            ),
            "public_url": configured_public_origin(persisted)
            if has_persisted_deployment
            else runtime_public_url,
        }

    def _runtime_network_security(desired_auth_required: object | None = None) -> dict:
        return current_mcp_network_security(
            sh.config,
            desired_auth_required=desired_auth_required,
            environment=os.environ,
            in_docker=sh.in_docker(),
        )

    @mcp.custom_route("/dashboard", methods=["GET"])
    async def dashboard(request: Request) -> Response:
        """Keep the current production dashboard URL stable after migration."""
        from starlette.responses import RedirectResponse

        # A relative Location header keeps reverse-proxy / ASGI mount prefixes
        # intact without trusting a forwarded prefix to construct a new URL.
        target = "memory-dashboard"
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(url=target, status_code=302)


    @mcp.custom_route("/api/env-vars", methods=["GET"])
    async def api_env_vars(request: Request) -> Response:
        """Return status of all known OMBRE_* env vars (sensitive fields masked)."""
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err

        # 启动期被平台注入的可配置 env 集合（在任何 dashboard 保存 mutate os.environ 之前快照）。
        # from_boot=True ⇒ 该变量是平台级 env，重启后会覆盖 dashboard 存进 config.yaml 的值。
        from utils import BOOT_ENV_CONFIG

        def _masked(name: str) -> dict:
            return {"set": bool(os.environ.get(name, "").strip()), "value": None,
                    "from_boot": name in BOOT_ENV_CONFIG}

        def _plain(name: str) -> dict:
            v = os.environ.get(name, "").strip()
            return {"set": bool(v), "value": v or None, "from_boot": name in BOOT_ENV_CONFIG}

        vars_data = [
            # LLM 压缩组
            {"name": "OMBRE_COMPRESS_API_KEY", "group": "llm", "label": "压缩 LLM API Key", "sensitive": True, **_masked("OMBRE_COMPRESS_API_KEY")},
            {"name": "OMBRE_COMPRESS_BASE_URL", "group": "llm", "label": "压缩 LLM Base URL", "sensitive": False, **_plain("OMBRE_COMPRESS_BASE_URL")},
            {"name": "OMBRE_COMPRESS_MODEL", "group": "llm", "label": "压缩 LLM 模型", "sensitive": False, **_plain("OMBRE_COMPRESS_MODEL")},
            {"name": "OMBRE_COMPRESS_TIMEOUT_SECONDS", "group": "llm", "label": "压缩 LLM 超时秒数", "sensitive": False, **_plain("OMBRE_COMPRESS_TIMEOUT_SECONDS")},
            # Embedding 组
            {"name": "OMBRE_EMBED_API_KEY", "group": "embed", "label": "向量化 API Key", "sensitive": True, **_masked("OMBRE_EMBED_API_KEY")},
            {"name": "OMBRE_EMBED_BASE_URL", "group": "embed", "label": "向量化 Base URL", "sensitive": False, **_plain("OMBRE_EMBED_BASE_URL")},
            {"name": "OMBRE_EMBED_MODEL", "group": "embed", "label": "向量化模型", "sensitive": False, **_plain("OMBRE_EMBED_MODEL")},
            {"name": "OMBRE_EMBED_TIMEOUT_SECONDS", "group": "embed", "label": "向量化超时秒数", "sensitive": False, **_plain("OMBRE_EMBED_TIMEOUT_SECONDS")},
            # 服务配置组
            {"name": "OMBRE_TRANSPORT", "group": "system", "label": "传输模式", "sensitive": False, **_plain("OMBRE_TRANSPORT")},
            {"name": "OMBRE_PORT", "group": "system", "label": "服务端口", "sensitive": False, **_plain("OMBRE_PORT")},
            {"name": "OMBRE_LOG_FILE", "group": "system", "label": "日志文件路径", "sensitive": False, **_plain("OMBRE_LOG_FILE")},
            {"name": "OMBRE_CONFIG_PATH", "group": "system", "label": "配置文件路径", "sensitive": False, **_plain("OMBRE_CONFIG_PATH")},
            {"name": "OMBRE_MCP_REQUIRE_AUTH", "group": "auth", "label": "MCP 鉴权开关覆盖", "sensitive": False, **_plain("OMBRE_MCP_REQUIRE_AUTH")},
            {"name": "OMBRE_MCP_AUTH_MODE", "group": "auth", "label": "MCP 鉴权模式覆盖 (oauth/token/hybrid)", "sensitive": False, **_plain("OMBRE_MCP_AUTH_MODE")},
            {"name": "OMBRE_MCP_TOKEN", "group": "auth", "label": "MCP 静态 Token", "sensitive": True, **_masked("OMBRE_MCP_TOKEN")},
            {"name": "AI_NAME", "group": "identity", "label": "AI 显示名", "sensitive": False, **_plain("AI_NAME")},
            # 路径组
            {"name": "OMBRE_VAULT_DIR", "group": "paths", "label": "Vault 目录 (推荐)", "sensitive": False, **_plain("OMBRE_VAULT_DIR")},
            {"name": "OMBRE_BUCKETS_DIR", "group": "paths", "label": "桶目录 (旧版兼容)", "sensitive": False, **_plain("OMBRE_BUCKETS_DIR")},
            {"name": "OMBRE_HOST_VAULT_DIR", "group": "paths", "label": "宿主机 Vault 目录 (Docker)", "sensitive": False, **_plain("OMBRE_HOST_VAULT_DIR")},
            # Webhook 组
            {"name": "OMBRE_HOOK_URL", "group": "webhook", "label": "Webhook URL", "sensitive": False, **_plain("OMBRE_HOOK_URL")},
            {"name": "OMBRE_HOOK_SKIP", "group": "webhook", "label": "跳过 Webhook", "sensitive": False,
             "set": bool(os.environ.get("OMBRE_HOOK_SKIP", "").strip()),
             "value": os.environ.get("OMBRE_HOOK_SKIP", "").strip() or None},
            # 鉴权组
            {"name": "OMBRE_DASHBOARD_PASSWORD", "group": "auth", "label": "Dashboard 密码", "sensitive": True, **_masked("OMBRE_DASHBOARD_PASSWORD")},
        ]

        return JSONResponse({"vars": vars_data})


    @mcp.custom_route("/api/config", methods=["GET"])
    async def api_config_get(request: Request) -> Response:
        """Get current runtime config (safe fields only, API key masked)."""
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        try:
            desired = _desired_startup_state(read_config_yaml())
        except (OSError, ValueError) as exc:
            logger.error("读取持久化启动配置失败: %s", exc)
            return JSONResponse(
                {"error": f"failed to read persisted config: {exc}"},
                status_code=500,
            )
        dehy = sh.config.get("dehydration", {})
        emb = sh.config.get("embedding", {})
        runtime_network_security = _runtime_network_security(
            desired["mcp_require_auth"]
        )
        embedding_api_key = _secret_value(
            emb.get("api_key", ""), "OMBRE_EMBED_API_KEY"
        )
        current_payload = _current_public_config(sh.config)
        embedding_secret = cast(
            dict[str, object], current_payload.pop("embedding_secret")
        )
        dehydration_secret = cast(
            dict[str, object], current_payload.pop("dehydration_secret")
        )
        response_payload = {
            "dehydration": {
                "model": dehy.get("model", ""),
                "base_url": dehy.get("base_url", ""),
                "api_key_masked": dehydration_secret["api_key_masked"],
                "max_tokens": dehy.get("max_tokens", 1024),
                "temperature": dehy.get("temperature", 0.1),
                "api_format": dehy.get("api_format", "openai_compat"),
                "timeout_seconds": dehy.get("timeout_seconds", 60),
            },
            "embedding": {
                "enabled": _parse_bool(emb.get("enabled", False), default=False),
                "model": emb.get("model", ""),
                "base_url": emb.get("base_url", ""),
                "effective_base_url": emb.get("base_url") or dehy.get("base_url", ""),
                "api_format": emb.get("api_format", "openai_compat"),
                "timeout_seconds": emb.get("timeout_seconds", 30),
                "api_key_masked": embedding_secret["api_key_masked"],
                "has_own_api_key": bool(embedding_api_key),
                "backend": "api",
                "backend_options": [
                    {"value": "api", "label": "Gemini API（云端）", "note": "需填 OMBRE_EMBED_API_KEY，3072 维质量最高，需联网；客户端几乎不占额外内存"},
                ],
            },
            "surfacing": {
                "breath_max_results": int(sh.config.get("surfacing", {}).get("breath_max_results") or 20),
                "breath_max_tokens": int(sh.config.get("surfacing", {}).get("breath_max_tokens") or 10000),
                "feel_max_tokens": int(sh.config.get("surfacing", {}).get("feel_max_tokens") or 6000),
            },
            "merge_threshold": sh.config.get("merge_threshold", 75),
            "transport": desired["transport"],
            "transport_effective": runtime_transport,
            "buckets_dir": sh.config.get("buckets_dir", ""),
            # MCP 鉴权开关。默认 true；具体 OAuth/静态 Token 模式由 mcp_auth_mode 决定。
            # 渲染一键开关；关掉后 /mcp 免认证直连（供自有前端 / GPT / GLM 等）。
            "mcp_require_auth": desired["mcp_require_auth"],
            "mcp_require_auth_effective": runtime_mcp_auth_required,
            # 鉴权模式（仅 mcp_require_auth=true 时有意义）：OAuth、静态 Token 或两者共存。
            "mcp_auth_mode": desired["mcp_auth_mode"],
            "mcp_auth_mode_effective": runtime_mcp_auth_mode,
            "mcp_network_security": runtime_network_security,
            # 静态 Token 状态：只回掩码/是否已配置，绝不回明文。
            "mcp_token_configured": bool(_current_mcp_token()),
            "mcp_token_hint": _mask_mcp_token(_current_mcp_token()),
            # Dashboard 的公网 MCP 地址是 OAuth resource/audience 的启动期
            # 配置；同时回传已保存值与本进程实际值，避免假装热切换成功。
            "deployment": {
                "public_url": desired["public_url"],
                "public_url_effective": runtime_public_url,
            },
            "restart_required": (
                (
                    desired["mcp_require_auth"] != runtime_mcp_auth_required
                    and not runtime_network_security.get("guard_active")
                    and not runtime_network_security.get("auth_environment_override")
                )
                or desired["mcp_auth_mode"] != runtime_mcp_auth_mode
                or desired["transport"] != runtime_transport
                or desired["public_url"] != runtime_public_url
            ),
            # 部署信息：数据目录 + 端口 + 是否容器内。前端「系统」区展示，端口可改。
            "host_port": sh.config.get("host_port"),
            "in_docker": sh.in_docker(),
            # AI 一方的显示名（取自环境变量 AI_NAME，回退 "AI"）。前端只读，用于
            # 面向用户的文案（如删除确认、信件署名占位）。
            "ai_name": _get_ai_name(),
            # 记忆归属：多人共用一套 OB 时标明「这份记忆是谁的」。owner_count>=2 时
            # 前端顶部才显示归属徽标（单人不打扰）；owner_name 为徽标文字。均只读。
            "owner_name": _get_owner_name(),
            "owner_count": _get_owner_count(),
        }
        response_payload.update(current_payload)
        return JSONResponse(response_payload)


    @mcp.custom_route("/api/config", methods=["POST"])
    @_serialize_config_updates
    async def api_config_update(request: Request) -> Response:
        """Hot-update runtime sh.config. Optionally persist to config.yaml."""
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "JSON body must be an object"}, status_code=400)

        try:
            body, env_updates, persist_env_requested = _normalize_current_config_request(
                cast(dict[str, Any], body), sh.config
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        updated = []
        try:
            persist_requested = _parse_bool(body.get("persist", False))
            if _gateway_persistence_required(body) and not persist_requested:
                return JSONResponse(
                    {"error": "Gateway-owned settings require persist=true"},
                    status_code=400,
                )
            mcp_auth_value = (
                _parse_bool(body["mcp_require_auth"])
                if "mcp_require_auth" in body
                else None
            )
            mcp_auth_mode_value = None
            if "mcp_auth_mode" in body:
                mcp_auth_mode_value = str(body["mcp_auth_mode"]).strip().lower()
                if mcp_auth_mode_value not in ("oauth", "token", "hybrid"):
                    return JSONResponse(
                        {"error": "mcp_auth_mode must be 'oauth', 'token', or 'hybrid'"},
                        status_code=400,
                    )
            embedding_payload = body.get("embedding")
            if "embedding" in body and not isinstance(embedding_payload, dict):
                return JSONResponse(
                    {"error": "embedding must be an object"}, status_code=400
                )
            if "dehydration" in body and not isinstance(
                body.get("dehydration"), dict
            ):
                return JSONResponse(
                    {"error": "dehydration must be an object"}, status_code=400
                )
            if "surfacing" in body and not isinstance(body.get("surfacing"), dict):
                return JSONResponse(
                    {"error": "surfacing must be an object"}, status_code=400
                )
            dehydration_payload = dict(body.get("dehydration") or {})
            if "extra_body" in dehydration_payload and not isinstance(
                dehydration_payload["extra_body"], dict
            ):
                return JSONResponse(
                    {"error": "dehydration.extra_body must be an object"},
                    status_code=400,
                )
            if "max_tokens" in dehydration_payload:
                dehydration_payload["max_tokens"] = _bounded_config_int(
                    dehydration_payload["max_tokens"],
                    "dehydration.max_tokens",
                    128,
                    8192,
                )
            if "temperature" in dehydration_payload:
                dehydration_payload["temperature"] = _bounded_config_float(
                    dehydration_payload["temperature"],
                    "dehydration.temperature",
                    0.0,
                    2.0,
                )
            if "timeout_seconds" in dehydration_payload:
                dehydration_payload["timeout_seconds"] = _bounded_config_float(
                    dehydration_payload["timeout_seconds"],
                    "dehydration.timeout_seconds",
                    1.0,
                    600.0,
                )

            merge_threshold_value = (
                _bounded_config_int(
                    body["merge_threshold"], "merge_threshold", 0, 100
                )
                if "merge_threshold" in body
                else None
            )
            host_port_value = (
                _bounded_config_int(body["host_port"], "host_port", 1, 65535)
                if "host_port" in body
                else None
            )

            surfacing_values: dict[str, int] = {}
            surfacing_payload = body.get("surfacing") or {}
            for key, low, high in (
                ("breath_max_results", 1, 50),
                ("breath_max_tokens", 500, 20000),
                ("feel_max_tokens", 500, 20000),
            ):
                if key in surfacing_payload:
                    surfacing_values[key] = _bounded_config_int(
                        surfacing_payload[key], f"surfacing.{key}", low, high
                    )
            deployment_payload = body.get("deployment")
            if "deployment" in body and not isinstance(deployment_payload, dict):
                return JSONResponse(
                    {"error": "deployment must be an object"}, status_code=400
                )
            deployment_public_url = None
            if isinstance(deployment_payload, dict) and "public_url" in deployment_payload:
                raw_public_url = str(deployment_payload["public_url"] or "").strip()
                deployment_public_url = ""
                if raw_public_url:
                    deployment_public_url = normalize_public_https_origin(
                        raw_public_url
                    )
                    if not deployment_public_url:
                        return JSONResponse(
                            {
                                "error": (
                                    "deployment.public_url must be an HTTPS domain "
                                    "or complete /mcp URL"
                                )
                            },
                            status_code=400,
                        )
            embedding_enabled = (
                _parse_bool(embedding_payload["enabled"])
                if isinstance(embedding_payload, dict)
                and "enabled" in embedding_payload
                else None
            )
            embedding_backend = None
            if isinstance(embedding_payload, dict) and "backend" in embedding_payload:
                backend_raw = str(embedding_payload["backend"]).strip().lower()
                embedding_backend = (
                    "api" if backend_raw in ("api", "gemini") else backend_raw
                )
                if embedding_backend != "api":
                    return JSONResponse(
                        {"error": f"unsupported embedding backend: {backend_raw}"},
                        status_code=400,
                    )
            sampling_payload = None
            if isinstance(body.get("surfacing"), dict):
                candidate = body["surfacing"].get("sampling")
                if candidate is not None and not isinstance(candidate, dict):
                    return JSONResponse(
                        {"error": "surfacing.sampling must be an object"},
                        status_code=400,
                    )
                sampling_payload = candidate
            sampling_enabled = (
                _parse_bool(sampling_payload["enabled"])
                if isinstance(sampling_payload, dict)
                and "enabled" in sampling_payload
                else None
            )
            sampling_values: dict[str, int | float] = {}
            if isinstance(sampling_payload, dict):
                if "top_k" in sampling_payload:
                    sampling_values["top_k"] = _bounded_config_int(
                        sampling_payload["top_k"],
                        "surfacing.sampling.top_k",
                        1,
                        50,
                    )
                if "sample_k" in sampling_payload:
                    sampling_values["sample_k"] = _bounded_config_int(
                        sampling_payload["sample_k"],
                        "surfacing.sampling.sample_k",
                        1,
                        20,
                    )
                if "temperature" in sampling_payload:
                    sampling_values["temperature"] = _bounded_config_float(
                        sampling_payload["temperature"],
                        "surfacing.sampling.temperature",
                        0.1,
                        5.0,
                    )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

        startup_setting_requested = (
            deployment_public_url is not None
            or mcp_auth_value is not None
            or mcp_auth_mode_value is not None
        )
        if startup_setting_requested and not persist_requested:
            return JSONResponse(
                {
                    "error": (
                        "MCP startup settings require persist=true because "
                        "they only take effect after restart"
                    )
                },
                status_code=400,
            )
        hot_update_keys = {
            "dehydration",
            "embedding",
            "merge_threshold",
            "host_port",
            "surfacing",
        }
        if startup_setting_requested and hot_update_keys.intersection(body):
            return JSONResponse(
                {
                    "error": (
                        "MCP startup settings cannot be combined with hot runtime "
                        "settings; save them in separate requests"
                    )
                },
                status_code=400,
            )

        mcp_network_security: dict | None = None
        if mcp_auth_value is False:
            # 先于任何热配置变更执行，避免同一请求稍后因危险鉴权设置被拒绝时，
            # 其他字段却已经部分生效；原子写入锁内还会基于最新磁盘配置再检查一次。
            security_candidate = dict(sh.config)
            security_candidate["mcp_require_auth"] = False
            mcp_network_security = assess_mcp_network_safety(
                security_candidate,
                environment=os.environ,
                in_docker=sh.in_docker(),
            )
            security_issue = mcp_network_safety_issue(mcp_network_security)
            if security_issue:
                return JSONResponse({
                    "error": security_issue,
                    "mcp_network_security": mcp_network_security,
                }, status_code=400)

        if env_updates:
            persistence_issue = sh._env_persistence_issue()
            if persistence_issue:
                return JSONResponse({"error": persistence_issue}, status_code=409)

        persisted_before: dict[str, Any] | None = None
        if persist_requested:
            try:
                persisted_before = deepcopy(read_config_yaml())
            except Exception:
                return JSONResponse(
                    {"error": "persisted config could not be read"},
                    status_code=500,
                )

        runtime_paths = _runtime_config_paths(body)
        persisted_paths = _persisted_config_paths(body)
        runtime_snapshot = _snapshot_config_runtime()

        def _rollback_runtime(
            expected: Mapping[str, Any] | None = None,
        ) -> None:
            _restore_config_runtime(
                runtime_snapshot,
                expected if expected is not None else _snapshot_config_runtime(),
                runtime_paths,
            )

        # --- Dehydration config ---
        if "dehydration" in body:
            d = dehydration_payload
            dehy = sh.config.setdefault("dehydration", {})
            for key in ("model", "base_url", "max_tokens", "temperature", "api_format", "timeout_seconds", "extra_body"):
                if key in d:
                    dehy[key] = d[key]
                    updated.append(f"dehydration.{key}")
            if "api_key" in d and d["api_key"]:
                dehy["api_key"] = d["api_key"]
                updated.append("dehydration.api_key")
            # The dependency cascade can run without a standalone dehydrator.
            # When one exists, publish the same validated settings immediately.
            dehydrator = getattr(sh, "dehydrator", None)
            if dehydrator is not None:
                dehydrator.model = dehy.get("model", dehydrator.model)
                dehydrator.base_url = dehy.get("base_url", dehydrator.base_url)
                dehydrator.max_tokens = int(
                    dehy.get("max_tokens") or dehydrator.max_tokens
                )
                configured_temperature = dehy.get("temperature")
                if configured_temperature is not None:
                    dehydrator.temperature = float(configured_temperature)
                dehydrator.timeout_seconds = _positive_float(
                    dehy.get("timeout_seconds"), dehydrator.timeout_seconds
                )
                dehydrator.api_format = dehy.get(
                    "api_format",
                    getattr(dehydrator, "api_format", "openai_compat"),
                )
                dehydrator.extra_body = dict(dehy.get("extra_body") or {})
                if "api_key" in d and d["api_key"]:
                    dehydrator.api_key = dehy["api_key"]
                dehydrator.api_available = bool(dehydrator.api_key)
                if (
                    dehydrator.api_available
                    and dehydrator.api_format == "openai_compat"
                ):
                    from openai import AsyncOpenAI

                    try:
                        dehydrator.client = AsyncOpenAI(
                            api_key=dehydrator.api_key,
                            base_url=dehydrator.base_url,
                            timeout=dehydrator.timeout_seconds,
                        )
                    except Exception as exc:
                        _rollback_runtime()
                        logger.warning(
                            "dehydration reload failed: err_type=%s detail=hidden",
                            type(exc).__name__,
                        )
                        return JSONResponse(
                            {"error": "dehydration reload failed"},
                            status_code=400,
                        )
                else:
                    dehydrator.client = None

        # --- Embedding config ---
        if "embedding" in body:
            e = cast(dict[str, object], embedding_payload)
            emb = sh.config.setdefault("embedding", {})
            rebuild_embedding = False
            if embedding_enabled is not None:
                emb["enabled"] = embedding_enabled
                updated.append("embedding.enabled")
                rebuild_embedding = True
            if "model" in e:
                emb["model"] = e["model"]
                updated.append("embedding.model")
                rebuild_embedding = True
            if "base_url" in e:
                emb["base_url"] = str(e["base_url"]).strip()
                updated.append("embedding.base_url")
                rebuild_embedding = True
            if "timeout_seconds" in e:
                emb["timeout_seconds"] = e["timeout_seconds"]
                updated.append("embedding.timeout_seconds")
                rebuild_embedding = True
            if "api_format" in e:
                emb["api_format"] = str(e["api_format"]).strip()
                updated.append("embedding.api_format")
                rebuild_embedding = True
            if embedding_backend is not None:
                emb["backend"] = embedding_backend
                updated.append("embedding.backend")
                rebuild_embedding = True
            if "api_key" in e and e["api_key"]:
                emb["api_key"] = e["api_key"]
                updated.append("embedding.api_key")
                rebuild_embedding = True

            # 一个请求可能修改多个字段；只重建一次，再把同一实例发布给 Web 路由、
            # BucketManager、ImportEngine 和 MCP 工具运行时，避免读写使用不同模型。
            if rebuild_embedding:
                try:
                    _rebuild_embedding_runtime()
                except Exception as e:
                    _rollback_runtime()
                    logger.warning(
                        "embedding reload failed: err_type=%s detail=hidden",
                        type(e).__name__,
                    )
                    return JSONResponse(
                        {"error": "embedding reload failed"},
                        status_code=400,
                    )

        # --- Merge threshold ---
        if merge_threshold_value is not None:
            sh.config["merge_threshold"] = merge_threshold_value
            updated.append("merge_threshold")

        # MCP 鉴权开关、鉴权模式与公网地址都是启动期快照。它们只写入
        # config.yaml，不能提前发布到 sh.config；否则 OAuth/MCP 中间件仍使用
        # 旧闭包，而诊断与其他路由却会误以为新值已经生效。GET /api/config 会从
        # 持久配置回显 desired 值，并单独返回 effective 值。

        # --- 对外端口（host_port）---
        # 裸机：写 config 后进程自重启即监听新端口（前端「保存并重启」）。
        # Docker：容器内端口由 Dockerfile 固定，host_port 仅供部署脚本读取注入
        # OMBRE_HOST_PORT，须重建容器才生效（前端会提示）。
        if host_port_value is not None:
            sh.config["host_port"] = host_port_value
            updated.append("host_port")

        # --- Surfacing defaults (breath/feel token & result caps) ---
        if "surfacing" in body and isinstance(body["surfacing"], dict):
            sf = sh.config.setdefault("surfacing", {})
            for key, value in surfacing_values.items():
                sf[key] = value
                updated.append(f"surfacing.{key}")

        try:
            updated.extend(
                _apply_current_runtime_sections(body, pending_env=env_updates)
            )
        except Exception:
            # Provider constructors and custom runtime proxies may include a
            # credential in their exception text. Keep both HTTP and logs
            # generic; the transaction snapshot is sufficient for rollback.
            logger.warning("config runtime reload failed")
            _rollback_runtime()
            return JSONResponse(
                {"error": "runtime reload failed"},
                status_code=500,
            )
        runtime_after = _snapshot_config_runtime()
        persisted_after: dict | None = None

        # --- Persist to config.yaml if requested ---
        if persist_requested:
            def _mutate(save_config: dict) -> None:
                _persist_current_sections(save_config, body)
                if "dehydration" in body:
                    sc_dehy = save_config.setdefault("dehydration", {})
                    if not isinstance(sc_dehy, dict):
                        sc_dehy = {}
                        save_config["dehydration"] = sc_dehy
                    for key in ("model", "base_url", "max_tokens", "temperature", "api_format", "timeout_seconds", "extra_body"):
                        if key in dehydration_payload:
                            sc_dehy[key] = dehydration_payload[key]
                    # Never persist api_key to yaml (use env var)

                if "embedding" in body:
                    sc_emb = save_config.setdefault("embedding", {})
                    if not isinstance(sc_emb, dict):
                        sc_emb = {}
                        save_config["embedding"] = sc_emb
                    for key in ("model", "base_url", "api_format", "timeout_seconds"):
                        if key in body["embedding"]:
                            sc_emb[key] = body["embedding"][key]
                    if embedding_enabled is not None:
                        sc_emb["enabled"] = embedding_enabled
                    if embedding_backend is not None:
                        sc_emb["backend"] = embedding_backend

                if merge_threshold_value is not None:
                    save_config["merge_threshold"] = merge_threshold_value

                if mcp_auth_value is not None:
                    security_candidate = dict(save_config)
                    security_candidate.setdefault("transport", runtime_transport)
                    security_candidate["mcp_require_auth"] = mcp_auth_value
                    latest_security = assess_mcp_network_safety(
                        security_candidate,
                        environment=os.environ,
                        in_docker=sh.in_docker(),
                    )
                    security_issue = mcp_network_safety_issue(latest_security)
                    if security_issue:
                        raise ValueError(security_issue)
                    save_config["mcp_require_auth"] = mcp_auth_value

                if mcp_auth_mode_value is not None:
                    save_config["mcp_auth_mode"] = mcp_auth_mode_value

                if host_port_value is not None:
                    save_config["host_port"] = host_port_value

                if "surfacing" in body and isinstance(body["surfacing"], dict):
                    sc_sf = save_config.setdefault("surfacing", {})
                    if not isinstance(sc_sf, dict):
                        sc_sf = {}
                        save_config["surfacing"] = sc_sf
                    for key, value in surfacing_values.items():
                        sc_sf[key] = value
                    if "sampling" in body["surfacing"] and isinstance(body["surfacing"]["sampling"], dict):
                        sc_samp = sc_sf.setdefault("sampling", {})
                        if not isinstance(sc_samp, dict):
                            sc_samp = {}
                            sc_sf["sampling"] = sc_samp
                        if sampling_enabled is not None:
                            sc_samp["enabled"] = sampling_enabled
                        for key, value in sampling_values.items():
                            sc_samp[key] = value

                if deployment_public_url is not None:
                    sc_deployment = save_config.get("deployment")
                    if not isinstance(sc_deployment, dict):
                        sc_deployment = {}
                        save_config["deployment"] = sc_deployment
                    if deployment_public_url:
                        sc_deployment["public_url"] = deployment_public_url
                    else:
                        sc_deployment.pop("public_url", None)

            try:
                persisted_after = deepcopy(atomic_update_config_yaml(_mutate))
                updated.append("persisted_to_yaml")
                if mcp_auth_value is not None:
                    updated.append("mcp_require_auth")
                if mcp_auth_mode_value is not None:
                    updated.append("mcp_auth_mode")
                if deployment_public_url is not None:
                    updated.append("deployment.public_url")
            except ValueError as e:
                _rollback_runtime(runtime_after)
                return JSONResponse({"error": str(e), "updated": []}, status_code=400)
            except Exception as e:
                _rollback_runtime(runtime_after)
                logger.error(
                    "config persist failed: err_type=%s detail=hidden",
                    type(e).__name__,
                )
                return JSONResponse(
                    {"error": "persist failed", "updated": []},
                    status_code=500,
                )

        # Secrets are the final commit in this request. Do not publish them to
        # either the private .env file or this process until every fallible
        # runtime rebuild and requested YAML transaction above has succeeded.
        if env_updates:
            try:
                _atomic_update_env_vars(env_updates)
            except Exception:
                yaml_rollback_failed = False
                if persisted_before is not None and persisted_after is not None:
                    persisted_before_snapshot = persisted_before
                    persisted_after_snapshot = persisted_after

                    def _restore_yaml(save_config: dict[str, Any]) -> None:
                        _restore_mapping_paths(
                            save_config,
                            persisted_before_snapshot,
                            persisted_after_snapshot,
                            persisted_paths,
                        )

                    try:
                        atomic_update_config_yaml(_restore_yaml)
                    except Exception:
                        yaml_rollback_failed = True
                _rollback_runtime(runtime_after)
                return JSONResponse(
                    {
                        "error": (
                            "Secret persistence failed. Verify OMBRE_ENV_PATH "
                            "points to a writable mounted .env source."
                            + (
                                " Restoring config.yaml also failed; inspect "
                                "the deployment config before restarting."
                                if yaml_rollback_failed
                                else ""
                            )
                        )
                    },
                    status_code=500 if yaml_rollback_failed else 409,
                )
            for env_name, secret_value in env_updates.items():
                os.environ[env_name] = secret_value
            updated.append("persisted_to_env")

        desired = _desired_startup_state(
            persisted_after if persisted_after is not None else sh.config
        )
        runtime_network_security = _runtime_network_security(
            desired["mcp_require_auth"]
        )
        auth_environment_conflict = (
            runtime_network_security.get("auth_environment_override")
            and runtime_network_security.get("auth_environment_value")
            != desired["mcp_require_auth"]
        )
        gateway_live_payload, gateway_restart_fields = (
            _build_gateway_live_apply_plan(body, env_updates)
        )
        gateway_live_apply_applied = False
        gateway_live_apply_error = ""
        if gateway_live_payload:
            (
                gateway_live_apply_applied,
                gateway_live_apply_error,
            ) = await _post_gateway_live_config(gateway_live_payload)
        gateway_live_apply_attempted = bool(
            gateway_live_payload
            and gateway_live_apply_error != "not_configured"
        )
        gateway_live_apply_failed = bool(
            gateway_live_payload
            and not gateway_live_apply_applied
            and gateway_live_apply_error != "not_configured"
        )
        gateway_restart_required = bool(
            gateway_restart_fields
            or (gateway_live_payload and not gateway_live_apply_applied)
        )
        brain_restart_required = (
            (
                desired["mcp_require_auth"] != runtime_mcp_auth_required
                and not runtime_network_security.get("guard_active")
                and not runtime_network_security.get("auth_environment_override")
            )
            or desired["mcp_auth_mode"] != runtime_mcp_auth_mode
            or desired["transport"] != runtime_transport
            or desired["public_url"] != runtime_public_url
        )
        restart_required = gateway_restart_required or brain_restart_required
        return JSONResponse({
            "updated": updated,
            "ok": True,
            "restart_required": restart_required,
            "brain_restart_required": brain_restart_required,
            "gateway_restart_required": gateway_restart_required,
            "gateway_external_restart_required": gateway_restart_required,
            "gateway_restart_fields": gateway_restart_fields,
            "gateway_live_apply_attempted": gateway_live_apply_attempted,
            "gateway_live_apply_applied": gateway_live_apply_applied,
            "gateway_live_apply_failed": gateway_live_apply_failed,
            "gateway_live_apply_error": gateway_live_apply_error,
            "persist_env": persist_env_requested,
            "mcp_require_auth_effective": runtime_mcp_auth_required,
            "mcp_auth_mode_effective": runtime_mcp_auth_mode,
            "transport": desired["transport"],
            "transport_effective": runtime_transport,
            "mcp_require_auth": desired["mcp_require_auth"],
            "mcp_auth_mode": desired["mcp_auth_mode"],
            "mcp_network_security": runtime_network_security,
            "warnings": (
                (
                    [runtime_network_security["reason"]]
                    if runtime_network_security.get("override_active") else []
                )
                + (
                    [
                        "OMBRE_MCP_REQUIRE_AUTH 仍由平台环境变量控制；"
                        "请在部署平台修改或删除该变量后重建/重启服务。"
                    ]
                    if auth_environment_conflict else []
                )
            ),
            "deployment": {
                "public_url": desired["public_url"],
                "public_url_effective": runtime_public_url,
            },
            "message": (
                "设置已保存；当前配置或环境仍请求免鉴权，安全门禁继续强制鉴权。"
                if runtime_network_security.get("guard_active")
                else (
                    "设置已保存，但 OMBRE_MCP_REQUIRE_AUTH 仍由平台环境变量控制；"
                    "请在部署平台修改或删除该变量后重建/重启服务。"
                    if auth_environment_conflict
                    else (
                        "设置已保存，但外部 Gateway 在线同步失败；请重启 ombre-gateway 服务。"
                        if gateway_live_apply_failed
                        else (
                            "设置已保存；请重启外部 ombre-gateway 服务完成应用。"
                            if gateway_restart_required
                            else (
                                "MCP 启动配置已保存，需要重启 Ombre Brain 后生效。"
                                if brain_restart_required
                                else "设置已生效。"
                            )
                        )
                    )
                )
            ),
        })


    # =============================================================
    # /api/mcp-token/regenerate — 生成/轮换 token/hybrid 模式使用的静态密钥
    # 独立成一个小路由（而不是塞进 POST /api/config）：生成新密钥和改配置项
    # 是两件不同的事，参照 oauth.py 里 token 签发自成一块的做法。
    # =============================================================
    @mcp.custom_route("/api/mcp-token/regenerate", methods=["POST"])
    async def api_mcp_token_regenerate(request: Request) -> Response:
        """(Re)generate the static MCP token and persist it to config.yaml.

        Returns the plaintext token exactly once — GET /api/config only ever
        returns a masked hint, so the Dashboard must capture this response.
        Takes effect immediately when the running process is already in token
        or hybrid mode: _is_valid_static_mcp_token reads sh.config/env fresh on
        every request. A newly selected auth mode still requires a restart.
        """
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err

        new_token = secrets.token_urlsafe(32)

        # 原生锁覆盖“落盘 + 发布”整个提交段，且临界区内没有 await；既避免
        # 并发请求发生磁盘 B、运行态 A 的逆序，也不产生 asyncio 跨循环绑定。
        with mcp_token_commit_lock:
            try:
                atomic_update_config_yaml(
                    lambda save_config: save_config.__setitem__(
                        "mcp_token", new_token
                    )
                )
            except Exception as e:
                return JSONResponse(
                    {"error": f"persist failed: {e}"}, status_code=500
                )

            # 鉴权每次请求都直接读取 sh.config；必须先确认持久化成功，再发布运行态。
            # 否则磁盘写失败时接口虽然返回 500，新 token 却已经即时生效，重启后又
            # 回到旧 token，形成无法从响应判断的临时授权状态。
            sh.config["mcp_token"] = new_token

        env_override = bool(os.environ.get("OMBRE_MCP_TOKEN", "").strip())
        return JSONResponse({
            "ok": True,
            "token": new_token,
            "token_hint": _mask_mcp_token(new_token),
            "env_override": env_override,
            "message": (
                "环境变量 OMBRE_MCP_TOKEN 优先级更高，已生成的新密钥暂不会生效，"
                "请改用该环境变量或先取消设置它。"
                if env_override
                else "新 Token 已生成并保存，请立即复制；刷新页面后不再显示完整值。"
                     "当前进程已处于 Token/共存模式时立即生效；刚切换模式仍需重启。"
            ),
        })


    # =============================================================
    # /api/test/dehydration — 测试脱水 LLM API Key 是否可用
    # =============================================================
    @mcp.custom_route("/api/test/dehydration", methods=["POST"])
    async def api_test_dehydration(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        # Probe through the active Dehydrator so health checks use the exact
        # same OpenAI-compatible, Anthropic, or Gemini-native dispatch as real
        # dehydration work.  Hand-assembling a chat/completions request here
        # previously produced false failures for both native formats.
        dehydrator = getattr(sh, "dehydrator", None)
        if dehydrator is None or not str(
            getattr(dehydrator, "api_key", "") or ""
        ).strip():
            return JSONResponse({"ok": False, "error": "未设置 API Key"}, status_code=400)
        try:
            reply = await dehydrator._chat_once(
                "Connection health probe. Reply briefly.",
                "Reply with OK.",
                max_tokens=5,
                temperature=0.0,
            )
            if not str(reply or "").strip():
                return JSONResponse(
                    {"ok": False, "error": "Provider returned an empty response"},
                    status_code=502,
                )
            api_format = str(
                getattr(dehydrator, "api_format", "openai_compat")
                or "openai_compat"
            )
            return JSONResponse(
                {
                    "ok": True,
                    "message": f"API Key 有效 ✓（{api_format}）",
                    "api_format": api_format,
                }
            )
        except Exception as e:
            return JSONResponse(
                {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]},
                status_code=502,
            )


    # =============================================================
    # /api/test/embedding — 测试向量化 Embedding 是否真的可用
    # 之前只有脱水(compress)能测，向量化无从验证 → 用户「压缩正常但向量化静默失败」
    # 时完全无感。这里实际发一次 embedding 请求，把成功/失败如实回给前端。(#2/#3)
    # =============================================================
    @mcp.custom_route("/api/test/embedding", methods=["POST"])
    async def api_test_embedding(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        eng = sh.embedding_engine  # 读全局（Fix: env-sh.config 保存后已正确重建）
        if not getattr(eng, "enabled", False) or getattr(eng, "_backend", None) is None:
            return JSONResponse({
                "ok": False,
                "error": "向量化未启用或缺 key（standby）。请填入 Embedding API Key 点「保存」后再测。",
            })
        try:
            vec = await eng._generate_async("connectivity probe / 连接性探针")
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"[:300]})
        if vec:
            model = getattr(eng, "model", "") or (
                eng._backend.model_name() if getattr(eng, "_backend", None) else "?"
            )
            return JSONResponse({
                "ok": True,
                "message": f"向量化连接成功 ✓（模型 {model}，维度 {len(vec)}）",
            })
        return JSONResponse({
            "ok": False,
            "error": "调用返回空向量：检查 model 名 / base_url / key 是否匹配该 provider"
                     "（如硅基流动 base_url=https://api.siliconflow.cn/v1、model=BAAI/bge-m3）。详见错误面板 OB-E001。",
        })


    # =============================================================
    # /api/models — 获取 LLM provider 可用模型列表（供 Dashboard 模型选择器使用）
    # POST Body: {api_key, base_url, api_format}
    # 支持 openai_compat / gemini / anthropic 三种格式
    # =============================================================
    @mcp.custom_route("/api/models", methods=["POST"])
    async def api_list_models(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await sh._read_json_object(request)
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

        provider_fields = ("api_key", "base_url", "api_format")
        if any(key in body and not isinstance(body[key], str) for key in provider_fields):
            return JSONResponse({"ok": False, "error": "provider fields must be strings"}, status_code=400)
        api_key = str(body.get("api_key", "")).strip()
        base_url = str(body.get("base_url", "")).strip()
        api_format = str(body.get("api_format", "openai_compat")).strip().lower()
        if (
            len(api_key) > _MAX_PROVIDER_KEY_CHARS
            or len(base_url) > _MAX_PROVIDER_URL_CHARS
            or len(api_format) > _MAX_PROVIDER_FORMAT_CHARS
        ):
            return JSONResponse({"ok": False, "error": "provider configuration is too large"}, status_code=400)

        # Sentinel "__use_current__": use server-side key from dehydration config
        if api_key == "__use_current__":
            api_key = sh.config.get("dehydration", {}).get("api_key", "")
            if not base_url:
                base_url = sh.config.get("dehydration", {}).get("base_url", "")
            if not api_format or api_format == "openai_compat":
                api_format = sh.config.get("dehydration", {}).get("api_format", "openai_compat")
        # Sentinel "__use_current_embed__": use server-side key from embedding config
        if api_key == "__use_current_embed__":
            api_key = sh.config.get("embedding", {}).get("api_key", "")
            if not base_url:
                base_url = sh.config.get("embedding", {}).get("base_url", "")

        if not api_key:
            return JSONResponse({"ok": False, "error": "需要 api_key（请先保存 API Key 或在输入框填入）"}, status_code=400)

        try:
            models: list[str] = []
            if api_format in ("gemini", "gemini_embed"):
                # gemini → generateContent models；gemini_embed → embedContent models
                method_filter = "embedContent" if api_format == "gemini_embed" else "generateContent"
                url = "https://generativelanguage.googleapis.com/v1beta/models"
                async with httpx.AsyncClient(timeout=10.0) as c:
                    r = await c.get(
                        url,
                        params={"pageSize": 200},
                        headers={"x-goog-api-key": api_key},
                    )
                r.raise_for_status()
                for m in r.json().get("models", []):
                    if method_filter in m.get("supportedGenerationMethods", []):
                        models.append(m.get("name", "").replace("models/", ""))
            elif api_format == "anthropic":
                ant_base = base_url.rstrip("/") if base_url else "https://api.anthropic.com"
                headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
                async with httpx.AsyncClient(timeout=10.0) as c:
                    r = await c.get(f"{ant_base}/v1/models", headers=headers)
                r.raise_for_status()
                models = [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
            else:  # openai_compat
                if not base_url:
                    return JSONResponse({"ok": False, "error": "openai_compat 格式需要 base_url"}, status_code=400)
                headers_oai = {"Authorization": f"Bearer {api_key}"}
                async with httpx.AsyncClient(timeout=10.0) as c:
                    r = await c.get(f"{base_url.rstrip('/')}/models", headers=headers_oai)
                r.raise_for_status()
                models = sorted(m.get("id", "") for m in r.json().get("data", []) if m.get("id"))
            return JSONResponse({"ok": True, "models": [m for m in models if m]})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)[:300]})


    # =============================================================
    # /api/env-config — Dashboard 热更新环境变量（四块：Compress / Embed / Password / Webhook）
    # GET  返回当前值（API key 脱敏）
    # POST 批量更新：同时更新进程内 config + 写 .env 文件持久化
    # =============================================================

    # 哪些变量可以从 Dashboard 读写（不能出现在这里之外的变量）
    _ENV_CONFIG_FIELDS: dict[str, dict] = {
        # Compress / 脱水压缩
        "OMBRE_COMPRESS_API_KEY":  {"group": "compress", "sensitive": True,  "in_memory": ("dehydration", "api_key")},
        "OMBRE_COMPRESS_BASE_URL": {"group": "compress", "sensitive": False, "in_memory": ("dehydration", "base_url")},
        "OMBRE_COMPRESS_MODEL":    {"group": "compress", "sensitive": False, "in_memory": ("dehydration", "model")},
        "OMBRE_COMPRESS_FORMAT":   {"group": "compress", "sensitive": False, "in_memory": ("dehydration", "api_format")},
        "OMBRE_COMPRESS_TIMEOUT_SECONDS": {"group": "compress", "sensitive": False, "in_memory": ("dehydration", "timeout_seconds")},
        # Embed / 向量化（backend 切换走 /api/embedding/migrate）
        "OMBRE_EMBED_API_KEY":     {"group": "embed",    "sensitive": True,  "in_memory": ("embedding", "api_key")},
        "OMBRE_EMBED_BASE_URL":    {"group": "embed",    "sensitive": False, "in_memory": ("embedding", "base_url")},
        "OMBRE_EMBED_MODEL":       {"group": "embed",    "sensitive": False, "in_memory": ("embedding", "model")},
        "OMBRE_EMBED_FORMAT":      {"group": "embed",    "sensitive": False, "in_memory": ("embedding", "api_format")},
        "OMBRE_EMBED_TIMEOUT_SECONDS": {"group": "embed", "sensitive": False, "in_memory": ("embedding", "timeout_seconds")},
        # Webhook
        "OMBRE_HOOK_URL":          {"group": "webhook",  "sensitive": False, "in_memory": None},
        "OMBRE_HOOK_SKIP":         {"group": "webhook",  "sensitive": False, "in_memory": None},
        # Identity / display labels
        "AI_NAME":                 {"group": "identity", "sensitive": False, "in_memory": None},
    }

    _ENV_CONFIG_NOTE = {
        "compress": "改完即时生效，并原子写入受管 .env；不会把 API key 写入 config.yaml。",
        "embed": "provider tuple 立即重建并原子写入受管 .env；backend 切换请使用 embedding 迁移工具。",
        "webhook": "改完下次 breath/dream 触发时生效，并写入受管 .env。",
        "identity": "AI 显示名立即生效并写入受管 .env；未启用 override 时平台环境变量优先。",
    }


    def _mask(val: str) -> str:
        """对 API key 做脱敏，末 4 位保留供校验。"""
        if not val:
            return ""
        if len(val) > 8:
            return f"{val[:4]}...{val[-4:]}"
        return "***"


    @mcp.custom_route("/api/env-config", methods=["GET"])
    async def api_env_config_get(request: Request) -> Response:
        """
        返回四块配置的当前值（API key 脱敏显示）。
        优先读进程内 sh.config / os.environ，其次读 .env 文件。
        """
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err

        result: dict[str, dict] = {}
        for var, meta in _ENV_CONFIG_FIELDS.items():
            # 优先从 config dict 读（进程内最新）
            raw = ""
            if meta["in_memory"]:
                section, key = meta["in_memory"]
                raw = str(sh.config.get(section, {}).get(key, "")).strip()
            # 进程内为空，则读 os.environ
            if not raw:
                raw = os.environ.get(var, "").strip()
            # 再读 .env 文件
            if not raw:
                raw = sh._read_env_var(var)
            result[var] = {
                "group": meta["group"],
                "sensitive": meta["sensitive"],
                "value": _mask(raw) if meta["sensitive"] else raw,
                "is_set": bool(raw),
            }

        return JSONResponse({
            "ok": True,
            "fields": result,
            "notes": _ENV_CONFIG_NOTE,
        })


    @mcp.custom_route("/api/env-config", methods=["POST"])
    @_serialize_config_updates
    async def api_env_config_set(request: Request) -> Response:
        """
        热更新指定环境变量。

        Body (JSON): {"updates": {"OMBRE_COMPRESS_API_KEY": "sk-...", ...}}
        - 只写传入的字段，未传字段不动。
        - 空字符串 = 清除该变量（.env 里写成 NAME= ，进程内 sh.config 设为 ""）。
        - API key 不支持 "***" 保持不变（应传实际值或空字符串）。

        返回字段：
        - updated / persisted：同一原子事务中已应用并写入受管 .env 的变量名；
        - partial / warnings：请求里有字段未通过白名单或值校验；
        - 任一运行时重建或落盘失败都会回滚本次运行时更新。
        """
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err

        try:
            body = await sh._read_json_object(request)
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

        updates: dict = body.get("updates", {})
        if not isinstance(updates, dict) or not updates:
            return JSONResponse({"ok": False, "error": "updates 必须是非空对象"}, status_code=400)
        if len(updates) > len(_ENV_CONFIG_FIELDS):
            return JSONResponse({"ok": False, "error": "updates 字段过多"}, status_code=400)

        accepted: dict[str, str] = {}
        warnings: list[str] = []

        for var, val in updates.items():
            if var not in _ENV_CONFIG_FIELDS:
                warnings.append(f"{var}: 不在白名单里，未应用")
                continue
            if not isinstance(val, str):
                warnings.append(f"{var}: 值必须是字符串，未应用")
                continue
            if len(val) > _MAX_ENV_VALUE_CHARS:
                warnings.append(f"{var}: 值超过 {_MAX_ENV_VALUE_CHARS} 字符，未应用")
                continue
            # 拒绝会破坏单行 dotenv/restart round-trip 的字符。
            if "\n" in val or "\r" in val:
                warnings.append(f"{var}: 值不能含换行，未应用")
                continue
            if "\0" in val:
                warnings.append(f"{var}: 值不能含 NUL，未应用")
                continue

            value = val.strip()

            # OMBRE_HOOK_URL 只允许 http/https（防止意外配成 file:// 等非 HTTP scheme）
            if var == "OMBRE_HOOK_URL" and value and not value.startswith(("http://", "https://")):
                warnings.append(f"{var}: 只允许 http:// 或 https:// 开头的 URL，未应用")
                continue

            accepted[var] = value

        if not accepted:
            return JSONResponse(
                {
                    "ok": False,
                    "partial": False,
                    "updated": [],
                    "persisted": [],
                    "warnings": warnings,
                    "error": warnings[0] if warnings else "没有字段成功更新",
                },
                status_code=400,
            )

        persistence_issue = sh._env_persistence_issue()
        if persistence_issue:
            return JSONResponse(
                {
                    "ok": False,
                    "partial": False,
                    "updated": [],
                    "persisted": [],
                    "error": persistence_issue,
                },
                status_code=409,
            )

        runtime_paths = _env_runtime_config_paths(accepted, _ENV_CONFIG_FIELDS)
        runtime_snapshot = _snapshot_config_runtime()
        previous_env = {var: os.environ.get(var) for var in accepted}

        def _restore_env_runtime(
            expected_runtime: Mapping[str, Any],
            expected_env: Mapping[str, str | None],
        ) -> None:
            _restore_config_runtime(
                runtime_snapshot,
                expected_runtime,
                runtime_paths,
            )
            for var, previous in previous_env.items():
                if os.environ.get(var) != expected_env.get(var):
                    continue
                if previous is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = previous

        # Compress is staged as one provider tuple. The client may be expensive
        # or fail to construct, so do not publish any attribute until every
        # requested value has validated successfully.
        compress_vars = [
            var for var in accepted
            if _ENV_CONFIG_FIELDS[var]["group"] == "compress"
        ]
        staged_dehydrator: object | None = None
        staged_dehydrator_attrs: dict[str, Any] | None = None
        if compress_vars:
            current_dehy = sh.dehydrator
            current_cfg = sh.config.get("dehydration", {})
            staged_cfg = dict(current_cfg) if isinstance(current_cfg, dict) else {}
            for var in compress_vars:
                _section, key = _ENV_CONFIG_FIELDS[var]["in_memory"]
                staged_cfg[key] = accepted[var]

            try:
                if current_dehy is None:
                    raise RuntimeError("dehydrator runtime unavailable")
                staged_api_key = staged_cfg.get(
                    "api_key", getattr(current_dehy, "api_key", "")
                )
                staged_base_url = staged_cfg.get(
                    "base_url", getattr(current_dehy, "base_url", "")
                )
                staged_model = staged_cfg.get(
                    "model", getattr(current_dehy, "model", "")
                )
                staged_timeout = _positive_float(
                    staged_cfg.get("timeout_seconds"),
                    getattr(current_dehy, "timeout_seconds", 60.0),
                )
                staged_format = staged_cfg.get(
                    "api_format", getattr(current_dehy, "api_format", "openai_compat")
                )
                staged_available = bool(staged_api_key)
                staged_client = None
                if staged_available and staged_format == "openai_compat":
                    from openai import AsyncOpenAI as _OAI_DH

                    staged_client = _OAI_DH(
                        api_key=staged_api_key,
                        base_url=staged_base_url,
                        timeout=staged_timeout,
                    )

                staged_dehydrator_attrs = {
                    "api_key": staged_api_key,
                    "base_url": staged_base_url,
                    "model": staged_model,
                    "timeout_seconds": staged_timeout,
                    "api_format": staged_format,
                    "api_available": staged_available,
                    "client": staged_client,
                }
                staged_dehydrator = current_dehy
            except Exception:
                return JSONResponse(
                    {
                        "ok": False,
                        "partial": False,
                        "updated": [],
                        "persisted": [],
                        "error": "provider configuration could not be applied",
                    },
                    status_code=400,
                )

        try:
            for var, value in accepted.items():
                meta = _ENV_CONFIG_FIELDS[var]
                if meta["in_memory"]:
                    section, key = meta["in_memory"]
                    section_cfg = sh.config.get(section)
                    if not isinstance(section_cfg, dict):
                        section_cfg = {}
                        sh.config[section] = section_cfg
                    section_cfg[key] = value
                if value:
                    os.environ[var] = value
                else:
                    os.environ.pop(var, None)

            if staged_dehydrator is not None and staged_dehydrator_attrs is not None:
                for name, value in staged_dehydrator_attrs.items():
                    setattr(staged_dehydrator, name, value)

            embed_vars = [
                var
                for var in accepted
                if _ENV_CONFIG_FIELDS[var]["group"] == "embed"
            ]
            if embed_vars:
                if (
                    "OMBRE_EMBED_API_KEY" in embed_vars
                    and not accepted["OMBRE_EMBED_API_KEY"]
                ):
                    sh.embedding_engine._backend = None  # type: ignore[attr-defined]
                    sh.embedding_engine.enabled = False
                    sh.replace_embedding_engine(sh.embedding_engine)
                else:
                    _rebuild_embedding_runtime()
        except Exception:
            _restore_env_runtime(
                _snapshot_config_runtime(),
                {var: os.environ.get(var) for var in accepted},
            )
            return JSONResponse(
                {
                    "ok": False,
                    "partial": False,
                    "updated": [],
                    "persisted": [],
                    "error": "provider runtime reload failed",
                },
                status_code=400,
            )

        runtime_after = _snapshot_config_runtime()
        staged_env = {var: os.environ.get(var) for var in accepted}

        # All env-config fields, including provider secrets, share the same
        # durable env transaction. They are never copied into config.yaml.
        try:
            _atomic_update_env_vars(accepted)
        except Exception:
            _restore_env_runtime(runtime_after, staged_env)
            return JSONResponse(
                {
                    "ok": False,
                    "partial": False,
                    "updated": [],
                    "persisted": [],
                    "error": "environment persistence failed",
                },
                status_code=409,
            )

        written = list(accepted)
        partial = bool(warnings) or len(written) != len(updates)
        response: dict = {
            "ok": True,
            "partial": partial,
            "updated": written,
            "persisted": written,
            "env_file": sh._project_env_path(),
            "note": (
                "有效字段已更新并持久化；无效字段见 warnings。"
                if partial
                else "当前进程运行时与持久化环境均已更新。"
            ),
        }
        if warnings:
            response["warnings"] = warnings
        return JSONResponse(response)


    # --- 传输模式热切换：streamable-http / stdio / sse（legacy）---
    # transport 是「启动时绑定」的（server.py 据此起 streamable_http_app / sse_app / stdio），
    # 运行中无法无缝切换，所以这里的做法是：持久化新值 → 原地自重启（os.execv 继承已改的
    # os.environ，绕过 compose 里硬编码的旧 OMBRE_TRANSPORT）→ 新进程按新 transport 起。
    _TRANSPORT_CHOICES = ("streamable-http", "sse", "stdio")

    @mcp.custom_route("/api/transport", methods=["POST"])
    @_serialize_config_updates
    async def api_transport_set(request: Request) -> Response:
        """切换 MCP 传输模式并自重启生效。

        Body (JSON): {"transport": "streamable-http" | "sse" | "stdio"}

        ⚠️ stdio 没有 HTTP 服务：切到 stdio 后 Dashboard / REST / /mcp(HTTP) 全部消失，
        且无法再从网页切回（需在服务器改 config.yaml / env 恢复）。前端对此二次确认。
        """
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await sh._read_json_object(request)
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

        new_t = str(body.get("transport") or "").strip()
        if new_t not in _TRANSPORT_CHOICES:
            return JSONResponse(
                {"ok": False, "error": f"transport 必须是 {list(_TRANSPORT_CHOICES)} 之一"},
                status_code=400,
            )

        current = str(sh.config.get("transport", "stdio"))
        if new_t == current:
            return JSONResponse({"ok": True, "transport": new_t, "restarting": False,
                                 "note": "传输模式未变化，无需重启。"})

        # Commit both durable sources before publishing the new runtime value.
        # The managed env file has startup precedence in supported deployments,
        # while config.yaml remains authoritative for native/no-env launches.
        # They therefore form one logical transaction: an env write failure
        # restores the exact prior transport key in YAML and never schedules a
        # restart with split desired state.
        previous_yaml_transport: list[tuple[bool, object]] = []

        def _persist_transport(saved: dict) -> None:
            previous_yaml_transport.append(
                ("transport" in saved, deepcopy(saved.get("transport")))
            )
            saved["transport"] = new_t

        try:
            atomic_update_config_yaml(_persist_transport)
        except Exception:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "config.yaml persistence failed",
                    "restarting": False,
                    "env_persisted": False,
                    "rollback_failed": False,
                },
                status_code=500,
            )

        try:
            sh._write_env_var("OMBRE_TRANSPORT", new_t)
        except Exception:
            rollback_failed = False
            try:
                existed, previous_value = previous_yaml_transport[0]

                def _restore_transport(saved: dict) -> None:
                    if existed:
                        saved["transport"] = deepcopy(previous_value)
                    else:
                        saved.pop("transport", None)

                atomic_update_config_yaml(_restore_transport)
            except Exception:
                rollback_failed = True
            return JSONResponse(
                {
                    "ok": False,
                    "error": (
                        "environment persistence failed and config.yaml rollback failed"
                        if rollback_failed
                        else "environment persistence failed; config.yaml was restored"
                    ),
                    "restarting": False,
                    "env_persisted": False,
                    "rollback_failed": rollback_failed,
                },
                status_code=500 if rollback_failed else 409,
            )

        # Only a fully durable transaction is published into this process.
        # os.execv inherits the new env value and starts the requested server
        # transport after the response has reached the Dashboard.
        sh.config["transport"] = new_t
        os.environ["OMBRE_TRANSPORT"] = new_t

        # Delay restart so this response can reach the client first.
        import threading

        threading.Timer(1.0, sh.restart_current_process).start()
        logger.info(f"[transport] 切换 {current} → {new_t}，1s 后自重启生效")
        return JSONResponse({
            "ok": True,
            "transport": new_t,
            "previous": current,
            "restarting": True,
            "env_persisted": True,
            "loses_http": new_t == "stdio",
        })
