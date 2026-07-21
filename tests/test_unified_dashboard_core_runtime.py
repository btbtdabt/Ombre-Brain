from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "frontend" / "dashboard-assets" / "core"
DASHBOARD = ROOT / "frontend" / "dashboard.html"
UNIFIED_SHELL = ROOT / "frontend" / "dashboard-assets" / "unified-shell.js"


def _source(name: str) -> str:
    return (CORE / name).read_text(encoding="utf-8")


def _run_node(script: str) -> dict[str, Any]:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for Dashboard core behavior tests")
    completed = subprocess.run(
        [node, "-"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=script,
    )
    return json.loads(completed.stdout)


def test_path_api_and_router_preserve_safety_and_legacy_contracts() -> None:
    sources = {
        name: _source(name)
        for name in ("path.js", "api.js", "router.js")
    }
    script = f"""
const vm = require('vm');
const sources = {json.dumps(sources)};
const window = {{
  location: {{
    origin: 'https://brain.example',
    pathname: '/ombre/memory-dashboard',
    search: '',
    hash: '',
  }},
  history: {{ pushState() {{}}, replaceState() {{}} }},
  addEventListener() {{}},
  removeEventListener() {{}},
  setTimeout,
  clearTimeout,
  URL,
  URLSearchParams,
  Headers,
  FormData,
  Blob,
  ArrayBuffer,
  AbortController,
  DOMException,
  Response,
}};
window.window = window;
vm.createContext(window);
for (const name of ['path.js', 'api.js', 'router.js']) {{
  vm.runInContext(sources[name], window, {{ filename: name }});
}}

(async () => {{
  const core = window.OmbreDashboardCore;
  const env = core.createPathEnv({{
    pathname: '/ombre/memory-dashboard',
    origin: 'https://brain.example',
  }});
  const blocked = [];
  for (const path of ['../auth', '%2e%2e/auth', 'https://evil.example/api']) {{
    try {{ env.api(path); }} catch (error) {{ blocked.push(error.name); }}
  }}

  let getCalls = 0;
  const retryClient = core.createApiClient({{
    pathEnv: env,
    retryDelayMs: 0,
    fetchImpl: async () => {{
      getCalls += 1;
      if (getCalls === 1) throw new TypeError('network');
      return new Response('{{"ok":true}}', {{ status: 200 }});
    }},
  }});
  const getStatus = (await retryClient.get('/api/buckets')).status;

  let writeCalls = 0;
  const writeClient = core.createApiClient({{
    pathEnv: env,
    retryDelayMs: 0,
    fetchImpl: async () => {{ writeCalls += 1; throw new TypeError('network'); }},
  }});
  await writeClient.post('/api/config', {{ enabled: true }}, {{ retries: 9 }}).catch(() => {{}});

  let abortCalls = 0;
  const abortClient = core.createApiClient({{
    pathEnv: env,
    fetchImpl: async (_url, init) => {{
      abortCalls += 1;
      if (init.signal.aborted) throw new DOMException('Aborted', 'AbortError');
      return new Response('{{}}', {{ status: 200 }});
    }},
  }});
  const controller = new AbortController();
  controller.abort();
  const abortName = await abortClient.get('/api/buckets', {{ signal: controller.signal }})
    .then(() => 'resolved', (error) => error.name);

  const pathEnv = core.createPathEnv({{ pathname: '/', origin: 'https://brain.example' }});
  const letters = core.createRouter({{
    pathEnv,
    location: {{ pathname: '/', search: '', hash: '#letters' }},
    history: {{}},
    eventTarget: {{}},
  }}).current();
  const reminders = core.createRouter({{
    pathEnv,
    location: {{ pathname: '/', search: '?tab=todos', hash: '' }},
    history: {{}},
    eventTarget: {{}},
  }}).current();
  const unknown = core.createRouter({{
    pathEnv,
    location: {{ pathname: '/', search: '?panel=memory-not-real', hash: '' }},
    history: {{}},
    eventTarget: {{}},
  }}).current();
  const mountedSearch = core.createRouter({{
    pathEnv: env,
    manifest: {{ panels: [{{ id: 'shared-search', workspace: 'shared' }}] }},
    location: {{
      pathname: '/ombre/memory-dashboard',
      search: '?workspace=shared&panel=shared-search&q=Amy%20Aki',
      hash: '',
    }},
    history: {{}},
    eventTarget: {{}},
  }}).current();

  process.stdout.write(JSON.stringify({{
    basePath: env.basePath,
    bootMode: env.bootMode,
    apiUrl: env.api('/api/buckets'),
    blocked,
    getCalls,
    getStatus,
    writeCalls,
    abortCalls,
    abortName,
    letters,
    reminders,
    unknown,
    mountedSearch,
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
    result = _run_node(script)

    assert result["basePath"] == "/ombre"
    assert result["bootMode"] == "memory"
    assert result["apiUrl"] == "https://brain.example/ombre/api/buckets"
    assert result["blocked"] == ["TypeError", "TypeError", "TypeError"]
    assert result["getCalls"] == 2
    assert result["getStatus"] == 200
    assert result["writeCalls"] == 1
    assert result["abortCalls"] == 1
    assert result["abortName"] == "AbortError"
    assert result["letters"]["panel"] == "system-letters"
    assert result["reminders"]["panel"] == "memory-reminders"
    assert result["unknown"]["panel"] == "shared-buckets"
    assert result["mountedSearch"] == {
        "workspace": "shared",
        "panel": "shared-search",
        "params": {"q": "Amy Aki"},
    }


def test_path_env_turns_direct_file_open_into_safe_preview_mode() -> None:
    source = _source("path.js")
    script = f"""
const vm = require('vm');
const window = {{
  location: {{
    protocol: 'file:',
    origin: 'null',
    pathname: '/C:/Users/Amy98/Projects/Ombre-Brain/frontend/dashboard.html',
    search: '',
    hash: '',
  }},
  URL,
}};
window.window = window;
vm.createContext(window);
vm.runInContext({json.dumps(source)}, window, {{ filename: 'path.js' }});

const core = window.OmbreDashboardCore;
const filePreview = core.createPathEnv({{ location: window.location }});
let invalidExplicitOrigin = '';
try {{
  core.createPathEnv({{ pathname: '/', origin: 'file:///tmp/dashboard.html' }});
}} catch (error) {{
  invalidExplicitOrigin = error.name;
}}

process.stdout.write(JSON.stringify({{
  isFilePreview: filePreview.isFilePreview,
  origin: filePreview.origin,
  basePath: filePreview.basePath,
  authStatusUrl: filePreview.api('/auth/status'),
  invalidExplicitOrigin,
}}));
"""
    result = _run_node(script)

    assert result == {
        "isFilePreview": True,
        "origin": "http://localhost:18001",
        "basePath": "",
        "authStatusUrl": "http://localhost:18001/auth/status",
        "invalidExplicitOrigin": "TypeError",
    }


def test_api_client_scopes_unauthorized_handlers_per_request() -> None:
    sources = {name: _source(name) for name in ("path.js", "api.js")}
    script = f"""
const vm = require('vm');
const sources = {json.dumps(sources)};
const window = {{
  location: {{ origin: 'https://brain.example', pathname: '/', search: '', hash: '' }},
  setTimeout,
  clearTimeout,
  URL,
  URLSearchParams,
  Headers,
  FormData,
  Blob,
  ArrayBuffer,
  AbortController,
  DOMException,
  Response,
}};
window.window = window;
vm.createContext(window);
for (const name of ['path.js', 'api.js']) {{
  vm.runInContext(sources[name], window, {{ filename: name }});
}}

(async () => {{
  let globalUnauthorizedCalls = 0;
  let localUnauthorizedCalls = 0;
  let leakedUnauthorizedOption = false;
  const core = window.OmbreDashboardCore;
  const client = core.createApiClient({{
    pathEnv: core.createPathEnv({{ pathname: '/', origin: 'https://brain.example' }}),
    defaultRetries: 0,
    fetchImpl: async (_url, init) => {{
      leakedUnauthorizedOption = leakedUnauthorizedOption
        || Object.prototype.hasOwnProperty.call(init, 'onUnauthorized');
      return new Response('{{"error":"unauthorized"}}', {{
        status: 401,
        headers: {{ 'Content-Type': 'application/json' }},
      }});
    }},
    onUnauthorized: async () => {{ globalUnauthorizedCalls += 1; }},
  }});

  await client.get('/api/session-protected');
  await client.post('/api/alternate-credential', {{}}, {{
    onUnauthorized: async () => {{ localUnauthorizedCalls += 1; }},
  }});
  await client.post('/api/alternate-credential-silent', {{}}, {{ onUnauthorized: null }});

  process.stdout.write(JSON.stringify({{
    globalUnauthorizedCalls,
    localUnauthorizedCalls,
    leakedUnauthorizedOption,
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""

    assert _run_node(script) == {
        "globalUnauthorizedCalls": 1,
        "localUnauthorizedCalls": 1,
        "leakedUnauthorizedOption": False,
    }


def test_forced_resource_refresh_aborts_stale_inflight_loader() -> None:
    script = f"""
const vm = require('vm');
const source = {json.dumps(_source("store.js"))};
const window = {{ AbortController, DOMException }};
window.window = window;
vm.runInNewContext(source, window, {{ filename: 'store.js' }});

(async () => {{
  const store = window.OmbreDashboardCore.createDashboardStore({{ api: {{}} }});
  let loadCount = 0;
  let firstAborted = false;
  const first = store.resource('buckets:list', (context) => new Promise((resolve, reject) => {{
    loadCount += 1;
    context.signal.addEventListener('abort', () => {{
      firstAborted = true;
      reject(context.signal.reason);
    }}, {{ once: true }});
  }}));
  const firstOutcome = first.then(
    () => 'resolved',
    (error) => error && error.name || 'rejected',
  );
  await Promise.resolve();
  const fresh = await store.resource('buckets:list', async () => {{
    loadCount += 1;
    return 'fresh';
  }}, {{ refresh: true }});
  process.stdout.write(JSON.stringify({{
    loadCount,
    firstAborted,
    firstOutcome: await firstOutcome,
    fresh,
    stored: store.peek('buckets:list'),
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
    result = _run_node(script)

    assert result == {
        "loadCount": 2,
        "firstAborted": True,
        "firstOutcome": "AbortError",
        "fresh": "fresh",
        "stored": "fresh",
    }


def test_post_init_feature_queue_emits_registration_and_can_navigate() -> None:
    sources = {
        name: _source(name)
        for name in ("path.js", "api.js", "router.js", "store.js", "app-shell.js")
    }
    script = f"""
const vm = require('vm');
const sources = {json.dumps(sources)};
const documentEvents = [];
const window = {{
  location: {{ origin: 'https://brain.example', pathname: '/', search: '', hash: '' }},
  history: {{ pushState() {{}}, replaceState() {{}} }},
  document: {{
    querySelector() {{ return null; }},
    dispatchEvent(event) {{ documentEvents.push(event.type); }},
  }},
  addEventListener() {{}},
  removeEventListener() {{}},
  setTimeout,
  clearTimeout,
  URL,
  URLSearchParams,
  Headers,
  FormData,
  Blob,
  ArrayBuffer,
  AbortController,
  DOMException,
  CustomEvent: class CustomEvent {{
    constructor(type, options) {{ this.type = type; this.detail = options.detail; }}
  }},
}};
window.window = window;
vm.createContext(window);
for (const name of ['path.js', 'api.js', 'router.js', 'store.js', 'app-shell.js']) {{
  vm.runInContext(sources[name], window, {{ filename: name }});
}}

(async () => {{
  window.OmbreDashboardFeatureFactories.push((app) => app.registerPanel({{
    id: 'shared-buckets', workspace: 'shared', requiresRoot: false,
  }}));
  const app = window.OmbreDashboardApp.createDashboardApp({{
    fetchImpl: async () => ({{ status: 200, ok: true }}),
    eventTarget: window,
  }});
  await app.init();

  const registered = [];
  let activated = 0;
  app.onPanelRegistered((panel) => registered.push(panel.id));
  await window.OmbreDashboardApp.queueFeature((target) => target.registerPanel({{
    id: 'memory-dreams',
    workspace: 'memory',
    requiresRoot: false,
    activate() {{ activated += 1; }},
  }}));
  app.router.go('memory', 'memory-dreams', {{ dream_id: 'dream-1' }});
  await new Promise((resolve) => setTimeout(resolve, 0));

  process.stdout.write(JSON.stringify({{
    registered,
    panelAvailable: app.panels.has('memory-dreams'),
    current: app.router.current(),
    activated,
    panelEvent: documentEvents.includes('ombre-dashboard-panel-registered'),
    featuresEvent: documentEvents.includes('ombre-dashboard-features-loaded'),
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
    result = _run_node(script)

    assert result["registered"] == ["memory-dreams"]
    assert result["panelAvailable"] is True
    assert result["current"] == {
        "workspace": "memory",
        "panel": "memory-dreams",
        "params": {"dream_id": "dream-1"},
    }
    assert result["activated"] == 1
    assert result["panelEvent"] is True
    assert result["featuresEvent"] is True


def test_unified_shell_does_not_create_or_activate_an_app_before_auth() -> None:
    script = f"""
const vm = require('vm');
const source = {json.dumps(UNIFIED_SHELL.read_text(encoding="utf-8"))};
let createCalls = 0;
const document = {{
  readyState: 'complete',
  documentElement: {{ dataset: {{}} }},
  addEventListener() {{}},
}};
const window = {{
  document,
  OmbreDashboardAuthReady: Promise.resolve(false),
  OmbreDashboardApp: {{ createDashboardApp() {{ createCalls += 1; }} }},
  console: {{ error() {{}} }},
  setTimeout,
  clearTimeout,
}};
window.window = window;
vm.runInNewContext(source, window, {{ filename: 'unified-shell.js' }});
setTimeout(() => process.stdout.write(JSON.stringify({{
  createCalls,
  authState: document.documentElement.dataset.unifiedDashboardAuth,
}})), 0);
"""
    result = _run_node(script)

    assert result == {"createCalls": 0, "authState": "required"}


def test_rapid_routes_skip_superseded_queued_panel_activation() -> None:
    sources = {
        name: _source(name)
        for name in ("path.js", "api.js", "router.js", "store.js", "app-shell.js")
    }
    script = f"""
const vm = require('vm');
const sources = {json.dumps(sources)};
const window = {{
  location: {{ origin: 'https://brain.example', pathname: '/', search: '', hash: '' }},
  history: {{ pushState() {{}}, replaceState() {{}} }},
  document: {{ querySelector() {{ return null; }}, dispatchEvent() {{}} }},
  addEventListener() {{}},
  removeEventListener() {{}},
  setTimeout,
  clearTimeout,
  URL,
  URLSearchParams,
  Headers,
  FormData,
  Blob,
  ArrayBuffer,
  AbortController,
  DOMException,
  CustomEvent: class CustomEvent {{ constructor(type, options) {{ this.type = type; this.detail = options.detail; }} }},
}};
window.window = window;
vm.createContext(window);
for (const name of ['path.js', 'api.js', 'router.js', 'store.js', 'app-shell.js']) {{
  vm.runInContext(sources[name], window, {{ filename: name }});
}}

(async () => {{
  let bucketsActivated = 0;
  let dreamsActivated = 0;
  let statusActivated = 0;
  const app = window.OmbreDashboardApp.createDashboardApp({{
    fetchImpl: async () => ({{ status: 200, ok: true }}),
    eventTarget: window,
  }});
  app.registerPanel({{
    id: 'shared-buckets', workspace: 'shared', requiresRoot: false,
    activate() {{ bucketsActivated += 1; }},
  }});
  app.registerPanel({{
    id: 'memory-dreams', workspace: 'memory', requiresRoot: false,
    async activate() {{
      dreamsActivated += 1;
      await new Promise((resolve) => setTimeout(resolve, 40));
    }},
  }});
  app.registerPanel({{
    id: 'system-status', workspace: 'system', requiresRoot: false,
    activate() {{ statusActivated += 1; }},
  }});
  await app.init();
  app.router.go('memory', 'memory-dreams', {{}});
  app.router.go('system', 'system-status', {{}});
  await new Promise((resolve) => setTimeout(resolve, 5));
  process.stdout.write(JSON.stringify({{
    bucketsActivated,
    dreamsActivated,
    statusActivated,
    current: app.router.current(),
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
    result = _run_node(script)

    assert result["bucketsActivated"] == 1
    assert result["dreamsActivated"] == 0
    assert result["statusActivated"] == 1
    assert result["current"]["panel"] == "system-status"


def test_navigation_supersedes_an_in_progress_panel_activation() -> None:
    sources = {
        name: _source(name)
        for name in ("path.js", "api.js", "router.js", "store.js", "app-shell.js")
    }
    script = f"""
const vm = require('vm');
const sources = {json.dumps(sources)};
const window = {{
  location: {{ origin: 'https://brain.example', pathname: '/', search: '', hash: '' }},
  history: {{ pushState() {{}}, replaceState() {{}} }},
  document: {{ querySelector() {{ return null; }}, dispatchEvent() {{}} }},
  addEventListener() {{}},
  removeEventListener() {{}},
  setTimeout,
  clearTimeout,
  URL,
  URLSearchParams,
  Headers,
  FormData,
  Blob,
  ArrayBuffer,
  AbortController,
  DOMException,
  CustomEvent: class CustomEvent {{ constructor(type, options) {{ this.type = type; this.detail = options.detail; }} }},
}};
window.window = window;
vm.createContext(window);
for (const name of ['path.js', 'api.js', 'router.js', 'store.js', 'app-shell.js']) {{
  vm.runInContext(sources[name], window, {{ filename: name }});
}}

(async () => {{
  let releaseSlow;
  let signalObserved = false;
  let activationAborted = false;
  let statusActivated = 0;
  let markSlowStarted;
  const slowStarted = new Promise((resolve) => {{ markSlowStarted = resolve; }});
  const app = window.OmbreDashboardApp.createDashboardApp({{
    fetchImpl: async () => ({{ status: 200, ok: true }}),
    eventTarget: window,
  }});
  app.registerPanel({{
    id: 'shared-buckets', workspace: 'shared', requiresRoot: false,
  }});
  app.registerPanel({{
    id: 'memory-dreams', workspace: 'memory', requiresRoot: false,
    activate(context) {{
      signalObserved = Boolean(context.signal);
      if (context.signal) {{
        context.signal.addEventListener('abort', () => {{ activationAborted = true; }}, {{ once: true }});
      }}
      markSlowStarted();
      return new Promise((resolve) => {{ releaseSlow = resolve; }});
    }},
  }});
  app.registerPanel({{
    id: 'system-status', workspace: 'system', requiresRoot: false,
    activate() {{ statusActivated += 1; }},
  }});
  await app.init();
  app.router.go('memory', 'memory-dreams', {{}});
  await slowStarted;
  app.router.go('system', 'system-status', {{}});
  await new Promise((resolve) => setTimeout(resolve, 10));
  const beforeSlowSettled = statusActivated;
  releaseSlow();
  await new Promise((resolve) => setTimeout(resolve, 0));
  process.stdout.write(JSON.stringify({{
    signalObserved,
    activationAborted,
    beforeSlowSettled,
    statusActivated,
    current: app.router.current(),
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
    result = _run_node(script)

    assert result["signalObserved"] is True
    assert result["activationAborted"] is True
    assert result["beforeSlowSettled"] == 1
    assert result["statusActivated"] == 1
    assert result["current"]["panel"] == "system-status"


def test_unauthorized_activation_can_destroy_without_transition_deadlock() -> None:
    sources = {
        name: _source(name)
        for name in ("path.js", "api.js", "router.js", "store.js", "app-shell.js")
    }
    script = f"""
const vm = require('vm');
const sources = {json.dumps(sources)};
const window = {{
  location: {{ origin: 'https://brain.example', pathname: '/', search: '', hash: '' }},
  history: {{ pushState() {{}}, replaceState() {{}} }},
  document: {{ querySelector() {{ return null; }}, dispatchEvent() {{}} }},
  addEventListener() {{}},
  removeEventListener() {{}},
  setTimeout,
  clearTimeout,
  URL,
  URLSearchParams,
  Headers,
  FormData,
  Blob,
  ArrayBuffer,
  AbortController,
  DOMException,
  CustomEvent: class CustomEvent {{ constructor(type, options) {{ this.type = type; this.detail = options.detail; }} }},
}};
window.window = window;
vm.createContext(window);
for (const name of ['path.js', 'api.js', 'router.js', 'store.js', 'app-shell.js']) {{
  vm.runInContext(sources[name], window, {{ filename: name }});
}}

(async () => {{
  let app;
  let unauthorizedCalls = 0;
  let destroyCompleted = false;
  app = window.OmbreDashboardApp.createDashboardApp({{
    fetchImpl: async () => ({{ status: 401, ok: false }}),
    eventTarget: window,
    defaultRetries: 0,
    onUnauthorized: async () => {{
      unauthorizedCalls += 1;
      await app.destroy();
      destroyCompleted = true;
    }},
  }});
  app.registerPanel({{
    id: 'shared-buckets', workspace: 'shared', requiresRoot: false,
    async activate() {{ await app.api.get('/api/private'); }},
  }});
  const outcome = await Promise.race([
    app.init().then(() => 'settled'),
    new Promise((resolve) => setTimeout(() => resolve('timeout'), 100)),
  ]);
  process.stdout.write(JSON.stringify({{ outcome, unauthorizedCalls, destroyCompleted }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
    result = _run_node(script)

    assert result == {
        "outcome": "settled",
        "unauthorizedCalls": 1,
        "destroyCompleted": True,
    }


def test_legacy_user_click_runs_side_effects_once_and_deep_links_still_activate() -> None:
    dashboard_source = DASHBOARD.read_text(encoding="utf-8")
    listener_start = dashboard_source.index(
        "document.querySelectorAll('.tab').forEach(tab => {"
    )
    listener_end = dashboard_source.index("\nlet searchTimer;", listener_start)
    legacy_listener = dashboard_source[listener_start:listener_end]
    shell_source = UNIFIED_SHELL.read_text(encoding="utf-8").replace(
        "\n})(window);",
        "\n  global.__unifiedShellTest = { registerLegacyPanels, bindPanelTab };"
        "\n})(window);",
    )
    script = f"""
const vm = require('vm');
const shellSource = {json.dumps(shell_source)};
const legacyListener = {json.dumps(legacy_listener)};

function classList(initial) {{
  const values = new Set(initial || []);
  return {{
    add(name) {{ values.add(name); }},
    remove(name) {{ values.delete(name); }},
    contains(name) {{ return values.has(name); }},
    toggle(name, force) {{
      if (force === undefined) force = !values.has(name);
      if (force) values.add(name); else values.delete(name);
      return force;
    }},
  }};
}}

function element(id, dataset) {{
  return {{
    id: id || '',
    dataset: Object.assign({{}}, dataset || {{}}),
    classList: classList(),
    style: {{}},
    hidden: false,
    listeners: Object.create(null),
    addEventListener(type, handler) {{
      if (!this.listeners[type]) this.listeners[type] = [];
      this.listeners[type].push(handler);
    }},
    click() {{
      (this.listeners.click || []).slice().forEach((handler) => handler({{
        target: this,
        preventDefault() {{}},
      }}));
    }},
    setAttribute(name, value) {{ this[name] = String(value); }},
    getAttribute(name) {{ return this[name] || null; }},
    focus() {{}},
  }};
}}

const settingsTab = element('', {{
  tab: 'settings', panelId: 'system-status', workspace: 'system',
}});
const networkTab = element('', {{
  tab: 'network', panelId: 'shared-network', workspace: 'shared',
}});
const tabs = [settingsTab, networkTab];
const viewIds = [
  'list-view', 'breath-view', 'network-view', 'plan-view', 'import-view',
  'logs-view', 'v3-debug-view', 'settings-view', 'letters-view',
  'anchors-view', 'about-view', 'mcp-local-origin',
];
const elements = Object.fromEntries(viewIds.map((id) => [id, element(id)]));
const document = {{
  readyState: 'loading',
  body: element('body'),
  documentElement: element('html'),
  addEventListener() {{}},
  getElementById(id) {{ return elements[id] || null; }},
  querySelectorAll(selector) {{
    if (selector === '.tab' || selector === '#panel-tabs .tab[data-panel-id]') return tabs;
    if (selector === '.content') return Object.values(elements).filter((item) => item.id.endsWith('-view'));
    return [];
  }},
  querySelector(selector) {{
    if (selector === '.tab[data-tab="settings"]') return settingsTab;
    if (selector === '.tab[data-tab="network"]') return networkTab;
    if (selector === '.tab[data-panel-id="system-status"]') return settingsTab;
    if (selector === '.tab[data-panel-id="shared-network"]') return networkTab;
    if (selector.startsWith('#')) return elements[selector.slice(1)] || null;
    return null;
  }},
}};

let settingsRequests = 0;
let networkRequests = 0;
let routeCalls = 0;
const window = {{
  document,
  location: {{ origin: 'https://brain.example' }},
  requestAnimationFrame(callback) {{ callback(); return 1; }},
  cancelAnimationFrame() {{}},
  setTimeout,
  clearTimeout,
  _netRAF: null,
  cancelBucketSearch() {{}},
  renderBuckets() {{}},
  filterBuckets() {{ return []; }},
  allBuckets: [],
  loadNetwork() {{ networkRequests += 1; }},
  loadPlans() {{}},
  pollImportStatus() {{}},
  loadImportResults() {{}},
  loadLogs() {{}},
  loadOBErrors() {{}},
  loadV3Debug() {{}},
  loadLetters() {{}},
  loadAnchorsView() {{}},
  loadAbout() {{}},
  renderMcpUrls() {{}},
}};
[
  'loadSettingsStatus', 'loadSystemDiagnostics', 'loadHumanName', 'loadConfig',
  'refreshEnvConfig', 'loadGithubStatus', 'loadSamplingSettings', 'loadEnvVars',
  'loadHostVault', 'loadLocalEmbStatus',
].forEach((name) => {{ window[name] = () => {{ settingsRequests += 1; }}; }});
window.window = window;
vm.createContext(window);
vm.runInContext(shellSource, window, {{ filename: 'unified-shell.js' }});
vm.runInContext(legacyListener, window, {{ filename: 'dashboard-legacy-tabs.js' }});

const panels = new Map();
let current = {{ workspace: 'shared', panel: 'shared-buckets', params: {{}} }};
const app = {{
  panels,
  registerPanel(definition) {{ panels.set(definition.id, definition); }},
  router: {{
    current() {{ return current; }},
    go(workspace, panel, params) {{
      routeCalls += 1;
      current = {{ workspace, panel, params: params || {{}} }};
      const definition = panels.get(panel);
      if (definition && definition.activate) definition.activate(Object.assign({{ state: current }}, params));
      return current;
    }},
  }},
}};
window.__unifiedShellTest.registerLegacyPanels(app);
window.__unifiedShellTest.bindPanelTab(app, settingsTab);
window.__unifiedShellTest.bindPanelTab(app, networkTab);

settingsTab.click();
const settingsAfterUserClick = settingsRequests;
settingsRequests = 0;
networkTab.click();
const networkAfterUserClick = networkRequests;
networkRequests = 0;
app.router.go('system', 'system-status', {{}});

process.stdout.write(JSON.stringify({{
  settingsAfterUserClick,
  networkAfterUserClick,
  settingsAfterProgrammaticRoute: settingsRequests,
  routeCalls,
}}));
"""

    assert _run_node(script) == {
        "settingsAfterUserClick": 10,
        "networkAfterUserClick": 1,
        "settingsAfterProgrammaticRoute": 10,
        "routeCalls": 3,
    }


def test_bucket_detail_route_hydrates_once_on_reload_and_open_command() -> None:
    shell_source = UNIFIED_SHELL.read_text(encoding="utf-8").replace(
        "\n})(window);",
        "\n  global.__unifiedShellTest = { registerLegacyPanels, configureApp };"
        "\n})(window);",
    )
    sources = {
        "router.js": _source("router.js"),
        "unified-shell.js": shell_source,
    }
    script = f"""
const vm = require('vm');
const sources = {json.dumps(sources)};

function element(id, dataset) {{
  const classes = new Set();
  return {{
    id: id || '', dataset: Object.assign({{}}, dataset || {{}}), style: {{}}, hidden: false,
    classList: {{
      add(name) {{ classes.add(name); }}, remove(name) {{ classes.delete(name); }},
      contains(name) {{ return classes.has(name); }},
      toggle(name, force) {{ if (force) classes.add(name); else classes.delete(name); }},
    }},
    click() {{}}, focus() {{}},
    setAttribute(name, value) {{ this[name] = String(value); }},
  }};
}}

const listTab = element('dashboard-tab-shared-buckets', {{
  tab: 'list', panelId: 'shared-buckets', workspace: 'shared',
}});
const listView = element('list-view');
const document = {{
  readyState: 'loading', body: element('body'), documentElement: element('html'),
  addEventListener() {{}},
  getElementById(id) {{ return id === 'list-view' ? listView : null; }},
  querySelectorAll(selector) {{
    if (selector === '#panel-tabs .tab[data-panel-id]') return [listTab];
    return [];
  }},
  querySelector(selector) {{
    if (selector === '.tab[data-tab="list"]') return listTab;
    if (selector === '.tab[data-panel-id="shared-buckets"]') return listTab;
    if (selector === '#list-view') return listView;
    return null;
  }},
}};
let detailCalls = [];
let releaseReload;
const window = {{
  document,
  location: {{
    origin: 'https://brain.example', pathname: '/memory-dashboard',
    search: '?workspace=shared&panel=shared-buckets&bucketId=Amy%2FAki', hash: '',
  }},
  history: {{ pushState() {{}}, replaceState() {{}} }},
  addEventListener() {{}}, removeEventListener() {{}},
  setTimeout, clearTimeout, URLSearchParams,
  requestAnimationFrame(callback) {{ callback(); return 1; }},
  cancelBucketSearch() {{}},
  showDetail(id) {{
    detailCalls.push(id);
    if (id === 'Amy/Aki') return new Promise((resolve) => {{ releaseReload = resolve; }});
    return Promise.resolve(true);
  }},
}};
window.window = window;
vm.createContext(window);
vm.runInContext(sources['router.js'], window, {{ filename: 'router.js' }});
vm.runInContext(sources['unified-shell.js'], window, {{ filename: 'unified-shell.js' }});

(async () => {{
  const pathEnv = {{
    bootMode: 'shared', entryRoute: '/memory-dashboard',
    route(path) {{ return path || '/memory-dashboard'; }},
    asset(path) {{ return '/dashboard-assets/' + path; }},
  }};
  const router = window.OmbreDashboardCore.createRouter({{
    pathEnv, location: window.location, history: window.history, eventTarget: window,
  }});
  const panels = new Map();
  const app = {{
    panels, router, commands: {{}},
    env: {{ asset(path) {{ return '/dashboard-assets/' + path; }} }},
    registerPanel(definition) {{
      panels.set(definition.id, definition);
      router.registerPanel(definition.id, definition.workspace);
    }},
  }};
  window.__unifiedShellTest.registerLegacyPanels(app);
  window.__unifiedShellTest.configureApp(app);
  const definition = panels.get('shared-buckets');
  const reloaded = router.current();
  const reloadContext = Object.assign({{ state: reloaded }}, reloaded.params);
  const firstHydration = definition.activate(reloadContext);
  const duplicateHydration = definition.activate(reloadContext);
  await Promise.resolve();
  const reloadCallsBeforeRelease = detailCalls.slice();
  if (releaseReload) releaseReload(true);
  await Promise.all([firstHydration, duplicateHydration]);

  router.onChange((next) => {{
    const context = Object.assign({{ state: next }}, next.params);
    definition.activate(context);
  }});
  const opened = app.commands.openBucket('second bucket');
  await new Promise((resolve) => setTimeout(resolve, 0));

  process.stdout.write(JSON.stringify({{
    reloadedBucketId: reloaded.params.bucketId,
    reloadCallsBeforeRelease,
    openedBucketId: opened.params.bucketId,
    detailCalls,
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""

    assert _run_node(script) == {
        "reloadedBucketId": "Amy/Aki",
        "reloadCallsBeforeRelease": ["Amy/Aki"],
        "openedBucketId": "second bucket",
        "detailCalls": ["Amy/Aki", "second bucket"],
    }


def test_legacy_section_route_survives_reload_and_history_without_duplicate_loaders() -> None:
    dashboard_source = DASHBOARD.read_text(encoding="utf-8")
    listener_start = dashboard_source.index(
        "document.querySelectorAll('.tab').forEach(tab => {"
    )
    listener_end = dashboard_source.index("\nlet searchTimer;", listener_start)
    legacy_listener = dashboard_source[listener_start:listener_end]
    shell_source = UNIFIED_SHELL.read_text(encoding="utf-8").replace(
        "\n})(window);",
        "\n  global.__unifiedShellTest = { registerLegacyPanels, registerPanelAliases, configureApp };"
        "\n})(window);",
    )
    sources = {
        "router.js": _source("router.js"),
        "unified-shell.js": shell_source,
    }
    script = f"""
const vm = require('vm');
const sources = {json.dumps(sources)};
const legacyListener = {json.dumps(legacy_listener)};

function element(id, dataset, group) {{
  const classes = new Set();
  return {{
    id: id || '', dataset: Object.assign({{}}, dataset || {{}}), style: {{}}, hidden: false,
    listeners: Object.create(null),
    classList: {{
      add(name) {{ classes.add(name); }}, remove(name) {{ classes.delete(name); }},
      contains(name) {{ return classes.has(name); }},
      toggle(name, force) {{ if (force) classes.add(name); else classes.delete(name); }},
    }},
    addEventListener(type, handler) {{ (this.listeners[type] ||= []).push(handler); }},
    click() {{ (this.listeners.click || []).slice().forEach((handler) => handler({{ target: this }})); }},
    setAttribute(name, value) {{ this[name] = String(value); }},
    getAttribute(name) {{ return name === 'data-sgroup' ? group || null : this[name] || null; }},
    scrollIntoView() {{ revealed.push(this.id); }},
    focus() {{}},
  }};
}}

const revealed = [];
const groups = [];
const settingsTab = element('dashboard-tab-system-status', {{
  tab: 'settings', panelId: 'system-status', workspace: 'system',
}});
const viewIds = [
  'list-view', 'breath-view', 'network-view', 'plan-view', 'import-view',
  'logs-view', 'v3-debug-view', 'settings-view', 'letters-view',
  'anchors-view', 'about-view', 'mcp-local-origin',
];
const elements = Object.fromEntries(viewIds.map((id) => [id, element(id)]));
elements['sec-service'] = element('sec-service', {{}}, 'advanced');
elements['sec-me'] = element('sec-me', {{}}, 'basics');
elements['sec-backup'] = element('sec-backup', {{}}, 'backup');
elements['sec-github'] = element('sec-github', {{}}, 'backup');
const document = {{
  readyState: 'loading', body: element('body'), documentElement: element('html'),
  addEventListener() {{}},
  getElementById(id) {{ return elements[id] || null; }},
  querySelectorAll(selector) {{
    if (selector === '.tab' || selector === '#panel-tabs .tab[data-panel-id]') return [settingsTab];
    return [];
  }},
  querySelector(selector) {{
    if (selector === '.tab[data-tab="settings"]') return settingsTab;
    if (selector === '.tab[data-panel-id="system-status"]') return settingsTab;
    if (selector.startsWith('#')) return elements[selector.slice(1)] || null;
    return null;
  }},
}};
const routeListeners = Object.create(null);
const location = {{
  origin: 'https://brain.example', pathname: '/ombre/memory-dashboard',
  search: '?workspace=system&panel=system-status&section=sec-backup', hash: '',
}};
const pushedUrls = [];
const window = {{
  document, location,
  history: {{
    pushState(_state, _title, url) {{
      pushedUrls.push(url);
      location.search = url.includes('?') ? '?' + url.split('?')[1] : '';
    }},
    replaceState(_state, _title, url) {{
      location.search = url.includes('?') ? '?' + url.split('?')[1] : '';
    }},
  }},
  addEventListener(type, handler) {{ (routeListeners[type] ||= []).push(handler); }},
  removeEventListener(type, handler) {{
    routeListeners[type] = (routeListeners[type] || []).filter((item) => item !== handler);
  }},
  dispatch(type) {{ (routeListeners[type] || []).slice().forEach((handler) => handler()); }},
  setTimeout, clearTimeout, URLSearchParams,
  requestAnimationFrame(callback) {{ callback(); return 1; }},
  cancelAnimationFrame() {{}}, _netRAF: null,
  cancelBucketSearch() {{}}, showSettingsGroup(group) {{ groups.push(group); }},
  loadNetwork() {{}}, loadPlans() {{}}, pollImportStatus() {{}}, loadImportResults() {{}},
  loadLogs() {{}}, loadOBErrors() {{}}, loadV3Debug() {{}}, loadLetters() {{}},
  loadAnchorsView() {{}}, loadAbout() {{}}, renderMcpUrls() {{}},
}};
let settingsRequests = 0;
[
  'loadSettingsStatus', 'loadSystemDiagnostics', 'loadHumanName', 'loadConfig',
  'refreshEnvConfig', 'loadGithubStatus', 'loadSamplingSettings', 'loadEnvVars',
  'loadHostVault', 'loadLocalEmbStatus',
].forEach((name) => {{ window[name] = () => {{ settingsRequests += 1; }}; }});
window.window = window;
vm.createContext(window);
vm.runInContext(sources['router.js'], window, {{ filename: 'router.js' }});
vm.runInContext(sources['unified-shell.js'], window, {{ filename: 'unified-shell.js' }});
vm.runInContext(legacyListener, window, {{ filename: 'dashboard-legacy-tabs.js' }});

function createRouter() {{
  return window.OmbreDashboardCore.createRouter({{
    pathEnv: {{
      bootMode: 'system', entryRoute: '/memory-dashboard',
      route(path) {{ return '/ombre' + (path || ''); }},
    }},
    location, history: window.history, eventTarget: window,
  }});
}}
function createApp(router) {{
  const panels = new Map();
  const app = {{
    panels, router, commands: {{}},
    env: {{ asset(path) {{ return '/ombre/dashboard-assets/' + path; }} }},
    registerPanel(definition) {{
      panels.set(definition.id, definition);
      router.registerPanel(definition.id, definition.workspace);
    }},
  }};
  window.__unifiedShellTest.registerLegacyPanels(app);
  window.__unifiedShellTest.registerPanelAliases(app);
  window.__unifiedShellTest.configureApp(app);
  const activate = (next) => panels.get(next.panel).activate(
    Object.assign({{ state: next }}, next.params)
  );
  router.onChange(activate);
  return app;
}}

let router = createRouter();
let app = createApp(router);
router.start();
const afterReload = {{ section: revealed.at(-1), requests: settingsRequests }};

app.commands.openLegacyPanel('settings', 'sec-github');
const githubUrl = pushedUrls.at(-1);
const afterGithub = {{ section: revealed.at(-1), requests: settingsRequests }};

location.search = '?workspace=system&panel=system-status&section=sec-backup';
window.dispatch('popstate');
const afterBack = {{ section: revealed.at(-1), requests: settingsRequests }};

location.search = '?workspace=system&panel=system-status&section=sec-github';
window.dispatch('popstate');
const afterForward = {{ section: revealed.at(-1), requests: settingsRequests }};

router.stop();
router = createRouter();
app = createApp(router);
router.start();
const afterRefreshedForward = {{ section: revealed.at(-1), requests: settingsRequests }};

location.search = '?workspace=system&panel=system-status&section=sec-me';
window.dispatch('popstate');
const afterMeSection = {{ section: revealed.at(-1), requests: settingsRequests }};

app.commands.openLegacyPanel('settings', 'sec-not-real');
const unknownSectionUrl = pushedUrls.at(-1);
const afterUnknownSection = {{ section: revealed.at(-1), requests: settingsRequests }};

location.search = '?workspace=models-data&panel=models-github-backup';
window.dispatch('popstate');
const afterGithubAlias = {{
  workspace: router.current().workspace, panel: router.current().panel,
  section: revealed.at(-1), search: location.search, requests: settingsRequests,
}};

location.search = '?workspace=system&panel=system-errors';
window.dispatch('popstate');
const afterErrorsAlias = {{
  workspace: router.current().workspace, panel: router.current().panel,
  search: location.search, requests: settingsRequests,
}};

process.stdout.write(JSON.stringify({{
  afterReload, githubUrl, afterGithub, afterBack, afterForward, afterRefreshedForward,
  afterMeSection, unknownSectionUrl, afterUnknownSection, afterGithubAlias,
  afterErrorsAlias, groups,
}}));
"""

    result = _run_node(script)

    assert result["afterReload"] == {"section": "sec-backup", "requests": 10}
    assert result["afterGithub"] == {"section": "sec-github", "requests": 20}
    assert result["afterBack"] == {"section": "sec-backup", "requests": 30}
    assert result["afterForward"] == {"section": "sec-github", "requests": 40}
    assert result["afterRefreshedForward"] == {
        "section": "sec-github",
        "requests": 50,
    }
    assert result["afterMeSection"] == {
        "section": "sec-me",
        "requests": 60,
    }
    assert result["afterUnknownSection"] == {
        "section": "sec-me",
        "requests": 70,
    }
    assert result["afterGithubAlias"] == {
        "workspace": "system",
        "panel": "system-status",
        "section": "sec-github",
        "search": "?section=sec-github&workspace=system&panel=system-status",
        "requests": 80,
    }
    assert result["afterErrorsAlias"] == {
        "workspace": "system",
        "panel": "system-logs",
        "search": "?workspace=system&panel=system-logs",
        "requests": 80,
    }
    assert result["githubUrl"].startswith("/ombre/memory-dashboard?")
    assert "section=sec-github" in result["githubUrl"]
    assert "section=" not in result["unknownSectionUrl"]
    assert result["groups"] == [
        "backup",
        "backup",
        "backup",
        "backup",
        "backup",
        "basics",
        "backup",
    ]
