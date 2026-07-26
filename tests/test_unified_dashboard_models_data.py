from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import textwrap
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from dotenv import dotenv_values

from web import config_api


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "frontend" / "dashboard-assets" / "models-data.js"
STYLESHEET = ROOT / "frontend" / "dashboard-assets" / "models-data.css"

PANEL_IDS = {
    "models-upstream",
    "models-dehydration",
    "models-embeddings",
    "models-reranker",
    "models-persona",
    "models-dream",
    "models-relationship-memory",
    "models-portrait-settings",
    "models-surfacing",
    "models-effective-config",
    "models-full-vault",
}


class FakeMCP:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Any] = {}

    def custom_route(self, path: str, methods: list[str]):
        def decorator(handler):
            for method in methods:
                self.routes[(method, path)] = handler
            return handler

        return decorator


class JsonRequest:
    def __init__(self, payload: object | None = None) -> None:
        self._payload = {} if payload is None else payload
        self.headers: dict[str, str] = {}
        self.query_params: dict[str, str] = {}
        self.path_params: dict[str, str] = {}

    async def json(self) -> object:
        return self._payload


def response_json(response) -> dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


@pytest.mark.asyncio
async def test_config_post_serializes_overlapping_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    max_active = 0

    class OverlapRequest(JsonRequest):
        async def json(self) -> object:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            return self._payload

    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", {"buckets_dir": "vault"})
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)

    mcp = FakeMCP()
    config_api.register(mcp)
    route = mcp.routes[("POST", "/api/config")]
    responses = await asyncio.gather(route(OverlapRequest()), route(OverlapRequest()))

    assert [response.status_code for response in responses] == [200, 200]
    assert max_active == 1


def test_models_data_module_registers_the_complete_workspace_without_ambient_globals() -> None:
    source = ASSET.read_text(encoding="utf-8")
    registered = re.findall(r"app\.registerPanel\(\{\s*id:\s*['\"]([^'\"]+)", source)

    assert set(registered) == PANEL_IDS
    assert len(registered) == len(PANEL_IDS)
    assert source.count("workspace: 'models-data'") == len(PANEL_IDS)
    assert "OmbreDashboardFeatureFactories" in source
    assert "CONFIG_PATCH_BUILDERS" in source
    for forbidden in (
        "authFetch",
        "getActiveTab",
        "loadAll(",
        "document.querySelector('.tab.active')",
        "document.querySelector(\".tab.active\")",
    ):
        assert forbidden not in source
    assert not re.search(r"(?:^|[^A-Za-z0-9_])BASE(?:[^A-Za-z0-9_]|$)", source)


def test_models_data_keeps_one_editor_per_resource_without_delegate_only_tabs() -> None:
    source = ASSET.read_text(encoding="utf-8")

    for builder in (
        "buildUpstreamPatch",
        "buildDehydrationPatch",
        "buildRerankerPatch",
        "buildPersonaPatch",
        "buildDreamPatch",
        "buildRelationshipMemoryPatch",
        "buildPortraitPatch",
        "buildSurfacingPatch",
    ):
        assert f"function {builder}(" in source

    assert "CANONICAL_EDITOR_RESOURCES" in source
    assert "mountCanonicalEmbeddingEditor" in source
    assert "openLegacyPanel('settings', 'sec-engine')" not in source
    assert "openLegacyPanel('settings', 'sec-github')" not in source
    assert "openLegacyPanel('settings', 'sec-backup')" not in source
    for removed_panel in (
        "models-compat-export",
        "models-github-backup",
        "models-migration-tools",
        "mountCompatExport",
        "mountGithubBackup",
        "mountMigrationTools",
    ):
        assert removed_panel not in source
    # Models/Data owns its own editors. Embedding operations stay in the one
    # mature editor that is physically mounted into Models/Data, so this feature
    # module must not reimplement unrelated legacy endpoints.
    for duplicate_endpoint in (
        "/api/export",
        "/api/github/config",
        "/api/github/sync",
        "/api/migrate/upload",
        "/api/embedding/migrate",
    ):
        assert duplicate_endpoint not in source


def test_models_data_scopes_config_writers_to_their_canonical_resources() -> None:
    source = ASSET.read_text(encoding="utf-8")

    persist_map = source[
        source.index("var PERSIST_REQUIRED_PANELS") : source.index(
            "function writeActionsMarkup"
        )
    ]
    assert "'models-dehydration': true" in persist_map
    assert "'models-relationship-memory': true" in persist_map

    dehydration = source[
        source.index("function buildDehydrationPatch") : source.index(
            "function buildRerankerPatch"
        )
    ]
    assert "DEHYDRATION_FIELDS" in dehydration
    for unrelated in ("embedding", "SURFACING", "merge_threshold"):
        assert unrelated not in dehydration

    surfacing = source[
        source.index("function buildSurfacingPatch") : source.index(
            "var CONFIG_PATCH_BUILDERS"
        )
    ]
    assert "SURFACING_LIMIT_FIELDS" in surfacing
    for unrelated in ("dehydration", "embedding", "merge_threshold"):
        assert unrelated not in surfacing


