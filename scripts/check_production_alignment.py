#!/usr/bin/env python3
"""Check local runtime config against the production Ombre + relay contract.

This script is intentionally read-only. It never prints secret values.
Run it after changing config.yaml, .env, VPS config, or Cloudflare Worker secrets.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - operator-facing failure
    raise SystemExit("PyYAML is required: pip install pyyaml") from exc


EXPECTED = {
    "gateway_base": "https://gateway.btombre.men",
    "relay_base": "https://nuojiji-relay.amydong.workers.dev",
    "final_model": "claude-opus-5",
    "native_final_model": "claude-opus-5-native",
    "auxiliary_model": "gemini-3.6-flash",
    "embedding_model": "gemini-embedding-2-preview",
    "reranker_model": "Qwen/Qwen3-Reranker-8B",
    "required_models": [
        "claude-opus-4-6",
        "claude-opus-4-8",
        "claude-opus-4-8-native",
        "claude-opus-5",
        "claude-opus-5-native",
        "claude-fable-5",
        "gemini-3.5-flash",
        "gemini-3.6-flash",
    ],
    "proxy_claude_models": [
        "claude-opus-4-6",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-fable-5",
    ],
}


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def read_json(url: str, *, token: str = "", method: str = "GET", body: dict[str, Any] | None = None) -> tuple[int, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 OmbreProductionAlignment/1.0",
    }
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {"raw": raw[:500]}
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw[:500]}
        return exc.code, parsed


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, label: str) -> None:
        print(f"OK   {label}")

    def warn(self, label: str) -> None:
        self.warnings.append(label)
        print(f"WARN {label}")

    def fail(self, label: str) -> None:
        self.failures.append(label)
        print(f"FAIL {label}")

    def assert_true(self, condition: bool, label: str) -> None:
        if condition:
            self.ok(label)
        else:
            self.fail(label)


def config_gateway_checks(check: Check, config_path: Path) -> None:
    if not config_path.exists():
        check.fail(f"local config missing: {config_path}")
        return
    loaded_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cfg = loaded_config if isinstance(loaded_config, dict) else {}
    for section in ("dehydration", "dream", "persona"):
        value = cfg.get(section)
        section_cfg = value if isinstance(value, dict) else {}
        check.assert_true(
            section_cfg.get("model") == EXPECTED["auxiliary_model"],
            f"local {section}.model is gemini-3.6-flash",
        )

    portrait_value = cfg.get("portrait")
    portrait = portrait_value if isinstance(portrait_value, dict) else {}
    check.assert_true(
        str(portrait.get("model") or "").strip() in {"", EXPECTED["auxiliary_model"]},
        "local portrait.model is Gemini 3.6 or inherits dehydration",
    )
    reflection_value = cfg.get("reflection")
    reflection = reflection_value if isinstance(reflection_value, dict) else {}
    check.assert_true(
        str(reflection.get("model") or "").strip() in {"", EXPECTED["auxiliary_model"]},
        "local reflection.model is Gemini 3.6 or inherits persona/dehydration",
    )
    for key in ("daily_chat_memory_summary_model", "daily_chat_memory_candidate_model"):
        check.assert_true(
            str(reflection.get(key) or "").strip() in {"", EXPECTED["auxiliary_model"]},
            f"local reflection.{key} is Gemini 3.6 or inherits dehydration",
        )

    embedding_value = cfg.get("embedding")
    embedding = embedding_value if isinstance(embedding_value, dict) else {}
    check.assert_true(
        embedding.get("model") == EXPECTED["embedding_model"],
        "local embedding keeps its specialized model",
    )
    reranker_value = cfg.get("reranker")
    reranker = reranker_value if isinstance(reranker_value, dict) else {}
    check.assert_true(
        reranker.get("model") == EXPECTED["reranker_model"],
        "local reranker keeps its specialized model",
    )

    gateway_value = cfg.get("gateway")
    gateway = gateway_value if isinstance(gateway_value, dict) else {}
    check.assert_true(
        gateway.get("upstream_default_model") == EXPECTED["final_model"],
        "local gateway upstream_default_model is claude-opus-5",
    )

    upstreams_value = gateway.get("upstreams")
    upstreams = upstreams_value if isinstance(upstreams_value, list) else []
    by_name = {str(item.get("name")): item for item in upstreams if isinstance(item, dict)}
    anthropic = by_name.get("anthropic", {})
    anthropic_native = by_name.get("anthropic-native", {})
    gemini = by_name.get("gemini", {})
    check.assert_true(
        anthropic.get("base_url") == "https://claude-proxy.amydong.workers.dev/v1",
        "local anthropic upstream base_url is claude proxy /v1",
    )
    check.assert_true(
        anthropic.get("default_model") == EXPECTED["final_model"],
        "local anthropic upstream default_model is claude-opus-5",
    )
    check.assert_true(
        set(EXPECTED["proxy_claude_models"]).issubset(set(anthropic.get("models") or [])),
        "local anthropic upstream exposes expected Claude models",
    )
    check.assert_true(
        anthropic_native.get("protocol") == "anthropic",
        "local native anthropic upstream uses Anthropic protocol",
    )
    check.assert_true(
        anthropic_native.get("prompt_cache") == "anthropic_explicit",
        "local native anthropic upstream uses explicit prompt caching",
    )
    check.assert_true(
        anthropic_native.get("prompt_cache_retention") == "1h",
        "local native anthropic upstream keeps stable cache entries for 1h",
    )
    check.assert_true(
        anthropic_native.get("base_url") == "https://api.anthropic.com/v1",
        "local native anthropic upstream base_url is official Anthropic /v1",
    )
    check.assert_true(
        anthropic_native.get("default_model") == EXPECTED["native_final_model"],
        "local native anthropic upstream default_model is claude-opus-5-native",
    )
    check.assert_true(
        any(
            isinstance(item, dict)
            and item.get("id") == EXPECTED["native_final_model"]
            and item.get("upstream_model") == EXPECTED["final_model"]
            for item in (anthropic_native.get("models") or [])
        ),
        "local native anthropic upstream maps native alias to Claude Opus 5",
    )
    check.assert_true(
        gemini.get("base_url") == "https://gemini.amydong.workers.dev/v1",
        "local gemini upstream base_url is OpenAI-compatible /v1",
    )
    check.assert_true(
        gemini.get("gemini_base_url") == "https://gemini.amydong.workers.dev/v1beta",
        "local gemini upstream native base_url is /v1beta",
    )
    check.assert_true(
        gemini.get("default_model") == EXPECTED["auxiliary_model"],
        "local gemini upstream default_model is gemini-3.6-flash",
    )
    check.assert_true(
        EXPECTED["auxiliary_model"] in set(gemini.get("models") or []),
        "local gemini upstream exposes gemini-3.6-flash",
    )
    for key in ("query_planner_model", "domain_sentinel_model"):
        check.assert_true(
            str(gateway.get(key) or "").strip() in {"", EXPECTED["auxiliary_model"]},
            f"local gateway.{key} is Gemini 3.6 or inherits dehydration",
        )

    routes_value = gateway.get("token_routes")
    routes = routes_value if isinstance(routes_value, list) else []
    gemini_routes = [
        route for route in routes
        if isinstance(route, dict)
        and route.get("token_env") == "OMBRE_GATEWAY_GEMINI_TOKEN"
        and route.get("default_model") == EXPECTED["auxiliary_model"]
    ]
    check.assert_true(bool(gemini_routes), "local gateway token_routes has Gemini token route")


def local_env_checks(check: Check, env: dict[str, str]) -> None:
    required = [
        "OMBRE_GATEWAY_TOKEN",
        "OMBRE_GATEWAY_GEMINI_TOKEN",
        "OMBRE_GATEWAY_ANTHROPIC_API_KEY",
        "OMBRE_GATEWAY_GEMINI_API_KEY",
    ]
    for key in required:
        check.assert_true(bool(env.get(key)), f"local .env has {key}")


def production_gateway_checks(check: Check, env: dict[str, str], gateway_base: str) -> None:
    default_token = env.get("OMBRE_GATEWAY_TOKEN", "")
    gemini_token = env.get("OMBRE_GATEWAY_GEMINI_TOKEN", "")
    if not default_token:
        check.fail("cannot test production gateway without OMBRE_GATEWAY_TOKEN")
        return

    status, health = read_json(f"{gateway_base}/health")
    gateway = health.get("gateway", {}) if isinstance(health, dict) else {}
    check.assert_true(status == 200, "production gateway /health is reachable")
    check.assert_true(
        gateway.get("upstream_default_model") == EXPECTED["final_model"],
        "production gateway default model is Claude Opus 5",
    )
    models = set(gateway.get("upstream_models") or [])
    check.assert_true(
        set(EXPECTED["required_models"]).issubset(models),
        "production gateway exposes Claude + Gemini models",
    )
    upstreams_value = gateway.get("upstreams")
    upstreams = upstreams_value if isinstance(upstreams_value, list) else []
    native_upstream = next(
        (
            item
            for item in upstreams
            if isinstance(item, dict) and item.get("name") == "anthropic-native"
        ),
        {},
    )
    check.assert_true(
        native_upstream.get("prompt_cache") == "anthropic_explicit",
        "production native Anthropic route uses explicit prompt caching",
    )
    check.assert_true(
        native_upstream.get("prompt_cache_retention") == "1h",
        "production native Anthropic route keeps stable cache entries for 1h",
    )

    status, payload = read_json(f"{gateway_base}/v1/models", token=default_token)
    model_ids = {item.get("id") for item in payload.get("data", [])} if isinstance(payload, dict) else set()
    check.assert_true(status == 200, "production gateway /v1/models accepts default token")
    check.assert_true(
        set(EXPECTED["required_models"]).issubset(model_ids),
        "production gateway /v1/models returns expected models",
    )

    chat_body = {
        "model": EXPECTED["final_model"],
        "messages": [{"role": "user", "content": "alignment check: reply ok only"}],
        "max_tokens": 8,
        "stream": False,
    }
    status, payload = read_json(
        f"{gateway_base}/v1/chat/completions",
        token=default_token,
        method="POST",
        body=chat_body,
    )
    check.assert_true(status == 200, "production gateway Claude final route works with default token")

    native_body = {
        "model": EXPECTED["native_final_model"],
        "messages": [{"role": "user", "content": "alignment check: reply ok only"}],
        "max_tokens": 8,
        "stream": False,
    }
    status, _payload = read_json(
        f"{gateway_base}/v1/messages",
        token=default_token,
        method="POST",
        body=native_body,
    )
    check.assert_true(status == 200, "production gateway native Claude Opus 5 route works")

    if gemini_token:
        gemini_body = {
            "contents": [{"role": "user", "parts": [{"text": "alignment check"}]}],
            "generationConfig": {"maxOutputTokens": 8, "temperature": 0.1},
        }
        status, _payload = read_json(
            f"{gateway_base}/v1beta/models/{EXPECTED['auxiliary_model']}:generateContent",
            token=gemini_token,
            method="POST",
            body=gemini_body,
        )
        check.assert_true(status == 200, "production gateway native Gemini route works with Gemini token")
    else:
        check.warn("skipped Gemini-token route check because OMBRE_GATEWAY_GEMINI_TOKEN is missing locally")


def relay_checks(check: Check, relay_env: dict[str, str], relay_base: str) -> None:
    relay_secret = relay_env.get("RELAY_SECRET", "")
    if not relay_secret:
        check.warn("skipped relay checks because RELAY_SECRET is missing locally")
        return

    status, _payload = read_json(f"{relay_base}/health")
    check.assert_true(status == 200, "relay /health is reachable")

    marker = f"alignment check {int(time.time())}: reply ok only"
    body = {
        "model": EXPECTED["final_model"],
        "messages": [{"role": "user", "content": marker}],
        # Opus 5 can consume a tiny output budget before emitting visible text.
        "max_tokens": 256,
        "stream": False,
    }
    status, payload = read_json(
        f"{relay_base}/v1/chat/completions",
        token=relay_secret,
        method="POST",
        body=body,
    )
    check.assert_true(status == 200, "relay final route returns a completion")

    status, debug = read_json(f"{relay_base}/debug/agent?limit=5", token=relay_secret)
    items = debug.get("items", []) if isinstance(debug, dict) else []
    latest = next(
        (
            item for item in items
            if item.get("type") == "agent_chat"
            and marker in (((item.get("request") or {}).get("last_user_preview")) or "")
        ),
        items[0] if items else {},
    )
    final = latest.get("final") or {}
    final_mcp = latest.get("final_mcp") or {}
    check.assert_true(status == 200, "relay debug endpoint is reachable")
    check.assert_true(final.get("apiType") == "claude", "relay final route uses Anthropic API type")
    check.assert_true(
        final.get("model") == EXPECTED["native_final_model"],
        "relay final route uses native Claude Opus 5 alias",
    )
    check.assert_true(not latest.get("coordinator"), "relay main route has no coordinator debug block")
    check.assert_true(final.get("toolLoop") is True, "relay final route uses native Claude MCP tool loop")
    check.assert_true(
        int(final_mcp.get("tool_count") or 0) >= 10,
        "relay native Claude final tool loop sees Ombre MCP tools",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--relay-env", default="../nuojiji-relay/.dev.vars")
    parser.add_argument("--gateway-base", default=EXPECTED["gateway_base"])
    parser.add_argument("--relay-base", default=EXPECTED["relay_base"])
    parser.add_argument("--skip-network", action="store_true")
    args = parser.parse_args()

    root = Path.cwd()
    env = load_env_file(root / args.env)
    relay_env = load_env_file((root / args.relay_env).resolve())

    check = Check()
    config_gateway_checks(check, root / args.config)
    local_env_checks(check, env)
    if not args.skip_network:
        production_gateway_checks(check, env, args.gateway_base.rstrip("/"))
        relay_checks(check, relay_env, args.relay_base.rstrip("/"))

    if check.warnings:
        print(f"\nWarnings: {len(check.warnings)}")
    if check.failures:
        print(f"\nFailures: {len(check.failures)}")
        for failure in check.failures:
            print(f"- {failure}")
        return 1
    print("\nAlignment check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
