from __future__ import annotations

import re
import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "frontend" / "dashboard-assets" / "memory-insights.js"
STYLES = ROOT / "frontend" / "dashboard-assets" / "memory-insights.css"

EXPECTED_PANELS = {
    "memory-word-map",
    "memory-identity-semantics",
    "memory-moment-diagnostics",
    "memory-recall-diagnostics",
    "memory-diffusion-diagnostics",
    "memory-gateway-injections",
}


def _source() -> str:
    return ASSET.read_text(encoding="utf-8")


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def test_memory_insights_registers_six_distinct_memory_panels() -> None:
    source = _source()
    registered = set(
        re.findall(
            r"app\.registerPanel\(\{.*?\bid:\s*['\"]([^'\"]+)['\"]",
            source,
            flags=re.DOTALL,
        )
    )

    assert "window.OmbreDashboardFeatureFactories" in source
    assert registered == EXPECTED_PANELS
    assert source.count("app.registerPanel({") == len(EXPECTED_PANELS)
    assert source.count("workspace: 'memory'") == len(EXPECTED_PANELS)


def test_memory_insights_use_only_the_unified_api_and_ui_boundaries() -> None:
    source = _source()

    assert "app.api" in source
    assert "app.ui" in source
    assert "authFetch" not in source
    assert not re.search(r"(?<![.\w])fetch\s*\(", source)
    assert not re.search(r"\bBASE\b", source)
    assert "innerHTML = error" not in source
    assert "escapeFallback" in source
    for escaped_character in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert escaped_character in source


def test_word_map_preserves_nodes_edges_cards_boundary_and_rebuild_contracts() -> None:
    source = _source()
    compact = _compact(source)

    assert "/api/word-map?nodes=20&edges=20" in source
    assert "/api/word-map/cards?term=" in source
    assert "&limit=20" in source
    assert "/api/word-map/rebuild" in source
    assert "{include_archive:false,nodes:20,edges:20}" in compact
    for visible_contract in (
        "Top Nodes",
        "Co-occurrence Edges",
        "Selected Term Cards",
        "Private Alias Boundary",
        "private_terms_excluded",
        "evidence_bucket_ids",
    ):
        assert visible_contract in source


def test_identity_semantics_keeps_a_distinct_list_and_rebuild_action() -> None:
    source = _source()
    compact = _compact(source)

    assert "/api/identity-semantics?limit=50" in source
    assert "/api/identity-semantics/rebuild" in source
    assert "{include_archive:false,limit:50}" in compact
    for visible_contract in (
        "Identity Semantics",
        "Private Alias",
        "canonical",
        "confidence",
        "scope",
        "evidence_bucket_ids",
    ):
        assert visible_contract in source


def test_moment_recall_and_diffusion_diagnostics_keep_query_contracts() -> None:
    source = _source()

    assert "/api/moments?bucket_id=" in source
    assert "&limit=40" in source
    assert "/api/recall-debug?q=" in source
    assert "&max_candidates=12&max_results=3" in source
    assert "/api/diffusion-debug?q=" in source

    for visible_contract in (
        "bucket_layer_debug",
        "runtime_gate",
        "source_window",
        "Moment Edges",
        "seed_buckets",
        "admitted_count",
        "suppressed_count",
        "recall_thresholds",
        "Seeds",
        "Hits",
        "paths",
        "warnings",
    ):
        assert visible_contract in source


def test_gateway_injection_inspector_defaults_to_metadata_only() -> None:
    source = _source()

    assert "/api/gateway-injections?limit=10&include_context=" in source
    assert "session_id=" in source
    assert "includeContext: false" in source
    assert "includeContext ? gatewayContextPreview(payload) : ''" in source
    for visible_contract in (
        "recalled_bucket_ids",
        "diffused_bucket_ids",
        "injected_bucket_ids",
        "recalled_moment_ids",
        "diffused_moment_ids",
        "recent_context_injected",
        "date_persona_trace_injected",
        "dream_context_status",
        "query_preview",
    ):
        assert visible_contract in source


def test_every_panel_has_loading_empty_error_retry_and_safe_event_delegation() -> None:
    source = _source()

    for state in ("loading", "empty", "error"):
        assert f"data-state=\"{state}\"" in source
    assert 'data-action="retry"' in source
    assert "addEventListener('click'" in source
    assert "addEventListener('submit'" in source
    assert "onclick=" not in source
    assert "setStatus" in source
    assert "if (state.rebuilding) return" in source
    assert "button.disabled = true" in source