def test_models_data_runtime_writers_emit_only_their_owned_config_sections() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the dashboard runtime contract test")
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(ASSET))}, 'utf8');
        const registered = [];
        const calls = [];
        const window = {{ OmbreDashboardFeatureFactories: [], confirm: () => true, prompt: () => null }};
        vm.runInNewContext(source, {{ window, console, Promise, setTimeout, clearTimeout, FormData: class FormData {{}} }}, {{ filename: 'models-data.js' }});
        const app = {{
          registerPanel(panel) {{ registered.push(panel); }},
          commands: {{}},
          api: {{
            post(path, body) {{ calls.push([path, body]); return Promise.reject(new Error('captured')); }},
            readJson(response) {{ return response; }},
          }},
          store: {{ invalidate() {{}}, resource() {{ throw new Error('unexpected load'); }} }},
          ui: {{}},
        }};
        window.OmbreDashboardFeatureFactories[0](app);

        function configRoot() {{
          const listeners = {{}};
          const status = {{ textContent: '', dataset: {{}}, hidden: true }};
          return {{
            classList: {{ add() {{}} }}, innerHTML: '', setAttribute() {{}},
            querySelector(selector) {{
              if (selector.includes('data-role="status"')) return status;
              const match = selector.match(/data-config-field="([^"]+)/);
              if (!match) return null;
              const path = match[1];
              if (/enabled$/.test(path)) return {{ checked: false, value: '' }};
              if (/(tokens|seconds|hours|rounds|budget|results|top_k|hops|frontier|ids)$/.test(path)) return {{ value: '1' }};
              if (/(temperature|activation|confidence)$/.test(path)) return {{ value: '0.5' }};
              return {{ value: 'owned-value' }};
            }},
            querySelectorAll() {{ return []; }},
            addEventListener(name, handler) {{ listeners[name] = handler; }},
            save() {{
              const button = {{ dataset: {{ writeAction: 'persist' }} }};
              listeners.click({{ target: {{ closest() {{ return button; }} }} }});
            }},
          }};
        }}

        const dehydrationRoot = configRoot();
        registered.find((item) => item.id === 'models-dehydration').mount(dehydrationRoot);
        dehydrationRoot.save();
        const surfacingRoot = configRoot();
        registered.find((item) => item.id === 'models-surfacing').mount(surfacingRoot);
        surfacingRoot.save();

        setTimeout(() => {{
          if (calls.length !== 2) throw new Error('expected two scoped writes');
          const dehydration = calls[0][1];
          const surfacing = calls[1][1];
          const dehydrationKeys = Object.keys(dehydration).sort().join(',');
          const surfacingKeys = Object.keys(surfacing).sort().join(',');
          if (dehydrationKeys !== 'dehydration,gateway,persist,persist_env') throw new Error('dehydration writer leaked: ' + dehydrationKeys);
          if (surfacingKeys !== 'gateway,memory_diffusion,persist,persist_env,recall,surfacing') throw new Error('surfacing writer leaked: ' + surfacingKeys);
          if ('embedding' in dehydration || 'embedding' in surfacing || 'merge_threshold' in dehydration || 'merge_threshold' in surfacing) throw new Error('unowned section serialized');
          process.stdout.write('ok');
        }}, 20);
        """
    )
    completed = subprocess.run(
        [node, "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "ok"


def test_models_data_preserves_current_config_fields_and_secure_vault_workflows() -> None:
    source = ASSET.read_text(encoding="utf-8")
    css = STYLESHEET.read_text(encoding="utf-8")

    assert "/api/config" in source
    assert "/api/config/effective" in source
    assert "/api/backup/export" in source
    assert "/api/backup/restore?mode=" in source
    assert "/api/models" in source
    assert "/api/test/dehydration" in source
    assert "DEHYDRATION_PRESETS" in source
    assert "deepseek-ai/DeepSeek-V3" in source
    assert 'data-role="dehydration-readiness"' in source
    assert 'data-action="discover-dehydration-models"' in source
    assert "MAX_VAULT_ARCHIVE_BYTES" in source
    assert "validateVaultArchive" in source
    assert "OVERWRITE" in source
    assert "app.apiUrl('/api/backup/export')" in source
    assert "postJson('/api/backup/export/prepare'" in source
    assert "getJson('/api/backup/export/status'" in source
    assert "timeoutMs: 0" in source
    assert "response.blob()" not in source
    assert "URL.createObjectURL" not in source
    assert "URL.revokeObjectURL" not in source
    assert 'data-write-only-secret="true"' in source
    assert "accept=\".zip,application/zip\"" in source
    assert "textContent" in source
    assert "models-data-grid" in css


def test_portrait_apply_now_is_described_as_process_local_not_saved() -> None:
    source = ASSET.read_text(encoding="utf-8")

    assert "Applied to this process." in source
    assert "Use Save to config for restart durability." in source
    assert (
        "{ path: 'reflection.daily_chat_memory_mode', label: 'Daily chat memory mode', "
        "type: 'select', options: ['off', 'review', 'auto'], default: 'off' }"
    ) in source

    for section in (
        "dehydration",
        "reranker",
        "persona",
        "dream",
        "reflection",
        "portrait",
        "gateway",
        "recall",
        "memory_diffusion",
        "self_anchor",
    ):
        assert section in source

    for field in (
        "daily_chat_memory_mode",
        "daily_chat_memory_turn_limit",
        "relationship_weather_affect_anchor_enabled",
        "current_inner_state_interval_rounds",
        "memory_detail_recall_enabled",
        "query_resurface_enabled",
        "chain_min_confidence",
        "entry_bucket_id",
        "prompt_cache_retention",
        "api_key_envs",
        "gemini_base_url",
        "gemini_auth",
    ):
        assert field in source


def test_models_data_runtime_registers_panels_delegates_and_builds_an_explicit_upstream_patch() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the dashboard runtime contract test")
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(ASSET))}, 'utf8');
        const registered = [];
        const legacyCalls = [];
        const apiCalls = [];
        const canonicalEmbeddingEditor = {{
          dataset: {{ canonicalEditorMounted: 'false' }}, hidden: true,
          setAttribute(name, value) {{
            if (name === 'data-canonical-editor-mounted') this.dataset.canonicalEditorMounted = value;
          }},
        }};
        const window = {{
          OmbreDashboardFeatureFactories: [], confirm: () => true, prompt: () => null,
          document: {{
            querySelector(selector) {{
              return selector.includes('embedding-settings') ? canonicalEmbeddingEditor : null;
            }},
          }},
        }};
        const context = {{ window, console, Promise, setTimeout, clearTimeout, FormData: class FormData {{}} }};
        vm.runInNewContext(source, context, {{ filename: 'models-data.js' }});
        if (window.OmbreDashboardFeatureFactories.length !== 1) throw new Error('factory missing');

        const app = {{
          registerPanel(panel) {{ registered.push(panel); }},
          commands: {{ openLegacyPanel(tab, section) {{ legacyCalls.push([tab, section]); }} }},
          api: {{
            post(path, body) {{ apiCalls.push([path, body]); return Promise.reject(new Error('capture complete')); }},
            readJson(response) {{ return response; }},
          }},
          store: {{ invalidate() {{}}, resource() {{ throw new Error('unexpected load'); }} }},
          ui: {{}},
        }};
        window.OmbreDashboardFeatureFactories[0](app);
        if (registered.length !== 11) throw new Error('expected 11 panels');
        if (new Set(registered.map((panel) => panel.id)).size !== 11) throw new Error('duplicate panel');
        if (registered.some((panel) => panel.workspace !== 'models-data')) throw new Error('wrong workspace');
        if (registered.some((panel) => ['models-compat-export', 'models-github-backup', 'models-migration-tools'].includes(panel.id))) {{
          throw new Error('delegate-only panels should not be registered');
        }}
        if (legacyCalls.length) throw new Error('models/data should not delegate backup tabs back to system');

        let mountedEmbedding = null;
        const embeddingHost = {{ appendChild(editor) {{ mountedEmbedding = editor; }} }};
        const embeddingRoot = {{
          classList: {{ add() {{}} }}, innerHTML: '', setAttribute() {{}},
          querySelector(selector) {{ return selector.includes('canonical-editor-host') ? embeddingHost : null; }},
          querySelectorAll() {{ return []; }}, addEventListener() {{}},
        }};
        registered.find((item) => item.id === 'models-embeddings').mount(embeddingRoot);
        if (mountedEmbedding !== canonicalEmbeddingEditor) throw new Error('canonical embedding editor was not moved');
        if (canonicalEmbeddingEditor.dataset.canonicalEditorMounted !== 'true' || canonicalEmbeddingEditor.hidden) throw new Error('canonical embedding editor stayed hidden');

        const controls = {{
          name: 'provider', protocol: 'anthropic', base_url: 'https://models.example/v1',
          api_key_envs: 'OMBRE_GATEWAY_PROVIDER_API_KEY_1\\nOMBRE_GATEWAY_PROVIDER_API_KEY_2',
          api_key_values: '\\nsecond,key,with,commas', default_model: 'public-model',
          prompt_cache: 'anthropic', prompt_cache_retention: '1h', anthropic_version: '2023-06-01',
          anthropic_beta: '', models: 'public-model => upstream-model',
        }};
        const row = {{ querySelector(selector) {{
          const match = selector.match(/data-upstream-field=\"([^\"]+)/);
          return match ? {{ value: controls[match[1]] || '' }} : null;
        }} }};
        const status = {{ textContent: '', dataset: {{}}, hidden: true }};
        const listeners = {{}};
        const upstreamRoot = {{
          classList: {{ add() {{}} }}, innerHTML: '', setAttribute() {{}},
          querySelector(selector) {{ return selector.includes('data-role=\"status\"') ? status : null; }},
          querySelectorAll(selector) {{
            if (selector === '[data-upstream-row]') return [row];
            if (selector === '[data-write-action]') return [];
            return [];
          }},
          addEventListener(name, handler) {{ (listeners[name] ||= []).push(handler); }},
        }};
        registered.find((item) => item.id === 'models-upstream').mount(upstreamRoot);
        if (upstreamRoot.innerHTML.includes('data-write-action="runtime"')) throw new Error('gateway panel exposed runtime-only save');
        const persistEnvButton = {{ dataset: {{ writeAction: 'persist-env' }} }};
        for (const handler of listeners.click) {{
          handler({{ target: {{ closest(selector) {{ return selector.includes('data-write-action') ? persistEnvButton : null; }} }} }});
        }}
        controls.api_key_values = '';
        const syntheticRuntimeButton = {{ dataset: {{ writeAction: 'runtime' }} }};
        for (const handler of listeners.click) {{
          handler({{ target: {{ closest(selector) {{ return selector.includes('data-write-action') ? syntheticRuntimeButton : null; }} }} }});
        }}
        setTimeout(() => {{
          if (apiCalls.length !== 2 || apiCalls.some((call) => call[0] !== '/api/config')) throw new Error('config writes missing');
          const body = apiCalls.find((call) => call[1].persist_env)[1];
          if (body.persist !== true) throw new Error('secret write mode inferred incorrectly');
          if (body.gateway.upstreams[0].models[0].upstream_model !== 'upstream-model') throw new Error('alias lost');
          if (body.gateway.upstreams[0].api_key_envs[0] !== 'OMBRE_GATEWAY_PROVIDER_API_KEY_1') throw new Error('env slot lost');
          if (JSON.stringify(body.gateway.upstreams[0].api_key_values) !== JSON.stringify(['', 'second,key,with,commas'])) throw new Error('secret positions or commas lost');
          const runtimeBody = apiCalls.find((call) => !call[1].persist_env)[1];
          if (runtimeBody.persist !== true) throw new Error('gateway-owned patch submitted persist=false');
          process.stdout.write('ok');
        }}, 20);
        """
    )
    completed = subprocess.run(
        [node, "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "ok"


@pytest.mark.asyncio
async def test_config_get_exposes_current_sections_but_never_plaintext_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api, "read_config_yaml", lambda: {})
    monkeypatch.setattr(
        config_api.sh,
        "config",
        {
            "buckets_dir": "vault",
            "dehydration": {
                "model": "tagger",
                "base_url": "https://models.example/v1",
                "api_key": "secret-dehydration-key",
            },
            "embedding": {
                "enabled": True,
                "model": "embed",
                "base_url": "https://embed.example/v1",
                "api_key": "secret-embedding-key",
            },
            "reranker": {
                "enabled": True,
                "model": "rerank",
                "score_weight": 0,
                "api_key": "secret-reranker-key",
            },
            "persona": {
                "enabled": True,
                "model": "persona",
                "api_key": "secret-persona-key",
            },
            "dream": {
                "enabled": True,
                "model": "dream",
                "daily_hour": 0,
                "daily_probability": 0,
                "api_key": "secret-dream-key",
            },
            "reflection": {
                "enabled": True,
                "daily_chat_memory_mode": "review",
                "thinking_mode": "thinking",
                "api_key": "secret-reflection-key",
            },
            "portrait": {"enabled": True, "material_limit": 18},
            "self_anchor": {"entry_bucket_id": "self-entry"},
            "recall": {"query_resurface_enabled": False},
            "memory_diffusion": {"enabled": True, "top_k": 4},
            "gateway": {
                "cooldown_hours": 0,
                "direct_render_mode": "COMPACT",
                "retrieval_mode": "legacy",
                "upstreams": [
                    {
                        "name": "provider",
                        "protocol": "openai",
                        "base_url": "https://gateway.example/v1",
                        "api_key": "secret-direct-upstream-key",
                        "api_key_envs": ["OMBRE_GATEWAY_PROVIDER_API_KEY"],
                        "models": ["model-a"],
                    }
                ],
            },
        },
    )
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("GET", "/api/config")](JsonRequest())
    payload = response_json(response)
    serialized = json.dumps(payload)

    assert response.status_code == 200
    assert payload["reranker"]["model"] == "rerank"
    assert payload["persona"]["model"] == "persona"
    assert payload["dream"]["model"] == "dream"
    assert payload["reflection"]["daily_chat_memory_mode"] == "review"
    assert payload["reflection"]["thinking_mode"] == "enabled"
    assert payload["portrait"]["material_limit"] == 18
    assert payload["self_anchor"]["entry_bucket_id"] == "self-entry"
    assert payload["reranker"]["score_weight"] == 0
    assert payload["dream"]["daily_hour"] == 0
    assert payload["dream"]["daily_probability"] == 0
    assert payload["gateway"]["cooldown_hours"] == 0
    assert payload["gateway"]["direct_render_mode"] == "compact"
    assert payload["gateway"]["retrieval_mode"] == "bucket"
    assert payload["gateway"]["upstreams"][0]["has_direct_api_key"] is True
    assert payload["gateway"]["upstreams"][0]["key_count"] >= 1
    assert "secret-" not in serialized
    assert "api_key" not in payload["gateway"]["upstreams"][0]
    # Reading the editor contract canonicalizes legacy aliases for display,
    # but must not silently rewrite the live configuration.
    assert config_api.sh.config["reflection"]["thinking_mode"] == "thinking"
    assert config_api.sh.config["gateway"]["direct_render_mode"] == "COMPACT"
    assert config_api.sh.config["gateway"]["retrieval_mode"] == "legacy"


