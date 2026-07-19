"""Redacted effective-configuration diagnostics for the dashboard."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import yaml


_FIELDS = (
    ("transport", ("OMBRE_TRANSPORT",), False),
    ("buckets_dir", ("OMBRE_BUCKETS_DIR",), False),
    ("state_dir", ("OMBRE_STATE_DIR",), False),
    ("dehydration.model", ("OMBRE_DEHYDRATION_MODEL", "OMBRE_MODEL"), False),
    ("dehydration.base_url", ("OMBRE_DEHYDRATION_BASE_URL", "OMBRE_BASE_URL"), False),
    ("dehydration.api_key", ("OMBRE_API_KEY",), True),
    ("embedding.enabled", ("OMBRE_EMBEDDING_ENABLED",), False),
    ("embedding.model", ("OMBRE_EMBED_MODEL", "OMBRE_EMBEDDING_MODEL"), False),
    (
        "embedding.base_url",
        ("OMBRE_EMBED_BASE_URL", "OMBRE_EMBEDDING_BASE_URL"),
        False,
    ),
    (
        "embedding.api_key",
        ("OMBRE_EMBED_API_KEY", "OMBRE_EMBEDDING_API_KEY"),
        True,
    ),
    ("reranker.enabled", ("OMBRE_RERANKER_ENABLED",), False),
    ("reranker.model", ("OMBRE_RERANKER_MODEL",), False),
    ("reranker.base_url", ("OMBRE_RERANKER_BASE_URL",), False),
    ("reranker.api_key", ("OMBRE_RERANKER_API_KEY",), True),
    ("gateway.host", ("OMBRE_GATEWAY_HOST",), False),
    ("gateway.port", ("OMBRE_GATEWAY_PORT",), False),
    ("gateway.upstream_default_model", ("OMBRE_GATEWAY_UPSTREAM_MODEL",), False),
    ("persona.model", ("OMBRE_PERSONA_MODEL",), False),
    ("persona.base_url", ("OMBRE_PERSONA_BASE_URL",), False),
    ("persona.api_key", ("OMBRE_PERSONA_API_KEY",), True),
    ("reflection.model", ("OMBRE_REFLECTION_MODEL",), False),
    ("reflection.base_url", ("OMBRE_REFLECTION_BASE_URL",), False),
    ("reflection.api_key", ("OMBRE_REFLECTION_API_KEY",), True),
    ("dream.enabled", ("OMBRE_DREAM_ENABLED",), False),
    ("dream.model", ("OMBRE_DREAM_MODEL",), False),
    ("dream.base_url", ("OMBRE_DREAM_BASE_URL",), False),
    ("dream.api_key", ("OMBRE_DREAM_API_KEY",), True),
)


def _read_yaml(path: str) -> dict:
    if not path or not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as source:
        payload = yaml.safe_load(source) or {}
    return payload if isinstance(payload, dict) else {}


def _get_path(payload: Mapping[str, Any], dotted: str) -> tuple[bool, Any]:
    current: Any = payload
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def effective_config_report(
    effective: dict,
    *,
    config_path: str,
    runtime_config_path: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    alias_provenance: Mapping[str, str] = {}
    if env is os.environ:
        from utils import ENV_ALIAS_PROVENANCE

        alias_provenance = ENV_ALIAS_PROVENANCE
    persisted = _read_yaml(config_path)
    runtime = _read_yaml(runtime_config_path)
    entries = []
    for dotted, env_names, sensitive in _FIELDS:
        env_source = next(
            (
                name
                for name in env_names
                if str(env.get(name) or "").strip()
                and not (
                    alias_provenance.get(name)
                    and str(env.get(name) or "").strip()
                    == str(env.get(alias_provenance[name]) or "").strip()
                )
            ),
            "",
        )
        in_runtime, _ = _get_path(runtime, dotted)
        in_persisted, _ = _get_path(persisted, dotted)
        exists, value = _get_path(effective, dotted)
        if env_source:
            source = f"env:{env_source}"
        elif in_runtime:
            source = "runtime_yaml"
        elif in_persisted:
            source = "config_yaml"
        else:
            source = "default"
        entry = {"path": dotted, "source": source, "sensitive": sensitive}
        if sensitive:
            entry["set"] = bool(value) if exists else False
        else:
            entry["value"] = value if exists else None
        entries.append(entry)

    upstreams = []
    for upstream in effective.get("gateway", {}).get("upstreams", []) or []:
        if not isinstance(upstream, dict):
            continue
        key_env = str(upstream.get("api_key_env") or "").strip()
        raw_key_envs = upstream.get("api_key_envs", key_env)
        if isinstance(raw_key_envs, str):
            key_envs = [item.strip() for item in raw_key_envs.split(",") if item.strip()]
        elif isinstance(raw_key_envs, list):
            key_envs = [
                str(item or "").strip()
                for item in raw_key_envs
                if str(item or "").strip()
            ]
        else:
            key_envs = []
        raw_direct_keys = upstream.get("api_keys", [])
        direct_key_count = 0
        if str(upstream.get("api_key") or "").strip():
            direct_key_count += 1
        if isinstance(raw_direct_keys, str):
            direct_key_count += len(
                [item for item in raw_direct_keys.split(",") if item.strip()]
            )
        elif isinstance(raw_direct_keys, list):
            direct_key_count += len(
                [
                    item
                    for item in raw_direct_keys
                    if (
                        isinstance(item, dict)
                        and str(item.get("api_key") or item.get("key") or "").strip()
                    )
                    or (not isinstance(item, dict) and str(item or "").strip())
                ]
            )
        env_names = list(dict.fromkeys(key_envs))
        env_key_count = sum(
            bool(str(env.get(env_name) or "").strip()) for env_name in env_names
        )
        key_count = env_key_count + direct_key_count
        upstreams.append(
            {
                "name": str(upstream.get("name") or ""),
                "base_url": str(upstream.get("base_url") or ""),
                "default_model": str(upstream.get("default_model") or ""),
                "models": list(upstream.get("models") or []),
                "api_key_env": key_env,
                "api_key_envs": key_envs,
                "api_key_set": key_count > 0,
                "has_direct_api_key": direct_key_count > 0,
                "direct_api_key_count": direct_key_count,
                "key_count": key_count,
            }
        )

    return {
        "config_file": {"path": str(Path(config_path)), "exists": os.path.isfile(config_path)},
        "runtime_config_file": {
            "path": str(Path(runtime_config_path)),
            "exists": os.path.isfile(runtime_config_path),
        },
        "entries": entries,
        "gateway_upstreams": upstreams,
    }
