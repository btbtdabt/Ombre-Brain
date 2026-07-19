from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / "frontend" / "dashboard-assets"
SCRIPT_PATH = ASSET_ROOT / "memory-profile.js"
STYLE_PATH = ASSET_ROOT / "memory-profile.css"


def _script() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def _style() -> str:
    return STYLE_PATH.read_text(encoding="utf-8")


def test_memory_profile_asset_registers_every_distinct_panel() -> None:
    source = _script()

    assert "window.OmbreDashboardFeatureFactories" in source
    assert "app.registerPanel" in source
    assert set(re.findall(r"id:\s*'([^']+)'", source)) >= {
        "memory-persona-state",
        "memory-portrait",
        "memory-profile-facts",
        "memory-profile-proposals",
        "memory-anchor-proposals",
    }
    assert source.count("workspace: 'memory'") == 5
    assert source.count("mount: function (root)") == 5
    assert len(re.findall(r"(?m)^\s+activate: function \(", source)) == 5
    assert len(re.findall(r"(?m)^\s+deactivate: function \(", source)) >= 3
    assert "context && context.scopeId" in source


def test_memory_profile_asset_uses_only_the_unified_app_boundary() -> None:
    source = _script()

    assert "app.api" in source
    assert "app.ui" in source
    assert "app.api.readJson" in source
    assert "await app.api[method](path, options.body, requestOptions)" in source
    assert "await app.api[method](path, requestOptions)" in source
    assert "delete requestOptions.body" in source
    assert "JSON.stringify(options.body)" not in source
    assert "app.ui.escape" in source
    assert "app.ui.escapeAttr" in source
    assert "app.ui.setStatus" in source
    assert "app.ui.confirm" in source
    assert "authFetch" not in source
    assert "BASE" not in source
    assert "showDetail" not in source
    assert "loadBuckets" not in source
    assert "document.getElementById" not in source
    assert "fetch(" not in source
    assert "onclick=" not in source