@pytest.mark.asyncio
async def test_config_post_validates_and_atomically_persists_current_sections_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime: dict[str, Any] = {
        "buckets_dir": "vault",
        "state_dir": "state",
        "dehydration": {"api_key": "old-secret"},
        "gateway": {
            "upstreams": [
                    {
                        "name": "provider",
                        "protocol": "openai",
                        "base_url": "https://gateway.example/v1",
                        "gemini_base_url": "https://gemini.example/v1beta",
                        "gemini_auth": "bearer",
                        "api_key": "hidden-direct-key",
                        "models": ["old-model"],
                }
            ]
        },
    }
    persisted: dict[str, Any] = {}
    env_writes: list[dict[str, str]] = []

    def persist_config(mutator):
        mutator(persisted)
        return persisted

    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(config_api, "atomic_update_config_yaml", persist_config)
    monkeypatch.setattr(config_api, "read_config_yaml", lambda: persisted)
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)
    monkeypatch.setattr(
        config_api,
        "_atomic_update_env_vars",
        lambda updates: env_writes.append(dict(updates)),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
                {
                    "persist": True,
                    "persist_env": True,
                "dehydration": {
                    "model": "tagger-v2",
                    "base_url": "https://models.example/v1",
                    "api_key": "new-runtime-secret",
                    "max_tokens": 2048,
                    "temperature": 0.2,
                },
                "reranker": {
                    "enabled": True,
                    "model": "rerank-v2",
                    "timeout_seconds": 15,
                    "candidate_limit": 30,
                    "score_weight": 0.7,
                },
                "persona": {
                    "enabled": True,
                    "event_recording_enabled": False,
                    "conflict_nudge_enabled": True,
                    "model": "persona-v2",
                    "base_url": "https://persona.example/v1",
                },
                "dream": {
                    "enabled": True,
                    "auto_enabled": True,
                    "surface_enabled": True,
                    "inject_enabled": False,
                    "retain_after_inject": True,
                    "daily_hour": 3,
                    "daily_probability": 0.4,
                    "min_material_count": 5,
                    "material_window_hours": 48,
                },
                "reflection": {
                    "enabled": True,
                    "auto_enabled": True,
                    "daily_enabled": True,
                    "daily_min_memory_items": 5,
                    "daily_conversation_turn_limit": 12,
                    "daily_chat_memory_mode": "review",
                    "daily_chat_memory_turn_limit": 0,
                    "memory_affect_anchor_enabled": False,
                    "relationship_weather_affect_anchor_enabled": False,
                },
                "portrait": {
                    "enabled": True,
                    "auto_enabled": True,
                    "auto_initial_enabled": False,
                    "daily_enabled": True,
                    "material_limit": 18,
                    "first_run_material_limit": 160,
                    "user_rewrite_evidence_delta": 10,
                    "manual_suppress_days": 14,
                },
                "self_anchor": {"entry_bucket_id": "self-entry"},
                "gateway": {
                    "cooldown_hours": 6,
                    "direct_render_mode": "auto",
                    "retrieval_mode": "graph",
                    "upstreams": [
                        {
                            "name": "provider",
                            "protocol": "openai",
                            "base_url": "https://gateway.example/v1",
                            "gemini_base_url": "https://gemini.example/v1beta",
                            "gemini_auth": "bearer",
                            "api_key_envs": ["OMBRE_GATEWAY_PROVIDER_API_KEY"],
                            "models": [
                                {
                                    "id": "public-model",
                                    "upstream_model": "provider-model",
                                }
                            ],
                        }
                    ],
                },
                "recall": {"query_resurface_enabled": False},
                "memory_diffusion": {
                    "enabled": True,
                    "top_k": 4,
                    "min_activation": 0.18,
                    "chain_walk_enabled": False,
                    "chain_max_hops": 6,
                    "chain_min_confidence": 0.72,
                    "chain_max_frontier": 24,
                },
            }
        )
    )
    payload = response_json(response)

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["gateway_restart_required"] is True
    assert runtime["reranker"]["candidate_limit"] == 30
    assert runtime["reflection"]["daily_chat_memory_mode"] == "review"
    assert runtime["gateway"]["upstreams"][0]["api_key"] == "hidden-direct-key"
    assert persisted["gateway"]["upstreams"][0]["api_key"] == "hidden-direct-key"
    assert persisted["gateway"]["upstreams"][0]["gemini_base_url"] == "https://gemini.example/v1beta"
    assert persisted["gateway"]["upstreams"][0]["gemini_auth"] == "bearer"
    assert persisted["persona"]["model"] == "persona-v2"
    assert persisted["persona"]["conflict_nudge_enabled"] is True
    assert persisted["self_anchor"]["entry_bucket_id"] == "self-entry"
    assert "api_key" not in persisted["dehydration"]
    assert "new-runtime-secret" not in json.dumps(persisted)
    assert env_writes == [{"OMBRE_COMPRESS_API_KEY": "new-runtime-secret"}]


