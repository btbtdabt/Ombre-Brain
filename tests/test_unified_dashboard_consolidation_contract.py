from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DASHBOARD = FRONTEND / "dashboard.html"
ASSETS = FRONTEND / "dashboard-assets"
SHELL = ASSETS / "unified-shell.js"
SHARED_BUCKETS = ASSETS / "shared-bucket-studio.js"
MEMORY_PROFILE = ASSETS / "memory-profile.js"
MODELS_DATA = ASSETS / "models-data.js"
P0_SURFACE_STYLES = ASSETS / "p0-dashboard-contract.css"
META_API = ROOT / "src" / "web" / "meta.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _panel_definition(source: str, panel_id: str) -> str:
    marker = f"id: '{panel_id}'"
    start = source.index(marker)
    end = source.find("app.registerPanel({", start + len(marker))
    return source[start:] if end < 0 else source[start:end]


def _about_contract(html: str) -> str:
    start = html.index("async function loadAbout()")
    end = html.index("// Re-render Lucide icons", start)
    return html[start:end]


def test_shared_workspace_has_one_visible_buckets_tab_with_two_modes() -> None:
    html = _read(DASHBOARD)
    shared_source = _read(SHARED_BUCKETS)

    assert html.count('data-panel-id="shared-buckets"') == 1
    assert 'data-panel-id="shared-bucket-studio"' not in html
    assert 'id="dashboard-tab-shared-bucket-studio"' not in html

    shared_tab = re.search(
        r'<button[^>]+data-panel-id="shared-buckets"[^>]*>(.*?)</button>',
        html,
        re.DOTALL,
    )
    assert shared_tab is not None
    assert "记忆桶" in shared_tab.group(1)
    assert "Buckets" in shared_tab.group(1)

    mode_surface = html + "\n" + shared_source
    assert 'data-bucket-mode="basic"' in mode_surface
    assert 'data-bucket-mode="advanced"' in mode_surface


def test_persona_panels_have_unambiguous_state_and_settings_labels() -> None:
    memory_persona = _panel_definition(_read(MEMORY_PROFILE), "memory-persona-state")
    models_persona = _panel_definition(_read(MODELS_DATA), "models-persona")

    assert "label: 'Persona State'" in memory_persona
    assert "label: 'Persona Settings'" in models_persona


def test_about_exposes_p0luz_faq_and_usage_guidance() -> None:
    about = _about_contract(_read(DASHBOARD)) + "\n" + _read(META_API)

    assert "P0luz/Ombre-Brain" in about
    assert re.search(r"FAQ|常见问题", about, re.IGNORECASE)
    assert re.search(r"Usage Guide|使用指南|README", about, re.IGNORECASE)
    assert re.search(r"href|url", about, re.IGNORECASE)
    assert "safeHttpsUrl(d.ifdian)" in about
    assert "supportUrl" in about


def test_all_registered_panels_receive_the_final_p0_surface_contract() -> None:
    html = _read(DASHBOARD)
    shell = _read(SHELL)

    stylesheet_hrefs = [
        match.group(1)
        for tag in re.findall(
            r'<link\b[^>]*rel="stylesheet"[^>]*>', html, re.IGNORECASE
        )
        if (match := re.search(r'href="([^"]+)"', tag, re.IGNORECASE))
    ]
    assert stylesheet_hrefs
    assert stylesheet_hrefs[-1].split("?", 1)[0].endswith(
        "dashboard-assets/p0-dashboard-contract.css"
    )
    assert P0_SURFACE_STYLES.is_file()

    resolve_start = shell.index("function resolvePanelRoot")
    resolve_end = shell.index("function registerLegacyPanels", resolve_start)
    resolve_panel_root = shell[resolve_start:resolve_end]
    assert re.search(
        r"existing\.classList\.add\(['\"]p0-panel-surface['\"]\)",
        resolve_panel_root,
    )
    assert re.search(
        r"section\.class(?:Name|List)[^\n]*p0-panel-surface", resolve_panel_root
    )

    css = _read(P0_SURFACE_STYLES)
    for selector in (
        ".p0-panel-surface",
        ".p0-panel-surface .ob-panel-header",
        ".p0-panel-surface .ob-card",
        ".p0-panel-surface .shared-bucket-studio__hero",
        ".p0-panel-surface .config-section",
    ):
        assert selector in css


def test_p0_contract_remains_last_after_feature_factories_append_styles() -> None:
    html = _read(DASHBOARD)
    shell = _read(SHELL)

    assert 'id="p0-dashboard-contract-style"' in html
    assert "function keepP0ContractLast" in shell
    assert (
        "document.addEventListener('ombre-dashboard-features-loaded', "
        "keepP0ContractLast)"
    ) in shell

    helper_start = shell.index("function keepP0ContractLast")
    helper_end = shell.index("function reportFatal", helper_start)
    helper = shell[helper_start:helper_end]
    assert "p0-dashboard-contract-style" in helper
    assert "document.head.appendChild" in helper
