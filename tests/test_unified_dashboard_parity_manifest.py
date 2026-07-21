from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tests.compat_web_current.conftest import RecordingMCP, request_for
from web import dashboard as dashboard_routes
from web.current_contract import CURRENT_ROUTE_SPECS


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = ROOT / "frontend"
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "dashboard_parity_manifest.json"
ROOT_DASHBOARD_PATH = FRONTEND_ROOT / "dashboard.html"
MEMORY_DASHBOARD_PATH = FRONTEND_ROOT / "memory-dashboard.html"
SHARED_BUCKET_STUDIO_PATH = FRONTEND_ROOT / "dashboard-assets" / "shared-bucket-studio.js"
MODELS_DATA_PATH = FRONTEND_ROOT / "dashboard-assets" / "models-data.js"


def _load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _dashboard_tab_ids(html: str) -> set[str]:
    return set(re.findall(r'data-tab="([^"]+)"', html))


def _function_occurrences(name: str) -> int:
    pattern = re.compile(rf"\b(?:async\s+)?function\s+{re.escape(name)}\s*\(")
    count = 0
    for path in (
        ROOT_DASHBOARD_PATH,
        MEMORY_DASHBOARD_PATH,
        *sorted((FRONTEND_ROOT / "dashboard-assets").glob("*.js")),
    ):
        count += len(pattern.findall(_read(path)))
    return count


def _panel_ids(manifest: dict[str, Any]) -> set[str]:
    return {panel["id"] for panel in manifest["panels"]}  # type: ignore[index]


def test_dashboard_parity_manifest_is_internally_consistent() -> None:
    manifest = _load_manifest()
    workspaces = manifest["workspaces"]
    panels = manifest["panels"]
    entry_routes = manifest["entry_routes"]
    family_coverage = manifest["current_route_family_coverage"]
    current_families = manifest["current_route_families"]
    canonical_editors = manifest["canonical_editors"]
    legacy_aliases = manifest["legacy_state_aliases"]
    legacy_panel_aliases = manifest["legacy_panel_aliases"]
    shared_actions = manifest["shared_route_action_coverage"]

    workspace_ids = [workspace["id"] for workspace in workspaces]  # type: ignore[index]
    panel_ids = [panel["id"] for panel in panels]  # type: ignore[index]
    family_ids = [family["id"] for family in current_families]  # type: ignore[index]
    panel_id_set = set(panel_ids)

    assert manifest["shell_id"] == "unified-dashboard"
    assert len(workspace_ids) == len(set(workspace_ids))
    assert len(panel_ids) == len(panel_id_set)
    assert len(family_ids) == len(set(family_ids))
    assert {route["shell_id"] for route in entry_routes} == {"unified-dashboard"}  # type: ignore[index]

    for panel in panels:  # type: ignore[assignment]
        assert panel["workspace"] in workspace_ids
        assert panel["kind"] in {"canonical", "distinct"}

    for alias in legacy_aliases:  # type: ignore[assignment]
        assert alias["panel"] in panel_id_set
        assert alias["kind"] in {"hash", "route", "tab"}

    panel_alias_states = [alias["state"] for alias in legacy_panel_aliases]
    assert len(panel_alias_states) == len(set(panel_alias_states))
    for alias in legacy_panel_aliases:  # type: ignore[assignment]
        assert alias["state"] not in panel_id_set
        assert alias["workspace"] in workspace_ids
        assert alias["panel"] in panel_id_set

    resource_counter = Counter(
        editor["resource"] for editor in canonical_editors  # type: ignore[index]
    )
    assert not [resource for resource, count in resource_counter.items() if count > 1]
    for editor in canonical_editors:  # type: ignore[assignment]
        assert editor["panel"] in panel_id_set

    owned_model_editors = {
        editor["resource"]: editor
        for editor in canonical_editors  # type: ignore[assignment]
        if editor.get("writer")
        in {"models-data-config", "models-embedding-config"}
    }
    assert set(owned_model_editors) == {
        "dehydration-settings",
        "embedding-settings",
        "memory-surfacing-settings",
    }
    models_source = _read(MODELS_DATA_PATH)
    dashboard_source = _read(ROOT_DASHBOARD_PATH)
    for editor in owned_model_editors.values():
        assert editor["panel"] in models_source
        assert editor["marker"] in models_source or editor["marker"] in dashboard_source
        if editor["writer"] == "models-data-config":
            assert (
                f"'{editor['panel']}': '{editor['resource']}'" in models_source
            )
        else:
            assert editor["writer"] == "models-embedding-config"
            assert (
                f'data-canonical-editor-resource="{editor["resource"]}"'
                in dashboard_source
            )
            assert (
                f'data-canonical-editor-panel="{editor["panel"]}"'
                in dashboard_source
            )
            embedding_writer = dashboard_source[
                dashboard_source.index("async function saveEmbedKey") :
                dashboard_source.index("async function saveEmbeddingConfig")
            ]
            assert "authFetch('/api/config'" in embedding_writer
            assert "persist_env: Boolean(key)" in embedding_writer
            assert "/api/env-config" not in embedding_writer

    assert dashboard_source.count(
        'data-canonical-editor-resource="embedding-settings"'
    ) == 1
    assert (
        'data-canonical-editor-panel="models-embeddings"' in dashboard_source
    )
    assert "markCanonicalEditor(root, panelId)" in models_source

    action_keys = [
        (item["method"], item["path"], item["action"])
        for item in shared_actions  # type: ignore[assignment]
    ]
    assert len(action_keys) == len(set(action_keys))
    for item in shared_actions:  # type: ignore[assignment]
        assert item["panel"] == "shared-bucket-studio"
        assert item["panel"] in panel_id_set
        assert item["control"] in {"action", "submit"}

    current_contract_by_family = {
        family_id: sorted(
            (spec.method, spec.path)
            for spec in CURRENT_ROUTE_SPECS
            if spec.family == family_id
        )
        for family_id in family_ids
    }
    manifest_contract_by_family = {
        family["id"]: sorted(
            (route["method"], route["path"]) for route in family["route_templates"]
        )
        for family in current_families  # type: ignore[assignment]
    }
    assert manifest_contract_by_family == current_contract_by_family
    assert set(family_coverage) == set(family_ids)
    for panel_refs in family_coverage.values():  # type: ignore[assignment]
        assert panel_refs
        for panel_id in panel_refs:
            assert panel_id in panel_id_set