@pytest.mark.asyncio
async def test_config_post_rejects_unknown_fields_and_unsafe_upstream_envs_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = {"buckets_dir": "vault", "persona": {"enabled": True}}
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(config_api, "read_config_yaml", lambda: {})
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)

    mcp = FakeMCP()
    config_api.register(mcp)
    unknown = await mcp.routes[("POST", "/api/config")](
        JsonRequest({"persona": {"enabled": False, "__proto__": {"polluted": True}}})
    )
    unsafe_env = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist_env": True,
                "gateway": {
                    "upstreams": [
                        {
                            "name": "provider",
                            "protocol": "openai",
                            "base_url": "https://gateway.example/v1",
                            "api_key_envs": ["PYTHONPATH"],
                            "api_key_values": ["malicious"],
                            "models": ["model"],
                        }
                    ]
                },
            }
        )
    )
    non_finite = await mcp.routes[("POST", "/api/config")](
        JsonRequest({"reranker": {"score_weight": float("nan")}})
    )

    assert unknown.status_code == 400
    assert unsafe_env.status_code == 400
    assert non_finite.status_code == 400
    assert runtime == {"buckets_dir": "vault", "persona": {"enabled": True}}


@pytest.mark.asyncio
async def test_config_post_rejects_malformed_legacy_sections_before_any_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = {
        "buckets_dir": "vault",
        "dehydration": {
            "model": "old-model",
            "max_tokens": 1024,
            "api_key": "old-secret",
        },
    }
    original_runtime = deepcopy(runtime)
    yaml_writes: list[object] = []
    env_writes: list[dict[str, str]] = []
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)
    monkeypatch.setattr(config_api.sh, "_env_persistence_issue", lambda: "")
    monkeypatch.setattr(config_api.sh, "dehydrator", None)
    monkeypatch.setattr(
        config_api,
        "atomic_update_config_yaml",
        lambda mutator: yaml_writes.append(mutator),
    )
    monkeypatch.setattr(
        config_api,
        "_atomic_update_env_vars",
        lambda updates: env_writes.append(dict(updates)),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    route = mcp.routes[("POST", "/api/config")]
    malformed = await route(
        JsonRequest(
            {
                "persist": True,
                "persist_env": True,
                "dehydration": {
                    "model": "new-model",
                    "max_tokens": "bad",
                    "api_key": "new-secret",
                },
            }
        )
    )
    unknown = await route(
        JsonRequest({"embedding": {"model": "new-model", "unexpected": True}})
    )

    assert malformed.status_code == 400
    assert unknown.status_code == 400
    assert response_json(malformed)["error"] == "dehydration.max_tokens must be an integer"
    assert "unknown fields" in response_json(unknown)["error"]
    assert runtime == original_runtime
    assert yaml_writes == []
    assert env_writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"dehydration": {"model": "tagger-v2"}},
        {"embedding": {"enabled": True}},
        {"gateway": {"cooldown_hours": 2}},
        {"reranker": {"enabled": True}},
        {"persona": {"enabled": True}},
        {"dream": {"enabled": True}},
        {"memory_diffusion": {"enabled": True}},
        {"self_anchor": {"entry_bucket_id": "anchor-id"}},
    ],
)
async def test_gateway_owned_config_rejects_runtime_only_updates(
    monkeypatch: pytest.MonkeyPatch,
    patch: dict[str, Any],
) -> None:
    runtime = {"buckets_dir": "vault"}
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(
        config_api,
        "read_config_yaml",
        lambda: pytest.fail("runtime-only Gateway updates must reject before I/O"),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](JsonRequest(patch))

    assert response.status_code == 400
    assert response_json(response)["error"] == (
        "Gateway-owned settings require persist=true"
    )
    assert runtime == {"buckets_dir": "vault"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"dehydration": {"api_key": "new-secret"}},
        {"embedding": {"api_key": "new-secret"}},
        {"reranker": {"api_key": "new-secret"}},
        {"persona": {"api_key": "new-secret"}},
        {"dream": {"api_key": "new-secret"}},
        {"reflection": {"api_key": "new-secret"}},
        {"gateway": {"domain_sentinel_api_key": "new-secret"}},
    ],
)
async def test_gateway_owned_secret_updates_require_env_persistence(
    monkeypatch: pytest.MonkeyPatch,
    patch: dict[str, Any],
) -> None:
    runtime = {"buckets_dir": "vault"}
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(
        config_api,
        "read_config_yaml",
        lambda: pytest.fail("non-durable secret updates must reject before I/O"),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](JsonRequest(patch))

    assert response.status_code == 400
    assert "persist_env=true" in response_json(response)["error"]
    assert runtime == {"buckets_dir": "vault"}


