from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_p0_and_ying_feature_sets_ship_in_one_dashboard_shell() -> None:
    system_html = (ROOT / "frontend" / "dashboard.html").read_text(encoding="utf-8")
    memory_html = (ROOT / "frontend" / "memory-dashboard.html").read_text(
        encoding="utf-8"
    )
    asset_root = ROOT / "frontend" / "dashboard-assets"
    modular_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(asset_root.glob("*.js"))
    )

    assert "/api/github/status" in system_html
    assert "/api/embedding/info" in system_html
    assert "/api/gateway-injections" in modular_source
    assert "/api/portrait-state" in modular_source
    assert "/api/persona" in modular_source
    assert 'data-dashboard-shell="unified-dashboard"' in system_html
    assert 'data-dashboard-shell="unified-dashboard"' in memory_html
    assert "function authFetch" not in memory_html
    assert len(memory_html) < 4_096


def test_memory_dashboard_module_and_routes_are_packaged() -> None:
    dashboard_routes = (ROOT / "src" / "web" / "dashboard.py").read_text(
        encoding="utf-8"
    )
    config_routes = (ROOT / "src" / "web" / "config_api.py").read_text(
        encoding="utf-8"
    )

    assert (ROOT / "frontend" / "dashboard-assets" / "chat-memory.js").is_file()
    assert '"/memory-dashboard"' in dashboard_routes
    assert '"/dashboard-assets/{name}"' in dashboard_routes
    assert 'target = "memory-dashboard"' in config_routes
    assert "request.url.query" in config_routes
    assert "RedirectResponse(url=target, status_code=302)" in config_routes
