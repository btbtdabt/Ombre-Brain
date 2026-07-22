from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from web import dashboard as dashboard_web


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "frontend" / "dashboard.html"
ASSETS = ROOT / "frontend" / "dashboard-assets"
BOOTSTRAP = ASSETS / "unified-shell.js"
SHELL_STYLES = ASSETS / "unified-shell.css"
WEB_MANIFEST = ROOT / "frontend" / "manifest.json"
NODE = shutil.which("node")


def _node_eval_args(script: str) -> list[str]:
    node = NODE
    assert node is not None, "Node.js is unavailable"
    return [node, "-e", script]


class _RouteMCP:
    def __init__(self) -> None:
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(handler):
            for method in methods:
                self.routes[(method, path)] = handler
            return handler

        return decorator


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_dashboard_loads_core_features_then_single_bootstrap() -> None:
    html = _read(DASHBOARD)
    ordered_assets = [
        "dashboard-assets/core/path.js",
        "dashboard-assets/core/api.js",
        "dashboard-assets/core/router.js",
        "dashboard-assets/core/store.js",
        "dashboard-assets/core/app-shell.js",
        "dashboard-assets/memory-care.js",
        "dashboard-assets/memory-profile.js",
        "dashboard-assets/memory-insights.js",
        "dashboard-assets/models-data.js",
        "dashboard-assets/unified-shell.js",
    ]

    offsets = [html.index(asset) for asset in ordered_assets]
    assert offsets == sorted(offsets)
    assert html.count("dashboard-assets/unified-shell.js") == 1
    assert html.count("dashboard-assets/core/path.js") == 1
    assert html.index("dashboard-assets/core/path.js") < html.index(
        "async function checkAuth"
    )


def test_dashboard_cache_busts_the_fixed_memory_care_runtime() -> None:
    html = _read(DASHBOARD)

    assert (
        'dashboard-assets/memory-care.js?v=20260722-memory-candidate-fix-v1'
        in html
    )


def test_bootstrap_adapts_ui_and_legacy_commands_before_feature_loading() -> None:
    source = _read(BOOTSTRAP)

    assert "app.ui =" in source
    assert "escapeAttr" in source
    assert "setStatus" in source
    assert "app.assetUrl" in source
    assert "openLegacyPanel" in source
    assert "refreshBuckets" in source
    assert "openBucket" in source
    assert source.index("app.ui =") < source.index("app.loadQueuedFeatures()")


def test_bootstrap_registers_every_existing_p0_panel_as_a_legacy_adapter() -> None:
    source = _read(BOOTSTRAP)
    legacy_panels = source[
        source.index("var LEGACY_PANELS") : source.index("var PANEL_ALIASES")
    ]
    panel_aliases = source[
        source.index("var PANEL_ALIASES") : source.index("var SECTION_PANELS")
    ]
    expected = {
        "shared-buckets": "list",
        "shared-search": "list",
        "shared-breath": "breath",
        "shared-network": "network",
        "shared-import": "import",
        "system-plans": "plan",
        "system-letters": "letters",
        "system-anchors": "anchors",
        "system-logs": "logs",
        "system-status": "settings",
        "system-replay-debug": "v3-debug",
        "system-about": "about",
    }

    for panel, tab in expected.items():
        assert f"'{panel}':" in legacy_panels
        assert f"tab: '{tab}'" in legacy_panels

    aliases = {
        "models-compat-export": ("system-status", "sec-backup"),
        "models-github-backup": ("system-status", "sec-github"),
        "models-migration-tools": ("system-status", "sec-backup"),
        "system-errors": ("system-logs", None),
        "system-identity-settings": ("system-status", "sec-me"),
        "system-auth-settings": ("system-status", "sec-me"),
        "system-mcp-settings": ("system-status", "sec-mcp"),
        "system-transport-settings": ("system-status", "sec-mcp"),
        "system-env-settings": ("system-status", "sec-env"),
        "system-tunnel-settings": ("system-status", "sec-me"),
        "system-diagnostics": ("system-status", "sec-service"),
        "system-version-update": ("system-status", "sec-version"),
        "system-restart-controls": ("system-status", "sec-service"),
        "system-developer": ("system-status", "sec-dev-mode"),
    }
    for alias, (target, section) in aliases.items():
        assert f"'{alias}':" not in legacy_panels
        alias_start = panel_aliases.index(f"'{alias}':")
        alias_end = panel_aliases.find("\n    },", alias_start)
        alias_source = panel_aliases[alias_start:alias_end]
        assert f"targetPanel: '{target}'" in alias_source
        if section:
            assert f"section: '{section}'" in alias_source


@pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")
def test_removed_panel_urls_replace_to_their_canonical_owner() -> None:
    source = _read(BOOTSTRAP)
    aliases_source = source[
        source.index("var PANEL_ALIASES") : source.index("var SECTION_PANELS")
    ]
    register_source = source[
        source.index("function registerPanelAliases") : source.index(
            "function matchingPanelForTab"
        )
    ]
    script = f"""
{aliases_source}
{register_source}
const registrations = [];
const replacements = [];
const app = {{
  registerPanel(definition) {{ registrations.push(definition); }},
  router: {{ replace(workspace, panel, params) {{ replacements.push([workspace, panel, params]); }} }},
}};
registerPanelAliases(app);
for (const definition of registrations) definition.activate();
process.stdout.write(JSON.stringify({{
  registrations: registrations.map((item) => [item.id, item.workspace, item.hiddenFromNav, item.requiresRoot]),
  replacements,
}}));
"""
    completed = subprocess.run(
        _node_eval_args(script),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)

    assert len(result["registrations"]) == 14
    assert all(item[2:] == [True, False] for item in result["registrations"])
    replacement_by_alias = {
        registered[0]: replacement
        for registered, replacement in zip(
            result["registrations"], result["replacements"], strict=True
        )
    }
    assert replacement_by_alias["models-github-backup"] == [
        "system",
        "system-status",
        {"section": "sec-github"},
    ]
    assert replacement_by_alias["models-compat-export"] == [
        "system",
        "system-status",
        {"section": "sec-backup"},
    ]
    assert replacement_by_alias["system-errors"] == [
        "system",
        "system-logs",
        {},
    ]


def test_bootstrap_uses_canonical_router_state_for_workspace_and_panel_tabs() -> None:
    source = _read(BOOTSTRAP)

    assert "app.router.onChange" in source
    assert "app.router.go" in source
    assert "app.onPanelRegistered" in source
    assert "data-unified-panel-tab" in source
    assert "aria-selected" in source
    assert "history" not in source or "app.router" in source


def test_mounted_dashboard_keeps_legacy_auth_and_api_requests_under_core_prefix() -> None:
    html = _read(DASHBOARD)

    assert "window.OmbreDashboardPathEnv = window.OmbreDashboardCore.createPathEnv" in html
    assert "const DASHBOARD_PATH = window.OmbreDashboardPathEnv" in html
    assert "const BASE = DASHBOARD_PATH.baseUrl" in html
    assert "const resolvedUrl = DASHBOARD_PATH.api(url);" in html
    assert "fetch('/auth" not in html
    assert "fetch(\"/auth" not in html
    assert "DASHBOARD_PATH.api('/auth/status')" in html
    assert 'href="./static/favicon.svg"' in html
    assert 'href="./static/icon.svg"' in html
    assert 'href="./static/manifest.json"' in html
    assert 'src="./static/icon.svg"' in html
    assert 'href="/static/' not in html
    assert 'src="/static/' not in html


def test_dashboard_file_preview_stops_before_auth_requests() -> None:
    html = _read(DASHBOARD)

    early_path_env_offset = html.index("window.OmbreDashboardPathEnv =")
    path_env_offset = html.index("const DASHBOARD_PATH =")
    file_mode_offset = html.index(
        "const DASHBOARD_FILE_MODE = DASHBOARD_PATH.isFilePreview === true"
    )
    check_auth_offset = html.index("async function checkAuth()")
    file_guard_offset = html.index(
        "if (DASHBOARD_FILE_MODE)", check_auth_offset
    )
    auth_request_offset = html.index(
        "fetch(DASHBOARD_PATH.api('/auth/status')", check_auth_offset
    )
    onboarding_cta_offset = html.index(
        "location.href=window.OmbreDashboardPathEnv.route('onboarding')"
    )

    assert early_path_env_offset < onboarding_cta_offset < path_env_offset
    assert path_env_offset < file_mode_offset < check_auth_offset
    assert check_auth_offset < file_guard_offset < auth_request_offset
    assert "showDashboardFilePreviewNotice();" in html[
        file_guard_offset:auth_request_offset
    ]
    assert "不能直接打开 dashboard.html" in html
    assert "http://localhost:18001/" in html