@pytest.mark.asyncio
async def test_persisted_dehydration_reports_gateway_restart_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = {"buckets_dir": "vault", "dehydration": {"model": "old"}}
    persisted: dict[str, Any] = {}

    def persist_config(mutator):
        mutator(persisted)
        return persisted

    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(config_api.sh, "dehydrator", None)
    monkeypatch.setattr(config_api, "read_config_yaml", lambda: {})
    monkeypatch.setattr(config_api, "atomic_update_config_yaml", persist_config)

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest({"persist": True, "dehydration": {"model": "new"}})
    )

    assert response.status_code == 200
    assert response_json(response)["gateway_restart_required"] is True


def test_gateway_upstream_legacy_shapes_round_trip_without_semantic_loss() -> None:
    current = {
        "gateway": {
            "upstreams": [
                {
                    "name": "legacy-provider",
                    "api_format": "claude",
                    "base_url": "https://legacy.example/v1",
                    "api_key_env": "LEGACY_AUTH_TOKEN",
                    "models": [
                        {
                            "alias": "public-model",
                            "provider_model": "provider-model",
                        }
                    ],
                }
            ]
        }
    }

    public = config_api._public_gateway_upstreams(current)
    assert public[0]["protocol"] == "anthropic"
    assert public[0]["api_key_envs"] == ["LEGACY_AUTH_TOKEN"]
    assert public[0]["models"] == [
        {"id": "public-model", "upstream_model": "provider-model"}
    ]

    normalized, env_updates, _persist_env = config_api._normalize_current_config_request(
        {"persist": True, "gateway": {"upstreams": public}}, current
    )
    assert normalized["gateway"]["upstreams"][0]["protocol"] == "anthropic"
    assert normalized["gateway"]["upstreams"][0]["api_key_envs"] == [
        "LEGACY_AUTH_TOKEN"
    ]
    assert normalized["gateway"]["upstreams"][0]["models"] == [
        {"id": "public-model", "upstream_model": "provider-model"}
    ]
    assert env_updates == {}

    public[0]["api_key_values"] = ["new-secret"]
    with pytest.raises(ValueError, match=r"OMBRE_GATEWAY_\*_API_KEY"):
        config_api._normalize_current_config_request(
            {
                "persist": True,
                "persist_env": True,
                "gateway": {"upstreams": public},
            },
            current,
        )

    public[0]["api_key_values"] = []
    public[0]["base_url"] = "https://attacker.example/v1"
    with pytest.raises(ValueError, match="legacy environment reference"):
        config_api._normalize_current_config_request(
            {"persist": True, "gateway": {"upstreams": public}},
            current,
        )


@pytest.mark.parametrize(
    "env_name",
    [
        "OMBRE_GATEWAY_TOKEN",
        "OMBRE_CHATGPT_OAUTH_ACCESS_TOKEN",
        "OMBRE_DASHBOARD_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "DATABASE_URL",
        "PATH",
        "OPENAI_API_KEY",
        "PROVIDER_API_KEY",
        "STRIPE_API_KEY",
        "SENTRY_API_KEY",
    ],
)
def test_gateway_upstream_rejects_non_provider_secret_environment_references(
    env_name: str,
) -> None:
    with pytest.raises(ValueError, match=r"OMBRE_GATEWAY_\*_API_KEY"):
        config_api._normalize_current_config_request(
            {
                "persist": True,
                "gateway": {
                    "upstreams": [
                        {
                            "name": "untrusted-target",
                            "protocol": "openai",
                            "base_url": "https://attacker.example/v1",
                            "api_key_envs": [env_name],
                            "models": ["test-model"],
                        }
                    ]
                },
            },
            {"gateway": {"upstreams": []}},
        )