def test_memory_insights_css_is_scoped_and_responsive() -> None:
    css = STYLES.read_text(encoding="utf-8")

    assert '.memory-insights-panel' in css
    assert '[data-panel-id^="memory-"] .memory-insights-panel' in css
    assert "@media (max-width: 760px)" in css
    assert ".memory-insights-grid" in css
    assert ".memory-insights-query" in css
    assert ".memory-insights-card" in css
    assert ".memory-insights-path" in css


def test_memory_insights_runtime_requests_and_privacy_transitions() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser-module behavior contract")

    script = r"""
const assert = require('assert');
global.window = {};
require(__ASSET__);

function response(payload) {
  return { ok: true, status: 200, json: async () => payload };
}

const calls = [];
let pendingIdentityResolve = null;
let identityCall = 0;
const api = {
  get: async (path, options) => {
    calls.push({ method: 'GET', path, options: options || {} });
    if (path.startsWith('/api/word-map?')) {
      return response({
        stats: { nodes: 1, card_nodes: 1, edge_evidence: 1 },
        nodes: [{ term: '<private>', kind: 'keyword', bucket_count: 1, weight: 0.8 }],
        edges: [{ term_a: '<private>', term_b: 'safe', bucket_count: 1, weight: 0.7 }],
        private_terms_excluded: ['amy'],
      });
    }
    if (path.startsWith('/api/word-map/cards?')) {
      return response({ cards: [{ bucket_id: 'memory-1', kind: 'keyword', source: 'tag', weight: 0.8 }] });
    }
    if (path.startsWith('/api/identity-semantics?')) {
      identityCall += 1;
      if (identityCall === 1) {
        return new Promise((resolve) => { pendingIdentityResolve = () => resolve(response({
          enabled: true,
          stats: { canonical: 1, aliases: 1, evidence: 1 },
          aliases: [{ alias: 'old', canonical: 'Amy', evidence_bucket_ids: ['memory-1'] }],
        })); });
      }
      return response({
        enabled: true,
        stats: { canonical: 1, aliases: 1, evidence: 2 },
        aliases: [{ alias: 'fresh', canonical: 'Amy', evidence_bucket_ids: ['memory-2'] }],
      });
    }
    if (path.startsWith('/api/moments?')) {
      return response({ status: 'ok', name: 'Memory', moments: [{ moment_id: 'm1', text: '<moment>' }], edges: [] });
    }
    if (path.startsWith('/api/recall-debug?')) {
      return response({ status: 'ok', seed_buckets: [], candidates: [{ bucket_id: 'memory-1', moment_id: 'm1', text_preview: '<recall>' }] });
    }
    if (path.startsWith('/api/diffusion-debug?')) {
      return response({ status: 'ok', seeds: [], hits: [{ bucket_id: 'memory-2', path: '<path>', paths: [] }], warnings: ['careful'] });
    }
    if (path.startsWith('/api/gateway-injections?')) {
      return response({ status: 'ok', items: [{ session_id: 'session', payload: {
        query_preview: '<query>', dynamic_context: '<secret>', injected_bucket_ids: [],
      } }] });
    }
    throw new Error('Unexpected GET ' + path);
  },
  post: async (path, body) => {
    calls.push({ method: 'POST', path, body });
    if (path === '/api/word-map/rebuild') {
      return response({ stats: { nodes: 1, card_nodes: 1, edge_evidence: 1 } });
    }
    if (path === '/api/identity-semantics/rebuild') {
      return response({ enabled: true, stats: {}, aliases: [] });
    }
    throw new Error('Unexpected POST ' + path);
  },
};

function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const ui = {
  escape: escapeHtml,
  escapeAttr: escapeHtml,
  setStatus: (element, message, tone) => { element.textContent = message; element.tone = tone; },
  confirm: async () => true,
};

class Element {
  constructor() { this.innerHTML = ''; this.textContent = ''; this.value = ''; this.checked = false; this.attrs = {}; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name] || null; }
}

class Root extends Element {
  constructor() { super(); this.elements = {}; this.listeners = {}; }
  querySelector(selector) {
    if (!this.elements[selector]) this.elements[selector] = new Element();
    return this.elements[selector];
  }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  contains() { return true; }
  click(action) {
    const button = new Element();
    button.setAttribute('data-action', action);
    button.closest = () => button;
    this.listeners.click({ target: button });
    return button;
  }
}

function wait() { return new Promise((resolve) => setTimeout(resolve, 15)); }

(async () => {
  const panels = [];
  window.OmbreDashboardFeatureFactories[0]({ api, ui, registerPanel: (panel) => panels.push(panel) });
  assert.strictEqual(panels.length, 6);
  const roots = Object.fromEntries(panels.map((panel) => {
    const root = new Root();
    panel.mount(root);
    return [panel.id, root];
  }));
  const byId = Object.fromEntries(panels.map((panel) => [panel.id, panel]));

  byId['memory-word-map'].activate({ state: { params: {} } });
  await wait();
  assert.strictEqual(identityCall, 1);

  byId['memory-identity-semantics'].activate({ state: { params: {} } });
  roots['memory-identity-semantics'].click('refresh');
  await wait();
  assert.strictEqual(identityCall, 2, 'forced refresh must bypass the older in-flight identity request');
  pendingIdentityResolve();
  await wait();
  assert(roots['memory-identity-semantics'].querySelector('[data-role="aliases"]').innerHTML.includes('fresh'));

  byId['memory-moment-diagnostics'].activate({ state: { params: { bucket_id: 'memory-1' } } });
  byId['memory-recall-diagnostics'].activate({ state: { params: { q: 'hello' } } });
  byId['memory-diffusion-diagnostics'].activate({ state: { params: { q: 'hello' } } });
  byId['memory-gateway-injections'].activate({ state: { params: {} } });
  await wait();

  assert(roots['memory-word-map'].querySelector('[data-role="nodes"]').innerHTML.includes('&lt;private&gt;'));
  assert(roots['memory-moment-diagnostics'].querySelector('[data-role="results"]').innerHTML.includes('&lt;moment&gt;'));
  assert(roots['memory-recall-diagnostics'].querySelector('[data-role="results"]').innerHTML.includes('&lt;recall&gt;'));
  assert(roots['memory-diffusion-diagnostics'].querySelector('[data-role="results"]').innerHTML.includes('&lt;path&gt;'));
  const gatewayRoot = roots['memory-gateway-injections'];
  assert(!gatewayRoot.querySelector('[data-role="results"]').innerHTML.includes('secret'));
  assert(calls.some((call) => call.path === '/api/gateway-injections?limit=10&include_context=0'));

  const gatewayForm = {
    getAttribute: () => 'gateway',
    elements: { session_id: { value: '' }, include_context: { checked: true } },
  };
  gatewayRoot.listeners.submit({ target: gatewayForm, preventDefault() {} });
  await wait();
  assert(calls.some((call) => call.path === '/api/gateway-injections?limit=10&include_context=1'));
  assert(gatewayRoot.querySelector('[data-role="results"]').innerHTML.includes('&lt;secret&gt;'));

  byId['memory-gateway-injections'].deactivate();
  byId['memory-gateway-injections'].activate({ state: { params: {} } });
  await wait();
  const gatewayCalls = calls.filter((call) => call.path && call.path.startsWith('/api/gateway-injections?'));
  assert.strictEqual(gatewayCalls[gatewayCalls.length - 1].path, '/api/gateway-injections?limit=10&include_context=0');
  assert.strictEqual(gatewayRoot.querySelector('input[name="include_context"]').checked, false);

  roots['memory-word-map'].click('rebuild-word-map');
  roots['memory-identity-semantics'].click('rebuild-identity');
  await wait();
  assert.deepStrictEqual(calls.find((call) => call.path === '/api/word-map/rebuild').body,
    { include_archive: false, nodes: 20, edges: 20 });
  assert.deepStrictEqual(calls.find((call) => call.path === '/api/identity-semantics/rebuild').body,
    { include_archive: false, limit: 50 });
  assert(calls.filter((call) => call.method === 'GET').every((call) => call.options.signal),
    'every sibling Insights loader must pass a cancellation signal');
})();
""".replace("__ASSET__", json.dumps(str(ASSET)))

    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_memory_insights_abort_superseded_reads_and_retry_with_a_fresh_signal() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser-module behavior contract")

    script = r"""
const assert = require('assert');
global.window = {};
require(__ASSET__);

function response(payload) {
  return { ok: true, status: 200, json: async () => payload };
}
function abortError() {
  const error = new Error('Aborted');
  error.name = 'AbortError';
  return error;
}
function wait() { return new Promise((resolve) => setTimeout(resolve, 10)); }

const calls = [];
let momentCall = 0;
let recallCall = 0;
const api = {
  get: (path, options) => {
    const call = { path, options: options || {}, aborted: false };
    calls.push(call);
    const signal = call.options.signal;
    assert(signal, 'every Insights read must pass an AbortSignal');
    signal.addEventListener('abort', () => { call.aborted = true; }, { once: true });

    if (path.startsWith('/api/moments?')) {
      momentCall += 1;
      if (momentCall === 2) {
        return Promise.resolve(response({
          status: 'ok', name: 'fresh moments', moments: [{ moment_id: 'fresh', text: 'fresh moment' }], edges: [],
        }));
      }
      return new Promise((resolve, reject) => {
        signal.addEventListener('abort', () => reject(abortError()), { once: true });
      });
    }
    if (path.startsWith('/api/identity-semantics?')) {
      return new Promise((resolve, reject) => {
        signal.addEventListener('abort', () => reject(abortError()), { once: true });
      });
    }
    if (path.startsWith('/api/recall-debug?')) {
      recallCall += 1;
      if (recallCall === 1) return Promise.reject(new Error('temporary recall failure'));
      return Promise.resolve(response({
        status: 'ok', seed_buckets: [],
        candidates: [{ bucket_id: 'fresh-recall', moment_id: 'm1', text_preview: 'fresh recall' }],
      }));
    }
    if (path.startsWith('/api/diffusion-debug?')) {
      return new Promise((resolve, reject) => {
        signal.addEventListener('abort', () => reject(abortError()), { once: true });
      });
    }
    if (path.startsWith('/api/gateway-injections?')) {
      return new Promise((resolve, reject) => {
        signal.addEventListener('abort', () => reject(abortError()), { once: true });
      });
    }
    throw new Error('Unexpected GET ' + path);
  },
};

class Element {
  constructor() { this.innerHTML = ''; this.textContent = ''; this.value = ''; this.checked = false; this.attrs = {}; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name] || null; }
}
class Root extends Element {
  constructor() { super(); this.elements = {}; this.listeners = {}; }
  querySelector(selector) {
    if (!this.elements[selector]) this.elements[selector] = new Element();
    return this.elements[selector];
  }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  contains() { return true; }
  click(action) {
    const button = new Element();
    button.setAttribute('data-action', action);
    button.closest = () => button;
    this.listeners.click({ target: button });
  }
}

(async () => {
  const panels = [];
  window.OmbreDashboardFeatureFactories[0]({
    api,
    ui: {
      escape: (value) => String(value == null ? '' : value),
      escapeAttr: (value) => String(value == null ? '' : value),
      setStatus: (element, message) => { element.textContent = message; },
    },
    registerPanel: (panel) => panels.push(panel),
  });
  const byId = Object.fromEntries(panels.map((panel) => [panel.id, panel]));
  const roots = {};
  for (const panelId of [
    'memory-identity-semantics',
    'memory-moment-diagnostics',
    'memory-recall-diagnostics',
    'memory-diffusion-diagnostics',
    'memory-gateway-injections',
  ]) {
    roots[panelId] = new Root();
    byId[panelId].mount(roots[panelId]);
  }

  const identityRoute = new AbortController();
  const identityActivation = byId['memory-identity-semantics'].activate({
    state: { params: {} }, signal: identityRoute.signal, scopeId: 'panel:identity',
  });
  await wait();
  const identityCall = calls[calls.length - 1];
  identityRoute.abort('route changed');
  await Promise.race([
    identityActivation,
    new Promise((resolve, reject) => setTimeout(() => reject(new Error('identity cancellation hung')), 100)),
  ]);
  assert(identityCall.aborted, 'primitive abort reasons must still cancel the shared identity read');

  const firstRoute = new AbortController();
  const firstMoment = byId['memory-moment-diagnostics'].activate({
    state: { params: { bucket_id: 'old' } }, signal: firstRoute.signal, scopeId: 'panel:moments',
  });
  await wait();
  const oldMomentCall = calls.find((call) => call.path.includes('bucket_id=old'));
  assert(oldMomentCall && !oldMomentCall.aborted);

  const secondRoute = new AbortController();
  const secondMoment = byId['memory-moment-diagnostics'].activate({
    state: { params: { bucket_id: 'fresh' } }, signal: secondRoute.signal, scopeId: 'panel:moments',
  });
  await Promise.all([firstMoment, secondMoment]);
  assert(oldMomentCall.aborted, 'same-panel route reload must abort the superseded request');
  assert(roots['memory-moment-diagnostics'].querySelector('[data-role="results"]').innerHTML.includes('fresh moment'));

  const recallRoute = new AbortController();
  await byId['memory-recall-diagnostics'].activate({
    state: { params: { q: 'retry' } }, signal: recallRoute.signal, scopeId: 'panel:recall',
  });
  roots['memory-recall-diagnostics'].click('retry');
  await wait();
  const recallCalls = calls.filter((call) => call.path.startsWith('/api/recall-debug?'));
  assert.strictEqual(recallCalls.length, 2);
  assert.notStrictEqual(recallCalls[0].options.signal, recallCalls[1].options.signal,
    'retry must receive a fresh signal');
  assert(!recallCalls[1].options.signal.aborted);
  assert(roots['memory-recall-diagnostics'].querySelector('[data-role="results"]').innerHTML.includes('fresh recall'));

  const diffusionRoute = new AbortController();
  const diffusionActivation = byId['memory-diffusion-diagnostics'].activate({
    state: { params: { q: 'cancel diffusion' } }, signal: diffusionRoute.signal,
    scopeId: 'panel:diffusion',
  });
  await wait();
  const diffusionCall = calls[calls.length - 1];
  diffusionRoute.abort();
  await diffusionActivation;
  assert(diffusionCall.aborted, 'route transition signal must reach the diffusion request');

  const gatewayRoute = new AbortController();
  const gatewayActivation = byId['memory-gateway-injections'].activate({
    state: { params: {} }, signal: gatewayRoute.signal, scopeId: 'panel:gateway',
  });
  await wait();
  const gatewayCall = calls[calls.length - 1];
  byId['memory-gateway-injections'].deactivate();
  await gatewayActivation;
  assert(gatewayCall.aborted, 'deactivation must abort the Gateway request');
})();
""".replace("__ASSET__", json.dumps(str(ASSET)))

    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_memory_insights_confirmation_fails_closed_and_uses_window_fallback() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the browser-module behavior contract")

    script = r"""
const assert = require('assert');
global.window = {};
require(__ASSET__);

function response(payload) {
  return { ok: true, status: 200, json: async () => payload };
}
function wait() { return new Promise((resolve) => setTimeout(resolve, 10)); }

const posts = [];
const api = {
  get: async (path) => {
    if (path.startsWith('/api/word-map?')) return response({ stats: {}, nodes: [], edges: [] });
    if (path.startsWith('/api/identity-semantics?')) return response({ enabled: false, stats: {}, aliases: [] });
    throw new Error('Unexpected GET ' + path);
  },
  post: async (path, body) => {
    posts.push({ path, body });
    return response({ stats: {}, nodes: [], edges: [] });
  },
};
class Element {
  constructor() {
    this.innerHTML = ''; this.textContent = ''; this.value = ''; this.checked = false;
    this.disabled = false; this.attrs = {};
  }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name] || null; }
}
class Root extends Element {
  constructor() { super(); this.elements = {}; this.listeners = {}; }
  querySelector(selector) {
    if (!this.elements[selector]) this.elements[selector] = new Element();
    return this.elements[selector];
  }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  contains() { return true; }
  click(action) {
    const button = new Element();
    button.setAttribute('data-action', action);
    button.closest = () => button;
    this.listeners.click({ target: button });
    return button;
  }
}

(async () => {
  const panels = [];
  const app = {
    api,
    ui: {
      escape: (value) => String(value == null ? '' : value),
      escapeAttr: (value) => String(value == null ? '' : value),
      setStatus: (element, message, tone) => { element.textContent = message; element.tone = tone; },
    },
    registerPanel: (panel) => panels.push(panel),
  };
  window.OmbreDashboardFeatureFactories[0](app);
  const panel = panels.find((item) => item.id === 'memory-word-map');
  const root = new Root();
  panel.mount(root);

  const noSurfaceButton = root.click('rebuild-word-map');
  await wait();
  assert.strictEqual(posts.length, 0, 'destructive rebuild must fail closed without any confirm surface');
  assert.strictEqual(noSurfaceButton.disabled, false);

  let fallbackCalls = 0;
  window.confirm = () => { fallbackCalls += 1; return false; };
  const rejectedButton = root.click('rebuild-word-map');
  await wait();
  assert.strictEqual(fallbackCalls, 1);
  assert.strictEqual(posts.length, 0, 'a rejected window.confirm fallback must not rebuild');
  assert.strictEqual(rejectedButton.disabled, false);

  window.confirm = () => { fallbackCalls += 1; return true; };
  const acceptedButton = root.click('rebuild-word-map');
  await wait();
  assert.strictEqual(fallbackCalls, 2);
  assert.strictEqual(posts.filter((call) => call.path === '/api/word-map/rebuild').length, 1);
  assert.strictEqual(acceptedButton.disabled, false);
})();
""".replace("__ASSET__", json.dumps(str(ASSET)))

    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