@pytest.mark.asyncio
async def test_dashboard_routes_version_every_relative_metadata_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = "9.8.7-test"
    monkeypatch.setattr(dashboard_web.sh, "repo_root", str(ROOT), raising=False)
    monkeypatch.setattr(dashboard_web.sh, "version", version, raising=False)
    mcp = _RouteMCP()
    dashboard_web.register(mcp)

    for route in ("/", "/memory-dashboard"):
        response = await mcp.routes[("GET", route)](object())
        html = response.body.decode("utf-8")

        assert response.status_code == 200
        assert response.headers["cache-control"] == (
            "no-cache, no-store, must-revalidate"
        )
        assert f'href="./static/favicon.svg?v={version}"' in html
        assert f'href="./static/icon.svg?v={version}"' in html
        assert f'href="./static/manifest.json?v={version}"' in html
        assert f'src="./static/icon.svg?v={version}"' in html


def test_web_manifest_uses_paths_relative_to_its_mounted_static_directory() -> None:
    manifest = json.loads(_read(WEB_MANIFEST))

    assert manifest["start_url"] == "../"
    assert manifest["icons"]
    assert all(not icon["src"].startswith("/") for icon in manifest["icons"])


def test_unified_boot_waits_for_auth_and_session_transitions_reload_safely() -> None:
    html = _read(DASHBOARD)
    source = _read(BOOTSTRAP)

    gate = "await global.OmbreDashboardAuthReady"
    assert gate in source
    assert source.index(gate) < source.index("createDashboardApp({")
    post_init_gate = "global.OmbreDashboardAuthenticated === false"
    assert post_init_gate in source
    assert source.index("await app.init();") < source.index(post_init_gate)
    assert source.index(post_init_gate) < source.index("global.OmbreDashboard = app;")
    assert "window.OmbreDashboardAuthReady = checkAuth();" in html
    assert "checkAuth().then" not in html
    for function_name in ("doSetup", "doRecover", "doLogin"):
        start = html.index(f"async function {function_name}")
        body = html[start : html.index("\n}", start) + 2]
        assert "completeDashboardAuthentication" in body
    logout_start = html.index("async function doLogout")
    logout_body = html[logout_start : html.index("\n}", logout_start) + 2]
    assert "clearAuthenticatedDashboardState" in logout_body
    assert "reloadDashboardSession" in logout_body
    assert "resp.status === 401" in logout_body
    assert "input[type=\"password\"]" in html
    assert "app.store.clear()" in html
    assert "await app.destroy()" in html
    assert "replaceChildren()" in html
    assert "whenDashboardAuthenticated(loadVersionBadge);" in html
    assert "setDashboardAuthenticatedInterval(refreshAnchorCounter, 30000);" in html
    assert "setDashboardAuthenticatedInterval(pollHeartbeat, 15000);" in html
    assert "setDashboardAuthenticatedInterval(pollCriticalErrors, 60000);" in html
    self_fab = html[html.index("async function initSelfFab") :]
    assert "await window.OmbreDashboardAuthReady" in self_fab


def test_authenticated_legacy_api_calls_share_the_401_teardown_path() -> None:
    html = _read(DASHBOARD)

    assert "fetch(BASE + '/api" not in html
    for function_name in ("runBreathDebug", "loadLetters"):
        start = html.index(f"async function {function_name}")
        end = html.index("\n}", start) + 2
        body = html[start:end]
        assert "await authFetch(url)" in body
        assert "await fetch(url)" not in body

    # Pre-authentication endpoints and explicitly public upstream lookups keep
    # using the browser fetch primitive; protected Dashboard APIs do not.
    assert "fetch(DASHBOARD_PATH.api('/auth/status')" in html
    assert "fetch('https://api.open-meteo.com/" in html
    assert "fetch('https://api.github.com/" in html


@pytest.mark.skipif(NODE is None, reason="Node.js is unavailable")
def test_unauthorized_teardown_reloads_if_followup_status_is_authenticated() -> None:
    html = _read(DASHBOARD)
    start = html.index("function reloadDashboardSession()")
    end = html.index("window.clearAuthenticatedDashboardState", start)
    lifecycle_source = html[start:end]
    script = r"""
const events = [];
const window = {
  location: { reload() { events.push('reload'); } },
};
async function clearAuthenticatedDashboardState() { events.push('clear'); }
async function checkAuth() { events.push('check'); return true; }
""" + lifecycle_source + r"""
(async () => {
  const result = await handleDashboardUnauthorized();
  process.stdout.write(JSON.stringify({
    events,
    result,
    authReadyIsPromise: Boolean(
      window.OmbreDashboardAuthReady
      && typeof window.OmbreDashboardAuthReady.then === 'function'
    ),
  }));
})().catch((error) => {
  process.stderr.write(String(error && error.stack || error));
  process.exitCode = 1;
});
"""
    completed = subprocess.run(
        _node_eval_args(script),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)

    assert result == {
        "events": ["clear", "check", "reload"],
        "result": True,
        "authReadyIsPromise": True,
    }


