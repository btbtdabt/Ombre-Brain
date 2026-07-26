from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "frontend" / "dashboard.html"


def _dashboard_section(start_marker: str, end_marker: str) -> str:
    html = DASHBOARD.read_text(encoding="utf-8")
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


def test_auth_success_paths_reload_through_one_session_boundary() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    completion = _dashboard_section(
        "async function completeDashboardAuthentication()",
        "async function handleDashboardUnauthorized()",
    )

    assert "clearDashboardPasswordInputs();" in completion
    assert "window.OmbreDashboardAuthenticated = true;" in completion
    assert "reloadDashboardSession();" in completion

    for start, end in (
        ("async function doSetup()", "function showLogin()"),
        ("async function doRecover()", "async function doLogin()"),
        ("async function doLogin()", "async function doLogout()"),
    ):
        source = _dashboard_section(start, end)
        assert "await completeDashboardAuthentication();" in source

    assert "window.OmbreDashboardAuthReady = checkAuth();" in html
    assert "checkAuth().then" not in html


def test_authenticated_work_stays_behind_the_authentication_promise() -> None:
    chrome = _dashboard_section(
        "whenDashboardAuthenticated(function startAuthenticatedChrome()",
        "// 更新/换机后发现一条记忆都没有",
    )
    polling = _dashboard_section(
        "whenDashboardAuthenticated(function startAuthenticatedPolling()",
        "// ── 给作者反馈",
    )
    self_fab = _dashboard_section(
        "(async function initSelfFab()",
        "</script>",
    )

    for call in (
        "syncRestartRequirement();",
        "refreshAnchorCounter();",
        "checkEmptyMemoryBanner();",
        "loadOwnerBadge();",
    ):
        assert call in chrome
    assert "setDashboardAuthenticatedInterval(refreshAnchorCounter, 30000);" in chrome

    for call in (
        "pollHeartbeat();",
        "pollCriticalErrors();",
        "maybeShowOnboarding();",
    ):
        assert call in polling
    assert "setDashboardAuthenticatedInterval(pollHeartbeat, 15000);" in polling
    assert "setDashboardAuthenticatedInterval(pollCriticalErrors, 60000);" in polling

    assert "await window.OmbreDashboardAuthReady" in self_fab
    assert "window.OmbreDashboardAuthenticated !== true" in self_fab


def test_auth_status_check_bypasses_browser_cache() -> None:
    check_auth = _dashboard_section(
        "async function checkAuth()",
        "async function doSetup()",
    )

    assert "cache: 'no-store'" in check_auth
    assert "signal: controller.signal" in check_auth


def test_session_teardown_is_reusable_and_invalidates_sensitive_state() -> None:
    teardown = _dashboard_section(
        "async function clearAuthenticatedDashboardState()",
        "function reloadDashboardSession()",
    )

    assert "if (dashboardSessionResetPromise) return dashboardSessionResetPromise;" in teardown
    assert "window.OmbreDashboardAuthenticated = false;" in teardown
    assert "app.store.clear()" in teardown
    assert "await app.destroy()" in teardown
    assert "replaceChildren();" in teardown
    assert "finally" in teardown
    assert "dashboardSessionResetPromise = null;" in teardown