@pytest.mark.parametrize(
    "env_name",
    [
        "OMBRE_GATEWAY_ANTHROPIC_API_KEY",
        "OMBRE_GATEWAY_PROVIDER_API_KEY_2",
    ],
)
def test_gateway_upstream_accepts_provider_api_key_environment_references(
    env_name: str,
) -> None:
    normalized, env_updates, _persist_env = (
        config_api._normalize_current_config_request(
            {
                "persist": True,
                "gateway": {
                    "upstreams": [
                        {
                            "name": "provider",
                            "protocol": "openai",
                            "base_url": "https://provider.example/v1",
                            "api_key_envs": [env_name],
                            "models": ["test-model"],
                        }
                    ]
                },
            },
            {"gateway": {"upstreams": []}},
        )
    )

    assert normalized["gateway"]["upstreams"][0]["api_key_envs"] == [env_name]
    assert env_updates == {}


def test_gateway_upstream_rejects_conflicting_values_for_one_key_slot() -> None:
    shared_env = "OMBRE_GATEWAY_PROVIDER_API_KEY"
    with pytest.raises(ValueError, match="conflicting values"):
        config_api._normalize_current_config_request(
            {
                "persist": True,
                "persist_env": True,
                "gateway": {
                    "upstreams": [
                        {
                            "name": "provider-a",
                            "protocol": "openai",
                            "base_url": "https://a.example/v1",
                            "api_key_envs": [shared_env],
                            "api_key_values": ["secret-a"],
                            "models": ["model-a"],
                        },
                        {
                            "name": "provider-b",
                            "protocol": "openai",
                            "base_url": "https://b.example/v1",
                            "api_key_envs": [shared_env],
                            "api_key_values": ["secret-b"],
                            "models": ["model-b"],
                        },
                    ]
                },
            },
            {"gateway": {"upstreams": []}},
        )


@pytest.mark.parametrize("key_field", ["api_key_envs", "api_key_values"])
def test_gateway_upstream_bounds_provider_key_slots(key_field: str) -> None:
    upstream: dict[str, Any] = {
        "name": "provider",
        "protocol": "openai",
        "base_url": "https://provider.example/v1",
        "api_key_envs": ["OMBRE_GATEWAY_PROVIDER_API_KEY"],
        "models": ["test-model"],
    }
    if key_field == "api_key_envs":
        upstream[key_field] = [
            f"OMBRE_GATEWAY_PROVIDER_API_KEY_{index}" for index in range(1, 34)
        ]
    else:
        upstream[key_field] = [f"secret-{index}" for index in range(33)]

    with pytest.raises(ValueError, match="supports at most 32 key slots"):
        config_api._normalize_current_config_request(
            {
                "persist": True,
                "persist_env": True,
                "gateway": {"upstreams": [upstream]},
            },
            {"gateway": {"upstreams": []}},
        )


@pytest.mark.parametrize(
    "replacement_upstreams",
    [
        [],
        [
            {
                "name": "renamed-provider",
                "protocol": "openai",
                "base_url": "https://provider.example/v1",
                "api_key_envs": [],
                "models": ["test-model"],
            }
        ],
        [
            {
                "name": "legacy-provider",
                "protocol": "openai",
                "base_url": "https://attacker.example/v1",
                "api_key_envs": [],
                "models": ["test-model"],
            }
        ],
    ],
)
def test_gateway_upstream_rejects_dropping_or_renaming_legacy_direct_secrets(
    replacement_upstreams: list[dict[str, Any]],
) -> None:
    current = {
        "gateway": {
            "upstreams": [
                {
                    "name": "legacy-provider",
                    "protocol": "openai",
                    "base_url": "https://provider.example/v1",
                    "api_key": "legacy-direct-secret",
                    "models": ["test-model"],
                }
            ]
        }
    }

    with pytest.raises(ValueError, match="legacy direct API key"):
        config_api._normalize_current_config_request(
            {
                "persist": True,
                "gateway": {"upstreams": replacement_upstreams},
            },
            current,
        )


def test_public_config_uses_runtime_defaults_and_inherited_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "OMBRE_RERANKER_API_KEY",
        "OMBRE_PERSONA_API_KEY",
        "OMBRE_PERSONA_BASE_URL",
        "OMBRE_PERSONA_MODEL",
        "OMBRE_DREAM_API_KEY",
        "OMBRE_DREAM_BASE_URL",
        "OMBRE_DREAM_MODEL",
        "OMBRE_REFLECTION_API_KEY",
        "OMBRE_EMBEDDING_API_KEY",
        "OMBRE_EMBED_API_KEY",
        "OMBRE_COMPRESS_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    public = config_api._current_public_config(
        {
            "dehydration": {
                "api_key": "shared-secret",
                "base_url": "https://provider.example/v1",
                "model": "shared-model",
            },
            "embedding": {},
            "reranker": {},
            "persona": {},
            "dream": {},
            "reflection": {},
        }
    )

    assert public["reranker"]["api_ready"] is True
    assert public["reranker"]["has_own_api_key"] is False
    assert public["reranker"]["api_key_masked"] == ""
    assert public["persona"]["model"] == ""
    assert public["persona"]["base_url"] == ""
    assert public["persona"]["effective_model"] == "deepseek-chat"
    assert public["persona"]["effective_base_url"] == (
        "https://api.deepseek.com/v1"
    )
    assert public["persona"]["api_ready"] is True
    assert public["persona"]["has_own_api_key"] is False
    assert public["persona"]["conflict_nudge_enabled"] is False
    assert public["dream"]["model"] == ""
    assert public["dream"]["base_url"] == ""
    assert public["dream"]["effective_model"] == "deepseek-v4-flash"
    assert public["dream"]["api_ready"] is False
    assert public["reflection"]["model"] == ""
    assert public["reflection"]["base_url"] == ""
    assert public["reflection"]["effective_model"] == "shared-model"
    assert public["reflection"]["effective_base_url"] == (
        "https://provider.example/v1"
    )
    assert public["reflection"]["api_ready"] is True
    assert public["reflection"]["has_own_api_key"] is False


def test_persona_engine_treats_blank_model_fields_as_runtime_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from persona_engine import PersonaStateEngine

    for name in ("OMBRE_PERSONA_BASE_URL", "OMBRE_PERSONA_MODEL"):
        monkeypatch.delenv(name, raising=False)
    engine = PersonaStateEngine(
        {
            "buckets_dir": str(tmp_path),
            "state_dir": str(tmp_path),
            "identity": {"ai": "Aki", "human": "Amy"},
            "persona": {
                "enabled": False,
                "model": "",
                "base_url": "",
            },
        }
    )

    assert engine.model == "deepseek-chat"
    assert engine.base_url == "https://api.deepseek.com/v1"


