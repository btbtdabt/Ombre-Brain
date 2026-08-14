import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "frontend" / "dashboard.html"


def _node_executable() -> str:
    executable = shutil.which("node")
    assert executable is not None
    return executable


def _dashboard_section(start_marker: str, end_marker: str) -> str:
    html = DASHBOARD.read_text(encoding="utf-8")
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


def _run_node(script: str) -> dict:
    completed = subprocess.run(
        [_node_executable(), "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def test_auth_success_paths_reload_through_one_verified_session_boundary() -> None:
    html = DASHBOARD.read_text(encoding="utf-8")
    completion = _dashboard_section(
        "async function completeDashboardAuthentication()",
        "async function handleDashboardUnauthorized()",
    )

    assert "clearDashboardPasswordInputs();" in completion
    assert "DASHBOARD_PATH.api('/auth/status')" in completion
    assert "cache: 'no-store'" in completion
    assert "credentials: 'same-origin'" in completion
    assert "data.authenticated === true" in completion
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


def test_login_failure_uses_author_proxy_diagnostics() -> None:
    login = _dashboard_section(
        "async function doLogin()",
        "async function doLogout()",
    )

    assert "credentials: 'same-origin'" in login
    assert "cache: 'no-store'" in login
    assert "readAuthFailure(resp" in login
    assert "登录请求未到达 OB" in login


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


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_setup_form_warns_only_when_not_on_a_loopback_host():
    section = _dashboard_section(
        "function _isLikelyLoopbackHost()", "function showAuthError"
    )

    def _remote_warning_display(hostname: str) -> str:
        script = f"""
const window = {{ location: {{ hostname: {json.dumps(hostname)} }} }};
const DASHBOARD_PATH = {{api(path) {{ return path; }}}};
const DASHBOARD_FILE_MODE = false;
let _dashboardAuthGeneration = 0;
const elements = new Map();
const document = {{
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, {{textContent:'', style:{{}}}});
    return elements.get(id);
  }},
}};
function invalidateAuthenticatedDashboardSession() {{}}
async function fetch(url, options) {{
  return {{ ok: true, status: 200, async json() {{ return {{authenticated:false, setup_needed:true}}; }} }};
}}
""" + section + r"""

(async function() {
  await checkAuth();
  const el = document.getElementById('auth-setup-remote-warning');
  process.stdout.write(JSON.stringify({display: el.style.display}));
})().catch(error => {
  console.error(error);
  process.exit(1);
});
"""
        completed = subprocess.run(
            [_node_executable(), "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)["display"]

    assert _remote_warning_display("ombre.example.com") == "block"
    assert _remote_warning_display("localhost") == "none"
    assert _remote_warning_display("127.0.0.1") == "none"
    assert _remote_warning_display("127.5.6.7") == "none"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_setup_failure_explains_the_local_only_restriction():
    setup_source = _dashboard_section(
        "function showAuthError(msg)", "function showLogin()"
    )
    backend_error = (
        "Initial password setup is local-only. Set OMBRE_DASHBOARD_PASSWORD "
        "before public deployment, or supply X-Ombre-Setup-Token matching "
        "OMBRE_SETUP_TOKEN."
    )
    script = f"""
const elements = new Map([
  ['auth-setup-pwd', {{value:'a-real-password', textContent:'', style:{{}}}}],
  ['auth-setup-pwd2', {{value:'a-real-password', textContent:'', style:{{}}}}],
  ['auth-error', {{value:'', textContent:'', style:{{}}}}],
]);
const DASHBOARD_PATH = {{api(path) {{ return path; }}}};
const document = {{
  getElementById(id) {{
    return elements.has(id) ? elements.get(id) : null;
  }},
}};
async function fetch(url) {{
  if (url !== '/auth/setup') throw new Error('unexpected fetch: ' + url);
  return {{
    ok: false,
    status: 403,
    async json() {{ return {{error: {json.dumps(backend_error)}}}; }},
  }};
}}
""" + setup_source + r"""

(async function() {
  await doSetup();
  const error = elements.get('auth-error');
  process.stdout.write(JSON.stringify({text: error.textContent}));
})().catch(error => {
  console.error(error);
  process.exit(1);
});
"""
    completed = subprocess.run(
        [_node_executable(), "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)

    assert "OMBRE_DASHBOARD_PASSWORD" in result["text"]
    assert "OMBRE_SETUP_TOKEN" in result["text"]
    assert "X-Ombre-Setup-Token" in result["text"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_login_failure_displays_backend_error_message():
    auth_source = (
        _dashboard_section("function showAuthError(msg)", "async function doSetup()")
        + _dashboard_section("async function doLogin()", "async function doLogout()")
    )
    expected_error = "登录服务繁忙，请 17 秒后重试"
    script = r"""
const elements = new Map([
  ['auth-login-pwd', {value:'wrong-password', textContent:'', style:{}}],
  ['auth-error', {value:'', textContent:'', style:{}}],
]);
const DASHBOARD_PATH = {api(path) { return path; }};
const document = {
  getElementById(id) {
    if (!elements.has(id)) throw new Error('unexpected element: ' + id);
    return elements.get(id);
  },
};
async function fetch(url) {
  if (url !== '/auth/login') throw new Error('unexpected fetch: ' + url);
  return {
    ok: false,
    async json() { return {error:'登录服务繁忙，请 17 秒后重试'}; },
  };
}
""" + auth_source + r"""

(async function() {
  await doLogin();
  const error = elements.get('auth-error');
  process.stdout.write(JSON.stringify({
    textContent: error.textContent,
    display: error.style.display,
  }));
})().catch(error => {
  console.error(error);
  process.exit(1);
});
"""
    completed = subprocess.run(
        [_node_executable(), "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)
    assert result["textContent"] == expected_error
    assert result["display"] == "block"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_login_failure_reports_non_json_proxy_response() -> None:
    auth_source = (
        _dashboard_section("function showAuthError(msg)", "async function doSetup()")
        + _dashboard_section("async function doLogin()", "async function doLogout()")
    )
    script = r"""
const elements = new Map([
  ['auth-login-pwd', {value:'correct-password', textContent:'', style:{}}],
  ['auth-error', {value:'', textContent:'', style:{}}],
]);
const document = {getElementById(id) { return elements.get(id); }};
const DASHBOARD_PATH = {api(path) { return path; }};
async function fetch(url) {
  if (url !== '/auth/login') throw new Error('unexpected fetch: ' + url);
  return {ok:false, status:502, async json() { throw new Error('nginx returned HTML'); }};
}
""" + auth_source + r"""
(async function() {
  await doLogin();
  const error = elements.get('auth-error');
  process.stdout.write(JSON.stringify({textContent:error.textContent, display:error.style.display}));
})().catch(error => { console.error(error); process.exit(1); });
"""

    assert _run_node(script) == {
        "textContent": (
            "登录请求失败（HTTP 502）：反向代理未返回 OB 的 JSON 响应，"
            "请检查 nginx 是否完整转发 /auth/*。"
        ),
        "display": "block",
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_login_success_requires_cookie_backed_session_before_reload() -> None:
    auth_source = _dashboard_section(
        "function reloadDashboardSession()",
        "async function handleDashboardUnauthorized()",
    ) + _dashboard_section("function showAuthError(msg)", "async function readAuthFailure")
    script = r"""
let reloads = 0;
const window = {
  OmbreDashboardAuthenticated: null,
  location: {reload() { reloads += 1; }},
};
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {textContent:'', style:{}});
  return elements.get(id);
}
const document = {body:{dataset:{}}, getElementById:element};
const DASHBOARD_PATH = {api(path) { return path; }};
function clearDashboardPasswordInputs() {}
async function fetch(url) {
  if (url !== '/auth/status') throw new Error('unexpected fetch: ' + url);
  return {ok:true, async json() { return {authenticated:false}; }};
}
""" + auth_source + r"""
(async function() {
  const authenticated = await completeDashboardAuthentication();
  process.stdout.write(JSON.stringify({
    authenticated,
    reloads,
    dashboardAuthenticated: window.OmbreDashboardAuthenticated,
    bodyAuthenticated: document.body.dataset.authenticated,
    error: element('auth-error').textContent,
    errorDisplay: element('auth-error').style.display,
    overlayDisplay: element('auth-overlay').style.display,
  }));
})().catch(error => { console.error(error); process.exit(1); });
"""

    assert _run_node(script) == {
        "authenticated": False,
        "reloads": 0,
        "dashboardAuthenticated": False,
        "bodyAuthenticated": "false",
        "error": (
            "验证已通过，但浏览器未建立登录会话。请检查 nginx 的 Host、"
            "X-Forwarded-Proto 与 Set-Cookie 转发。"
        ),
        "errorDisplay": "block",
        "overlayDisplay": "flex",
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_verified_login_session_reloads_exactly_once() -> None:
    auth_source = _dashboard_section(
        "function reloadDashboardSession()",
        "async function handleDashboardUnauthorized()",
    ) + _dashboard_section("function showAuthError(msg)", "async function readAuthFailure")
    script = r"""
let reloads = 0;
let passwordsCleared = 0;
const window = {
  OmbreDashboardAuthenticated: null,
  location: {reload() { reloads += 1; }},
};
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {textContent:'', style:{}});
  return elements.get(id);
}
const document = {body:{dataset:{}}, getElementById:element};
const DASHBOARD_PATH = {api(path) { return path; }};
function clearDashboardPasswordInputs() { passwordsCleared += 1; }
async function fetch(url) {
  if (url !== '/auth/status') throw new Error('unexpected fetch: ' + url);
  return {ok:true, async json() { return {authenticated:true}; }};
}
""" + auth_source + r"""
(async function() {
  const authenticated = await completeDashboardAuthentication();
  process.stdout.write(JSON.stringify({
    authenticated,
    reloads,
    passwordsCleared,
    dashboardAuthenticated: window.OmbreDashboardAuthenticated,
    bodyAuthenticated: document.body.dataset.authenticated,
    overlayDisplay: element('auth-overlay').style.display,
  }));
})().catch(error => { console.error(error); process.exit(1); });
"""

    assert _run_node(script) == {
        "authenticated": True,
        "reloads": 1,
        "passwordsCleared": 1,
        "dashboardAuthenticated": True,
        "bodyAuthenticated": "true",
        "overlayDisplay": "none",
    }
