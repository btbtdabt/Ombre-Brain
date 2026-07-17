from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_both_dashboard_feature_sets_ship_together() -> None:
    system_html = (ROOT / "frontend" / "dashboard.html").read_text(encoding="utf-8")
    memory_html = (ROOT / "frontend" / "memory-dashboard.html").read_text(
        encoding="utf-8"
    )

    assert "/api/github/status" in system_html
    assert "/api/embedding/info" in system_html
    assert "/api/gateway-injections?limit" in memory_html
    assert "/api/portrait-state" in memory_html
    assert "/api/persona?events_limit" in memory_html


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
    assert 'RedirectResponse(url="/memory-dashboard", status_code=302)' in config_routes