@pytest.mark.parametrize(
    ("section", "expected_model", "expected_base_url"),
    [
        ("persona", "deepseek-chat", "https://api.deepseek.com/v1"),
        ("dream", "deepseek-v4-flash", "https://api.deepseek.com"),
        ("reflection", "shared-model", "https://provider.example/v1"),
    ],
)
def test_unrelated_model_toggle_keeps_blank_inheritance_effective_at_runtime(
    monkeypatch: pytest.MonkeyPatch,
    section: str,
    expected_model: str,
    expected_base_url: str,
) -> None:
    class Engine:
        enabled = True
        model = "before"
        base_url = "https://before.example"

    engine = Engine()
    runtime = {
        "buckets_dir": "vault",
        "dehydration": {
            "model": "shared-model",
            "base_url": "https://provider.example/v1",
            "api_key": "shared-secret",
        },
        section: {"enabled": True, "model": "", "base_url": ""},
    }
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(config_api.sh, f"{section}_engine", engine, raising=False)
    monkeypatch.setattr(config_api, "_refresh_current_engine_clients", lambda _changed: None)

    config_api._apply_current_runtime_sections(
        {section: {"enabled": False, "model": "", "base_url": ""}}
    )

    assert runtime[section]["model"] == ""
    assert runtime[section]["base_url"] == ""
    assert engine.model == expected_model
    assert engine.base_url == expected_base_url


@pytest.mark.asyncio
async def test_config_post_validates_the_whole_request_before_writing_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[dict[str, str]] = []
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", {"buckets_dir": "vault"})
    monkeypatch.setattr(config_api, "read_config_yaml", lambda: {})
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)
    monkeypatch.setattr(
        config_api,
        "_atomic_update_env_vars",
        lambda updates: writes.append(dict(updates)),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist": True,
                "persist_env": True,
                "mcp_auth_mode": "invalid",
                "persona": {"api_key": "must-not-be-written"},
            }
        )
    )

    assert response.status_code == 400
    assert writes == []


@pytest.mark.asyncio
async def test_config_post_persists_explicit_secrets_only_to_validated_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = {"buckets_dir": "vault", "gateway": {"upstreams": []}}
    env_updates: dict[str, str] = {}
    persisted: dict[str, Any] = {}

    def persist_config(mutator):
        mutator(persisted)
        return persisted

    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(config_api, "read_config_yaml", lambda: {})
    monkeypatch.setattr(config_api, "atomic_update_config_yaml", persist_config)
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)
    monkeypatch.setattr(
        config_api,
        "_atomic_update_env_vars",
        lambda updates: env_updates.update(updates),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist": True,
                "persist_env": True,
                "persona": {"api_key": "persona-secret"},
                "dream": {"api_key": "dream-secret"},
                "reflection": {"api_key": "reflection-secret"},
                "gateway": {
                    "upstreams": [
                        {
                            "name": "provider",
                            "protocol": "anthropic",
                            "base_url": "https://gateway.example/v1",
                            "api_key_envs": ["OMBRE_GATEWAY_PROVIDER_API_KEY_1"],
                            "api_key_values": ["provider-secret"],
                            "models": ["model"],
                        }
                    ]
                },
            }
        )
    )

    assert response.status_code == 200
    assert env_updates == {
        "OMBRE_PERSONA_API_KEY": "persona-secret",
        "OMBRE_DREAM_API_KEY": "dream-secret",
        "OMBRE_REFLECTION_API_KEY": "reflection-secret",
        "OMBRE_GATEWAY_PROVIDER_API_KEY_1": "provider-secret",
    }
    assert "api_key_values" not in json.dumps(runtime)
    runtime["reflection"].pop("api_key")
    assert config_api._current_public_config(runtime)["reflection"][
        "has_own_api_key"
    ] is True


@pytest.mark.asyncio
async def test_config_post_preserves_blank_upstream_secret_slots_and_embedded_commas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_updates: dict[str, str] = {}
    first_name = "OMBRE_GATEWAY_PROVIDER_API_KEY_1"
    second_name = "OMBRE_GATEWAY_PROVIDER_API_KEY_2"
    second_secret = "second,key,with,commas"
    persisted: dict[str, Any] = {}

    def persist_config(mutator):
        mutator(persisted)
        return persisted
    monkeypatch.delenv(first_name, raising=False)
    monkeypatch.delenv(second_name, raising=False)
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(
        config_api.sh,
        "config",
        {"buckets_dir": "vault", "gateway": {"upstreams": []}},
    )
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)
    monkeypatch.setattr(config_api, "read_config_yaml", lambda: {})
    monkeypatch.setattr(config_api, "atomic_update_config_yaml", persist_config)
    monkeypatch.setattr(
        config_api,
        "_atomic_update_env_vars",
        lambda updates: env_updates.update(updates),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist": True,
                "persist_env": True,
                "gateway": {
                    "upstreams": [
                        {
                            "name": "provider",
                            "protocol": "openai",
                            "base_url": "https://gateway.example/v1",
                            "api_key_envs": [first_name, second_name],
                            "api_key_values": ["", second_secret],
                            "models": ["model"],
                        }
                    ]
                },
            }
        )
    )

    assert response.status_code == 200
    assert env_updates == {second_name: second_secret}
    assert first_name not in os.environ
    assert os.environ[second_name] == second_secret


@pytest.mark.asyncio
async def test_config_post_rejects_fake_container_env_persistence_without_a_mounted_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret_name = "OMBRE_PERSONA_API_KEY"
    runtime = {
        "buckets_dir": "vault",
        "persona": {"api_key": "old-persona-secret"},
    }
    monkeypatch.delenv(secret_name, raising=False)
    monkeypatch.delenv("OMBRE_ENV_PATH", raising=False)
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(config_api.sh, "repo_root", str(tmp_path))
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: True)
    monkeypatch.setattr(config_api.sh, "persona_engine", None, raising=False)
    monkeypatch.setattr(
        config_api,
        "_atomic_update_env_vars",
        lambda _updates: pytest.fail("must reject before writing an ephemeral file"),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist_env": True,
                "persona": {"api_key": "new-persona-secret"},
            }
        )
    )

    payload = response_json(response)
    assert response.status_code == 409
    assert "OMBRE_ENV_PATH" in payload["error"]
    assert runtime == {
        "buckets_dir": "vault",
        "persona": {"api_key": "old-persona-secret"},
    }
    assert secret_name not in os.environ