def test_header_search_routes_to_visible_shared_search_with_query_state() -> None:
    html = _read(DASHBOARD)
    source = _read(BOOTSTRAP)

    assert "routeDashboardSearch(q)" in html
    assert "app.commands.search" in source
    assert "app.router.go('shared', 'shared-search', { q: query })" in source
    assert "context.q" in source
    assert "return global.searchBuckets(query, context && context.signal)" in source
    assert "global.cancelBucketSearch" in source


def test_workspace_and_panel_tabs_use_accessible_roving_button_tabs() -> None:
    html = _read(DASHBOARD)
    source = _read(BOOTSTRAP)

    assert 'id="workspace-tabs" role="tablist"' in html
    assert 'id="panel-tabs" role="tablist"' in html
    assert '<button type="button" class="tab active"' in html
    assert '<div class="tab active"' not in html
    assert "document.createElement('button')" in source
    for attribute in ("aria-controls", "aria-labelledby", "aria-selected"):
        assert attribute in source
    for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
        assert key in source


def test_large_feature_workspaces_keep_panel_navigation_scrollable() -> None:
    css = _read(SHELL_STYLES)

    assert "#panel-tabs" in css
    assert "overflow-x: auto" in css
    assert "flex: 0 0 auto" in css


def test_moved_dehydration_editor_has_one_visible_canonical_location() -> None:
    html = _read(DASHBOARD)
    css = _read(SHELL_STYLES)

    assert 'data-unified-superseded-by="models-dehydration"' in html
    assert 'data-unified-canonical-link="models-dehydration"' in html
    assert 'data-unified-jump="models-dehydration"' in html
    assert "[data-unified-superseded-by]" in css
    assert "display: none !important" in css


def test_system_settings_cannot_submit_hidden_model_or_surfacing_editors() -> None:
    html = _read(DASHBOARD)

    assert 'data-unified-superseded-by="models-dehydration"' in html
    assert 'data-unified-superseded-by="models-embeddings"' in html
    assert 'data-unified-superseded-by="models-surfacing"' in html
    assert 'data-unified-canonical-link="models-embeddings"' in html
    assert 'data-unified-canonical-link="models-surfacing"' in html

    assert 'onclick="saveConfig(true)"' not in html
    assert "async function saveConfig(" not in html

    env_save = html[
        html.index("async function saveEnvConfig") : html.index(
            "async function _saveEnvKeys"
        )
    ]
    for stale_writer in (
        "OMBRE_COMPRESS_API_KEY",
        "OMBRE_COMPRESS_BASE_URL",
        "OMBRE_COMPRESS_MODEL",
        "OMBRE_EMBED_API_KEY",
        "OMBRE_EMBED_BASE_URL",
        "OMBRE_EMBED_MODEL",
    ):
        assert stale_writer not in env_save

    embedding_key_save = html[
        html.index("async function saveEmbedKey") : html.index(
            "async function saveEmbeddingConfig"
        )
    ]
    assert "authFetch('/api/config'" in embedding_key_save
    assert "embedding:" in embedding_key_save
    assert "persist_env: Boolean(key)" in embedding_key_save
    assert "api_key" in embedding_key_save
    assert "_saveEnvKeys" not in embedding_key_save
    assert "/api/env-config" not in embedding_key_save

    embedding_save = html[
        html.index("async function saveEmbeddingConfig") : html.index(
            "async function saveBucketDefaults"
        )
    ]
    assert "embedding:" in embedding_save
    for unrelated_resource in ("dehydration:", "surfacing:", "merge_threshold"):
        assert unrelated_resource not in embedding_save

    bucket_start = html.index("async function saveBucketDefaults")
    bucket_save = html[
        bucket_start : html.index(
            "window.OmbreDashboardAuthReady = checkAuth();", bucket_start
        )
    ]
    assert "merge_threshold" in bucket_save
    for unrelated_resource in ("dehydration:", "embedding:", "surfacing:"):
        assert unrelated_resource not in bucket_save


def test_embedding_editor_is_owned_and_mounted_by_models_data() -> None:
    html = _read(DASHBOARD)
    models_css = _read(ASSETS / "models-data.css")

    marker = 'data-canonical-editor-resource="embedding-settings"'
    assert html.count(marker) == 1
    assert 'data-canonical-editor-panel="models-embeddings"' in html
    assert 'data-canonical-editor-mounted="false"' in html
    assert 'onclick="saveEmbeddingConfig(true)"' in html
    assert '[data-canonical-editor-resource="embedding-settings"]' in models_css
    assert '[data-canonical-editor-mounted="true"]' in models_css