def test_profile_reads_are_scoped_abortable_and_ignore_stale_responses() -> None:
    node_executable = shutil.which("node")
    if not node_executable:
        raise AssertionError("Node.js is required for dashboard runtime tests")

    harness = r"""
const source = __SOURCE__;
const window = { OmbreDashboardFeatureFactories: [], AbortController, confirm: () => true };
global.window = window;
eval(source);

function fakeNode() {
  return {
    innerHTML: '', textContent: '', value: '', hidden: false, dataset: {},
    classList: { add() {} },
    addEventListener() {},
    querySelector() { return null; },
    focus() {},
  };
}

function fakeRoot() {
  const roles = new Map();
  const listeners = new Map();
  const value = fakeNode();
  value.querySelector = (selector) => {
    if (!roles.has(selector)) roles.set(selector, fakeNode());
    return roles.get(selector);
  };
  value.addEventListener = (type, listener) => listeners.set(type, listener);
  value.changeSession = (sessionId) => {
    const select = { value: sessionId, dataset: { action: 'persona-session' } };
    listeners.get('change')({ target: { closest: () => select } });
  };
  value.refresh = (action) => {
    const button = { dataset: { action } };
    listeners.get('click')({ target: { closest: () => button } });
  };
  return value;
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function response(payload) { return { ok: true, payload }; }
function personaPayload(sessionId, marker) {
  return {
    active_session_id: sessionId,
    state: {
      affect: { mood_label: marker, valence: 0.5, arousal: 0.3 },
      relationship: {}, personality: {},
    },
    config: {}, sessions: [{ session_id: sessionId }], events: [],
  };
}
function portraitPayload(marker) {
  return {
    self_anchor_entry: { text: marker },
    portrait: { user: {}, persona: { stable: marker }, relationship: {} },
    current_focus_items: [], recent_timeline: [], stable_candidates: [],
    profile_fact_candidates: [], generation_status: {},
  };
}
function factsPayload(marker) {
  return { facts: [{ id: marker, fact: marker, active: true, evidence: [] }] };
}

const pending = { persona: [], portrait: [], facts: [] };
const requestOptions = [];
const api = {
  readJson: async (result) => result.payload,
  get(path, options) {
    const item = deferred();
    requestOptions.push({ path, options, signal: options && options.signal });
    if (path.startsWith('/api/persona')) pending.persona.push(item);
    else if (path === '/api/portrait-state') pending.portrait.push(item);
    else if (path === '/api/profile-facts') pending.facts.push(item);
    else throw new Error('unexpected GET ' + path);
    return item.promise;
  },
};
const panels = [];
const app = {
  api,
  ui: {
    escape(value) {
      return String(value == null ? '' : value).replaceAll('&', '&amp;').replaceAll('<', '&lt;');
    },
    escapeAttr(value) { return this.escape(value); },
    setStatus(element, message, tone) {
      element.textContent = message;
      element.dataset.tone = tone;
    },
    confirm: async () => true,
  },
  registerPanel(panel) { panels.push(panel); },
};
window.OmbreDashboardFeatureFactories[0](app);
const byId = Object.fromEntries(panels.map((panel) => [panel.id, panel]));
const roots = { persona: fakeRoot(), portrait: fakeRoot(), facts: fakeRoot() };
byId['memory-persona-state'].mount(roots.persona);
byId['memory-portrait'].mount(roots.portrait);
byId['memory-profile-facts'].mount(roots.facts);

(async () => {
  const initialPersona = byId['memory-persona-state'].activate({ scopeId: 'panel:memory-persona-state' });
  pending.persona[0].resolve(response(personaPayload('initial', 'Initial persona')));
  await initialPersona;

  roots.persona.changeSession('old-session');
  roots.persona.changeSession('new-session');
  const oldPersonaRequest = requestOptions.find((item) => item.path.includes('old-session'));
  const oldPersonaSignalAborted = oldPersonaRequest.signal.aborted;
  pending.persona[2].resolve(response(personaPayload('new-session', 'New persona')));
  await new Promise((done) => setTimeout(done, 0));
  pending.persona[1].resolve(response(personaPayload('old-session', 'Old persona')));
  await new Promise((done) => setTimeout(done, 0));

  const oldPortrait = byId['memory-portrait'].activate({ scopeId: 'panel:memory-portrait' });
  const newPortrait = byId['memory-portrait'].activate({ scopeId: 'panel:memory-portrait' });
  const oldPortraitRequest = requestOptions.filter((item) => item.path === '/api/portrait-state')[0];
  const oldPortraitSignalAborted = oldPortraitRequest.signal.aborted;
  pending.portrait[1].resolve(response(portraitPayload('New portrait')));
  await newPortrait;
  pending.portrait[0].resolve(response(portraitPayload('Old portrait')));
  await oldPortrait;

  const oldFacts = byId['memory-profile-facts'].activate({ scopeId: 'panel:memory-profile-facts' });
  const newFacts = byId['memory-profile-facts'].activate({ scopeId: 'panel:memory-profile-facts' });
  const oldFactsRequest = requestOptions.filter((item) => item.path === '/api/profile-facts')[0];
  const oldFactsSignalAborted = oldFactsRequest.signal.aborted;
  pending.facts[1].resolve(response(factsPayload('New fact')));
  await newFacts;
  pending.facts[0].resolve(response(factsPayload('Old fact')));
  await oldFacts;

  const portraitBeforeDeactivate = roots.portrait.querySelector('[data-role="content"]').innerHTML;
  roots.portrait.refresh('portrait-refresh');
  const deactivatedRequest = requestOptions.filter((item) => item.path === '/api/portrait-state').at(-1);
  byId['memory-portrait'].deactivate({ reason: 'navigation' });
  const navigationSignalAborted = deactivatedRequest.signal.aborted;
  pending.portrait[2].resolve(response(portraitPayload('Deactivated portrait')));
  await new Promise((done) => setTimeout(done, 0));

  process.stdout.write(JSON.stringify({
    persona: roots.persona.querySelector('[data-role="content"]').innerHTML,
    portraitBeforeDeactivate,
    portrait: roots.portrait.querySelector('[data-role="content"]').innerHTML,
    facts: roots.facts.querySelector('[data-role="list"]').innerHTML,
    oldPersonaSignalAborted,
    oldPortraitSignalAborted,
    oldFactsSignalAborted,
    navigationSignalAborted,
    forwardedOptions: requestOptions.every((item) =>
      item.options && item.options.signal && item.options.timeoutMs === 15000),
  }));
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
""".replace("__SOURCE__", json.dumps(_script()))
    result = subprocess.run(
        [node_executable, "-"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=harness,
    )
    runtime = json.loads(result.stdout)

    assert "New persona" in runtime["persona"]
    assert "Old persona" not in runtime["persona"]
    assert "New portrait" in runtime["portraitBeforeDeactivate"]
    assert "Old portrait" not in runtime["portraitBeforeDeactivate"]
    assert "Deactivated portrait" not in runtime["portrait"]
    assert "New fact" in runtime["facts"]
    assert "Old fact" not in runtime["facts"]
    assert runtime["oldPersonaSignalAborted"] is True
    assert runtime["oldPortraitSignalAborted"] is True
    assert runtime["oldFactsSignalAborted"] is True
    assert runtime["navigationSignalAborted"] is True
    assert runtime["forwardedOptions"] is True


def test_persona_panel_preserves_state_sessions_and_event_details() -> None:
    source = _script()

    assert "'/api/persona?events_limit=20&sessions_limit=30'" in source
    for marker in (
        "active_session_id",
        "mood_label",
        "inner_thought",
        "affect_delta",
        "relationship_delta",
        "personality_delta",
        "confidence",
        "message_hash",
    ):
        assert marker in source


def test_portrait_panel_covers_every_current_mutation_contract() -> None:
    source = _script()

    for method, route in (
        ("get", "/api/portrait-state"),
        ("post", "/api/portrait-maintain"),
        ("post", "/api/portrait-state/items"),
        ("put", "/api/portrait-state/items"),
        ("delete", "/api/portrait-state/items"),
        ("put", "/api/portrait-state/stable"),
        ("post", "/api/portrait-state/stable/lock"),
        ("post", "/api/portrait-state/stable/rollback"),
        ("post", "/api/portrait-state/reset"),
    ):
        assert f"apiJson('{method}', '{route}'" in source

    for payload_field in (
        "expected_revision",
        "target_revision",
        "expected_text",
        "stable_revision",
        "stable_locked",
        "evidence",
        "self_anchor_entry",
        "current_focus_items",
        "recent_timeline",
        "recent_activities",
        "stable_candidates",
        "profile_fact_candidates",
    ):
        assert payload_field in source
    assert "confirm: 'DELETE'" in source
    assert "confirm: 'RESET'" in source


def test_profile_fact_and_proposal_contracts_remain_distinct() -> None:
    source = _script()

    assert "apiJson('get', '/api/profile-facts'" in source
    assert "'/api/profile-facts/' + encodeURIComponent(id)" in source
    assert "apiJson('post', '/api/profile-fact-proposals'" in source
    assert "apiJson('post', '/api/profile-fact-proposals/confirm'" in source
    assert "apiJson('post', '/api/anchor-proposals'" in source
    assert "apiJson('post', '/api/anchor-proposals/confirm'" in source
    for marker in (
        "evidence_bucket_id",
        "evidence_moment_id",
        "max_proposals: 3",
        "profile_kind",
        "subject",
        "predicate",
        "object",
        "future_use",
        "anchor_kind",
    ):
        assert marker in source
    assert "action: 'confirm'" in source
    assert "action: 'deprecate'" in source
    assert "action: 'edit'" in source


def test_memory_profile_styles_cover_panels_feedback_and_mobile() -> None:
    source = _style()

    for marker in (
        ".ob-memory-profile",
        ".ob-persona-grid",
        ".ob-portrait-grid",
        ".ob-profile-grid",
        ".ob-status[data-tone=\"error\"]",
        ".ob-empty",
        "@media (max-width: 760px)",
    ):
        assert marker in source


def test_proposal_confirmations_are_single_flight_and_restore_buttons() -> None:
    node_executable = shutil.which("node")
    if not node_executable:
        raise AssertionError("Node.js is required for dashboard runtime tests")

    harness = r"""
const assert = require('assert');
const source = __SOURCE__;
const window = { OmbreDashboardFeatureFactories: [], AbortController, confirm: () => true };
global.window = window;
eval(source);

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function response(payload) { return { ok: true, payload }; }
function tick() { return new Promise((resolve) => setTimeout(resolve, 0)); }

class Node {
  constructor() {
    this.innerHTML = ''; this.textContent = ''; this.value = ''; this.hidden = false;
    this.disabled = false; this.dataset = {}; this.listeners = {};
    this.classList = { add() {} };
  }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  emit(type, event) { for (const listener of this.listeners[type] || []) listener(event); }
  querySelector() { return null; }
}
class Root extends Node {
  constructor() { super(); this.roles = new Map(); }
  querySelector(selector) {
    if (!this.roles.has(selector)) this.roles.set(selector, new Node());
    return this.roles.get(selector);
  }
  click(button) {
    button.closest = () => button;
    this.emit('click', { target: button });
  }
}
function proposalButton(action, index) {
  const button = new Node();
  button.dataset = { action, index: String(index) };
  return button;
}

const profileWrite = deferred();
const anchorWrite = deferred();
const calls = [];
const api = {
  readJson: async (result) => result.payload,
  post(path, body, options) {
    calls.push({ path, body, options });
    if (path === '/api/profile-fact-proposals') {
      return Promise.resolve(response({ proposals: [{
        fact: 'Likes tea', profile_kind: 'preference', subject: 'user', predicate: 'likes',
        object: 'tea', evidence_bucket_id: 'profile-bucket', confidence: 0.9,
      }] }));
    }
    if (path === '/api/anchor-proposals') {
      return Promise.resolve(response({ proposals: [{
        bucket_id: 'anchor-bucket', anchor_kind: 'identity', confidence: 0.9,
      }], bucket: { name: 'Anchor candidate' } }));
    }
    if (path === '/api/profile-fact-proposals/confirm') return profileWrite.promise;
    if (path === '/api/anchor-proposals/confirm') return anchorWrite.promise;
    throw new Error('unexpected POST ' + path);
  },
};
let confirmCalls = 0;
const panels = [];
const app = {
  api,
  ui: {
    escape: (value) => String(value == null ? '' : value),
    escapeAttr: (value) => String(value == null ? '' : value),
    setStatus: (element, message, tone) => { element.textContent = message; element.dataset.tone = tone; },
    confirm: async () => { confirmCalls += 1; return true; },
  },
  registerPanel: (panel) => panels.push(panel),
};
window.OmbreDashboardFeatureFactories[0](app);
const byId = Object.fromEntries(panels.map((panel) => [panel.id, panel]));
const profileRoot = new Root();
const anchorRoot = new Root();
byId['memory-profile-proposals'].mount(profileRoot);
byId['memory-anchor-proposals'].mount(anchorRoot);

(async () => {
  profileRoot.querySelector('[data-role="bucket-id"]').value = 'profile-bucket';
  const profileForm = profileRoot.querySelector('[data-role="form"]');
  profileForm.emit('submit', { preventDefault() {} });
  await tick();

  const profileButton = proposalButton('profile-proposal-confirm', 0);
  profileRoot.click(profileButton);
  profileRoot.click(profileButton);
  assert.strictEqual(profileButton.disabled, true);
  await tick();
  assert.strictEqual(confirmCalls, 1, 'a double click must share one profile confirmation flight');
  assert.strictEqual(calls.filter((call) => call.path === '/api/profile-fact-proposals/confirm').length, 1);
  assert.strictEqual(profileButton.disabled, true, 'the profile button stays disabled while the write is pending');
  profileWrite.resolve(response({ id: 'fact-1' }));
  await tick();
  assert.strictEqual(profileButton.disabled, false, 'the profile button is restored after success');

  anchorRoot.querySelector('[data-role="bucket-id"]').value = 'anchor-bucket';
  const anchorForm = anchorRoot.querySelector('[data-role="form"]');
  anchorForm.emit('submit', { preventDefault() {} });
  await tick();

  const anchorButton = proposalButton('anchor-proposal-confirm', 0);
  anchorRoot.click(anchorButton);
  anchorRoot.click(anchorButton);
  assert.strictEqual(anchorButton.disabled, true);
  await tick();
  assert.strictEqual(confirmCalls, 2, 'a double click must share one anchor confirmation flight');
  assert.strictEqual(calls.filter((call) => call.path === '/api/anchor-proposals/confirm').length, 1);
  assert.strictEqual(anchorButton.disabled, true, 'the anchor button stays disabled while the write is pending');
  anchorWrite.reject(new Error('write failed'));
  await tick();
  assert.strictEqual(anchorButton.disabled, false, 'the anchor button is restored after failure');
  assert(profileRoot.querySelector('[data-role="status"]').textContent.includes('已写入'));
  assert(anchorRoot.querySelector('[data-role="status"]').textContent.includes('写入失败'));
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
""".replace("__SOURCE__", json.dumps(_script()))

    result = subprocess.run(
        [node_executable, "-"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=harness,
    )
    assert result.returncode == 0, result.stderr or result.stdout