@pytest.mark.asyncio
async def test_config_post_rolls_back_yaml_when_env_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    original = {
        "buckets_dir": "vault",
        "persona": {"model": "old-model"},
    }
    config_path.write_text(
        "buckets_dir: vault\npersona:\n  model: old-model\n",
        encoding="utf-8",
    )
    runtime = deepcopy(original)
    monkeypatch.setenv("OMBRE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("OMBRE_ENV_PATH", str(env_path))
    monkeypatch.delenv("OMBRE_PERSONA_API_KEY", raising=False)
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)
    monkeypatch.setattr(config_api.sh, "persona_engine", None, raising=False)
    monkeypatch.setattr(
        config_api,
        "_atomic_update_env_vars",
        lambda _updates: (_ for _ in ()).throw(OSError("env commit failed")),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist": True,
                "persist_env": True,
                "persona": {
                    "model": "new-model",
                    "api_key": "new-persona-secret",
                },
            }
        )
    )

    assert response.status_code == 409
    assert config_api.read_config_yaml() == original
    assert runtime == original
    assert "new-persona-secret" not in json.dumps(response_json(response))


@pytest.mark.asyncio
async def test_config_post_does_not_commit_secrets_when_dehydrator_reload_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_writes: list[dict[str, str]] = []
    secret_name = "OMBRE_COMPRESS_API_KEY"
    monkeypatch.delenv(secret_name, raising=False)
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(
        config_api.sh,
        "config",
        {
            "buckets_dir": "vault",
            "dehydration": {
                "model": "old-model",
                "base_url": "https://models.example/v1",
                "api_key": "old-runtime-key",
            },
        },
    )
    monkeypatch.setattr(
        config_api.sh,
        "dehydrator",
        dehydrator := SimpleNamespace(
            model="old-model",
            base_url="https://models.example/v1",
            max_tokens=1024,
            temperature=0.1,
            timeout_seconds=10.0,
            api_format="openai_compat",
            api_key="old-runtime-key",
            api_available=True,
            client=object(),
        ),
    )
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)
    monkeypatch.setattr(config_api, "read_config_yaml", lambda: {})
    monkeypatch.setattr(
        config_api,
        "_atomic_update_env_vars",
        lambda updates: env_writes.append(dict(updates)),
    )

    import openai

    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("client build failed: new-dehydration-secret")
        ),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist": True,
                "persist_env": True,
                "dehydration": {
                    "api_key": "new-dehydration-secret",
                    "base_url": "https://new-models.example/v1",
                },
            }
        )
    )

    assert response.status_code == 500
    assert env_writes == []
    assert secret_name not in os.environ
    assert "new-dehydration-secret" not in json.dumps(response_json(response))
    assert config_api.sh.config["dehydration"] == {
        "model": "old-model",
        "base_url": "https://models.example/v1",
        "api_key": "old-runtime-key",
    }
    assert dehydrator.base_url == "https://models.example/v1"
    assert dehydrator.api_key == "old-runtime-key"


@pytest.mark.asyncio
async def test_config_post_does_not_commit_secrets_when_embedding_reload_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_writes: list[dict[str, str]] = []
    secret_name = "OMBRE_EMBED_API_KEY"
    monkeypatch.delenv(secret_name, raising=False)
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(
        config_api.sh,
        "config",
        {"buckets_dir": "vault", "embedding": {"enabled": False}},
    )
    monkeypatch.setattr(config_api.sh, "dehydrator", None)
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)
    monkeypatch.setattr(
        config_api,
        "_atomic_update_env_vars",
        lambda updates: env_writes.append(dict(updates)),
    )
    monkeypatch.setattr(
        config_api,
        "_rebuild_embedding_runtime",
        lambda: (_ for _ in ()).throw(
            RuntimeError("embedding build failed: new-embedding-secret")
        ),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist_env": True,
                "embedding": {
                    "enabled": True,
                    "api_key": "new-embedding-secret",
                },
            }
        )
    )

    assert response.status_code == 400
    assert env_writes == []
    assert secret_name not in os.environ
    assert "new-embedding-secret" not in json.dumps(response_json(response))
    assert config_api.sh.config["embedding"] == {"enabled": False}


@pytest.mark.asyncio
async def test_config_post_does_not_commit_secrets_when_yaml_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_writes: list[dict[str, str]] = []
    secret_name = "OMBRE_PERSONA_API_KEY"
    monkeypatch.delenv(secret_name, raising=False)
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    runtime = {
        "buckets_dir": "vault",
        "persona": {"enabled": True, "api_key": "old-persona-secret"},
    }
    old_client = object()
    persona_engine = SimpleNamespace(
        enabled=True,
        mode="llm",
        api_key="old-persona-secret",
        base_url="https://persona.example/v1",
        client=old_client,
    )
    monkeypatch.setattr(config_api.sh, "config", runtime)
    monkeypatch.setattr(
        config_api.sh, "persona_engine", persona_engine, raising=False
    )
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: False)
    monkeypatch.setattr(
        config_api,
        "_atomic_update_env_vars",
        lambda updates: env_writes.append(dict(updates)),
    )
    monkeypatch.setattr(
        config_api,
        "atomic_update_config_yaml",
        lambda _mutator: (_ for _ in ()).throw(
            RuntimeError("yaml write failed: new-persona-secret")
        ),
    )

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist": True,
                "persist_env": True,
                "persona": {"api_key": "new-persona-secret"},
            }
        )
    )

    assert response.status_code == 500
    assert env_writes == []
    assert secret_name not in os.environ
    assert "new-persona-secret" not in json.dumps(response_json(response))
    assert runtime == {
        "buckets_dir": "vault",
        "persona": {"enabled": True, "api_key": "old-persona-secret"},
    }
    assert persona_engine.api_key == "old-persona-secret"
    assert persona_engine.client is old_client


@pytest.mark.asyncio
async def test_config_post_safely_serializes_quoted_backslashed_secret_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    secret_name = "OMBRE_PERSONA_API_KEY"
    secret_value = r'''prefix\\path\"double" and 'single' $TOKEN ${TOKEN} # fragment\tail'''
    monkeypatch.delenv(secret_name, raising=False)
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "config", {"buckets_dir": "vault"})
    monkeypatch.setattr(config_api.sh, "persona_engine", None, raising=False)
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: True)
    monkeypatch.setenv("OMBRE_ENV_PATH", str(env_path))

    mcp = FakeMCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/config")](
        JsonRequest(
            {
                "persist_env": True,
                "persona": {"api_key": secret_value},
            }
        )
    )

    assert response.status_code == 200
    assert os.environ[secret_name] == secret_value
    assert dotenv_values(env_path, interpolate=False)[secret_name] == secret_value
    serialized = env_path.read_text(encoding="utf-8")
    assert serialized.count("\n") == 1
    assert serialized.startswith(f"{secret_name}='")
    assert "\\'single\\'" in serialized
    assert "$TOKEN ${TOKEN}" in serialized
    assert "\\\\\\\\path" in serialized
    monkeypatch.delenv(secret_name)
    assert config_api.sh._read_env_var(secret_name) == secret_value