def test_shared_bucket_route_parity_has_a_reachable_control_per_operation() -> None:
    manifest = _load_manifest()
    source = _read(SHARED_BUCKET_STUDIO_PATH)
    coverage = manifest["shared_route_action_coverage"]
    required_current_routes = {
        ("GET", "/api/buckets/light"),
        ("POST", "/api/memories"),
        ("PATCH", "/api/bucket/{bucket_id}"),
        ("POST", "/api/buckets/delete"),
        ("POST", "/api/buckets/bulk-update"),
        ("POST", "/api/bucket/{bucket_id}/comments"),
        ("DELETE", "/api/bucket/{bucket_id}/comments/{comment_id}"),
        ("GET", "/api/moments"),
        ("POST", "/api/ingest-raw"),
        ("GET", "/api/search-raw"),
        ("POST", "/api/search-raw"),
        ("GET", "/api/edges"),
        ("GET", "/api/domain-taxonomy"),
    }
    covered_routes = {(item["method"], item["path"]) for item in coverage}

    assert required_current_routes <= covered_routes
    for item in coverage:
        marker = f'data-{item["control"]}="{item["action"]}"'
        assert marker in source, f"missing reachable control for {item['method']} {item['path']}"
        assert item["source_token"] in source


@pytest.mark.asyncio
async def test_unified_entry_routes_expect_one_shell_contract_marker() -> None:
    manifest = _load_manifest()
    shell_marker = f'data-dashboard-shell="{manifest["shell_id"]}"'
    workspace_markers = {
        f'data-workspace="{workspace["id"]}"'
        for workspace in manifest["workspaces"]  # type: ignore[index]
    }

    mcp = RecordingMCP()
    dashboard_routes.register(mcp)

    root_response = await mcp.routes[("GET", "/")](request_for("GET", "/"))
    memory_response = await mcp.routes[("GET", "/memory-dashboard")](
        request_for("GET", "/memory-dashboard")
    )

    root_html = root_response.body.decode("utf-8")
    memory_html = memory_response.body.decode("utf-8")

    assert shell_marker in root_html
    assert shell_marker in memory_html
    for marker in workspace_markers:
        assert marker in root_html
        assert marker in memory_html


def test_root_dashboard_declares_unified_workspace_navigation() -> None:
    manifest = _load_manifest()
    root_html = _read(ROOT_DASHBOARD_PATH)

    assert f'data-dashboard-shell="{manifest["shell_id"]}"' in root_html
    for workspace in manifest["workspaces"]:  # type: ignore[assignment]
        marker = f'data-workspace="{workspace["id"]}"'
        assert marker in root_html


def test_system_has_one_settings_tab_and_status_banner_is_not_global() -> None:
    html = _read(ROOT_DASHBOARD_PATH)

    assert html.count('id="dashboard-tab-system-status"') == 1
    for removed_tab in (
        'dashboard-tab-system-errors',
        'dashboard-tab-system-identity-settings',
        'dashboard-tab-system-auth-settings',
        'dashboard-tab-system-mcp-settings',
        'dashboard-tab-system-transport-settings',
        'dashboard-tab-system-env-settings',
        'dashboard-tab-system-tunnel-settings',
        'dashboard-tab-system-diagnostics',
        'dashboard-tab-system-version-update',
        'dashboard-tab-system-restart-controls',
        'dashboard-tab-system-developer',
    ):
        assert removed_tab not in html
    settings_index = html.index('id="settings-view"')
    banner_index = html.index('id="status-banner"')
    first_settings_section = html.index('id="sec-version"', settings_index)
    assert settings_index < banner_index < first_settings_section
    assert "banner.classList.add('open')" not in html


def test_memory_dashboard_is_not_a_second_full_application_after_cutover() -> None:
    manifest = _load_manifest()
    memory_html = _read(MEMORY_DASHBOARD_PATH)
    shared_aliases = {
        alias["state"]
        for alias in manifest["legacy_state_aliases"]  # type: ignore[index]
        if alias["kind"] == "tab" and alias["panel"].startswith("shared-")
    }
    memory_tab_ids = _dashboard_tab_ids(memory_html)

    assert f'data-dashboard-shell="{manifest["shell_id"]}"' in memory_html
    assert not (memory_tab_ids & shared_aliases)


@pytest.mark.parametrize("symbol", _load_manifest()["shared_core_symbols"])
def test_shared_core_symbols_exist_once_across_dashboard_frontends(symbol: str) -> None:
    assert _function_occurrences(symbol) == 1
