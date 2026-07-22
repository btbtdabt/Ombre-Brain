from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / "frontend" / "dashboard-assets"
MODULE = ASSET_ROOT / "memory-care.js"
STYLES = ASSET_ROOT / "memory-care.css"


def _module_source() -> str:
    return MODULE.read_text(encoding="utf-8")


def test_memory_care_factory_registers_every_distinct_panel() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the dashboard feature contract")

    harness = f"""
const vm = require('vm');
const source = {json.dumps(_module_source())};
const appended = [];
const document = {{
  baseURI: 'https://brain.example.test/memory-dashboard',
  getElementById: () => null,
  createElement: () => ({{ setAttribute() {{}}, dataset: {{}} }}),
  head: {{ appendChild: (node) => appended.push(node) }}
}};
const window = {{ OmbreDashboardFeatureFactories: [] }};
vm.runInNewContext(source, {{ window, document, URL, console, setTimeout, clearTimeout }});
if (window.OmbreDashboardFeatureFactories.length !== 1) throw new Error('factory missing');
const panels = [];
const app = {{
  api: {{}},
  ui: {{}},
  registerPanel(panel) {{ panels.push(panel); }}
}};
window.OmbreDashboardFeatureFactories[0](app);
const summary = panels.map((panel) => ({{
  id: panel.id,
  workspace: panel.workspace,
  label: panel.label,
  order: panel.order,
  mount: typeof panel.mount,
  activate: typeof panel.activate,
}}));
process.stdout.write(JSON.stringify(summary));
"""
    result = subprocess.run(
        [node, "-"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=harness,
    )
    panels = json.loads(result.stdout)

    assert [panel["id"] for panel in panels] == [
        "memory-reminders",
        "memory-reflection",
        "memory-chat-memory",
        "memory-dreams",
        "memory-darkroom",
    ]
    assert all(panel["workspace"] == "memory" for panel in panels)
    assert all(panel["label"] for panel in panels)
    assert [panel["order"] for panel in panels] == sorted(panel["order"] for panel in panels)
    assert all(panel["mount"] == "function" for panel in panels)
    assert all(panel["activate"] == "function" for panel in panels)


def test_memory_care_panels_activate_render_paginated_history_and_send_exact_payloads() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the dashboard feature contract")

    harness = """
(async function () {
const vm = require('vm');
const source = __SOURCE__;

function fakeNode() {
  const children = new Map();
  const listeners = Object.create(null);
  const attrs = Object.create(null);
  return {
    innerHTML: '', textContent: '', value: '', checked: false, hidden: false,
    disabled: false, dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener(name, listener) {
      (listeners[name] = listeners[name] || []).push(listener);
    },
    async dispatch(name, target) {
      const event = { target, preventDefault() {} };
      await Promise.all((listeners[name] || []).map((listener) => listener(event)));
    },
    contains() { return true; },
    focus() {}, reset() {},
    setAttribute(name, value) { attrs[name] = String(value); },
    getAttribute(name) { return attrs[name] || null; },
    querySelector(selector) {
      if (!children.has(selector)) children.set(selector, fakeNode());
      return children.get(selector);
    },
    querySelectorAll() { return []; },
    closest() { return null; },
  };
}

const document = {
  baseURI: 'https://brain.example.test/memory-dashboard',
  getElementById: () => null,
  createElement: fakeNode,
  head: { appendChild() {} },
};
const window = { OmbreDashboardFeatureFactories: [], confirm: () => true };
vm.runInNewContext(source, {
  window, document, URL, console, setTimeout, clearTimeout, Map, Set, Date, Promise,
  Object, Array, String, Number, Boolean, RegExp, Error, encodeURIComponent,
});

const calls = [];
const now = new Date();
const today = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
function response(payload) { return { ok: true, status: 200, payload }; }
const api = {
  readJson: async (result) => result.payload,
  get: async (path) => {
    calls.push({ method: 'GET', path });
    if (path.startsWith('/api/reminders')) return response({ reminders: [] });
    if (path === '/api/buckets/light?include_archive=false&limit=2000&offset=0&type=feel&tags=relationship_weather,daily_impression&sort=created_desc') {
      return response({ buckets: Array(2000).fill({ type: 'feel', tags: ['relationship_weather', 'daily_impression'] }), count: 2001 });
    }
    if (path === '/api/buckets/light?include_archive=false&limit=2000&offset=2000&type=feel&tags=relationship_weather,daily_impression&sort=created_desc') {
      return response({
        buckets: [{
          id: 'reflection_daily_' + today,
          name: 'Older paginated daily impression',
          type: 'feel',
          tags: ['relationship_weather', 'daily_impression'],
          created: today + 'T04:00:00Z',
        }],
        count: 2001,
      });
    }
    if (path.startsWith('/api/bucket/')) return response({ content: 'Full paginated impression body' });
    if (path.startsWith('/api/daily-chat-memory/pending')) return response({
      items: [{
        id: 'candidate-live',
        status: 'pending',
        date: today,
        candidate: {
          id: 'candidate-live',
          title: 'Live memory candidate',
          kind: 'key_event',
          content: 'A non-empty candidate must render its editable fields.',
          domain: ['project'],
          tags: ['dashboard'],
          importance: 7,
          confidence: 0.91,
        },
      }],
    });
    if (path.startsWith('/api/dreams')) return response({ records: [] });
    if (path === '/api/darkroom/status') return response({ status: 'ok', count: 0, door: 'Darkroom Door' });
    throw new Error('unexpected GET ' + path);
  },
  post: async (path, body) => {
    calls.push({ method: 'POST', path, body });
    return response({ status: 'ok' });
  },
  patch: async (path, body) => {
    calls.push({ method: 'PATCH', path, body });
    return response({ status: 'ok' });
  },
};
const panels = [];
const app = {
  api,
  ui: {
    escape: (value) => String(value == null ? '' : value),
    escapeAttr: (value) => String(value == null ? '' : value),
    setStatus(element, message, tone) { element.textContent = message; element.dataset.tone = tone; },
    confirm: async () => true,
  },
  store: { invalidate() {} },
  registerPanel(panel) { panels.push(panel); },
};
window.OmbreDashboardFeatureFactories[0](app);
const roots = Object.create(null);
for (const panel of panels) {
  roots[panel.id] = fakeNode();
  panel.mount(roots[panel.id]);
  await panel.activate();
}

function actionButton(action) {
  const button = fakeNode();
  button.dataset.action = action;
  button.textContent = action;
  button.closest = (selector) => selector === '[data-action]' ? button : null;
  return button;
}
const reflectionRoot = roots['memory-reflection'];
reflectionRoot.querySelector('[name="reflection_force"]').checked = true;
await reflectionRoot.dispatch('click', actionButton('run-reflection'));
reflectionRoot.querySelector('[name="activity_date"]').value = today;
reflectionRoot.querySelector('[name="activity_force"]').checked = false;
await reflectionRoot.dispatch('click', actionButton('run-activity'));

const chatRoot = roots['memory-chat-memory'];
function decisionButton(action, id) {
  const card = fakeNode();
  card.dataset.candidateId = id;
  card.dataset.editing = 'false';
  const button = fakeNode();
  button.dataset.action = action;
  button.textContent = action;
  button.closest = (selector) => selector === '[data-action]' ? button : card;
  return button;
}
await chatRoot.dispatch('click', decisionButton('confirm-candidate', 'candidate-confirm'));
await chatRoot.dispatch('click', decisionButton('reject-candidate', 'candidate-reject'));

const capCalls = [];
const capPage = Array(2000).fill({ type: 'dynamic', tags: [] });
const capApi = {
  readJson: async (result) => result.payload,
  get: async (path) => {
    capCalls.push(path);
    if (!path.startsWith('/api/buckets/light')) throw new Error('unexpected cap GET ' + path);
    return response({ buckets: capPage, count: 100001 });
  },
};
const capPanels = [];
const capApp = {
  api: capApi,
  ui: app.ui,
  store: { invalidate() {} },
  registerPanel(panel) { capPanels.push(panel); },
};
window.OmbreDashboardFeatureFactories[0](capApp);
const capReflection = capPanels.find((panel) => panel.id === 'memory-reflection');
const capRoot = fakeNode();
capReflection.mount(capRoot);
await capReflection.activate();

async function runReflectionScenario(scenarioApi) {
  const scenarioPanels = [];
  const scenarioApp = {
    api: scenarioApi,
    ui: app.ui,
    store: { invalidate() {} },
    registerPanel(panel) { scenarioPanels.push(panel); },
  };
  window.OmbreDashboardFeatureFactories[0](scenarioApp);
  const panel = scenarioPanels.find((item) => item.id === 'memory-reflection');
  const root = fakeNode();
  panel.mount(root);
  await panel.activate();
  return root.querySelector('[data-role="reflection-calendar"]').innerHTML;
}
const oversizedArrayError = await runReflectionScenario({
  readJson: async (result) => result.payload,
  get: async () => response(Array(200001).fill({ type: 'dynamic', tags: [] })),
});
const stableFirstPage = Array.from({ length: 2000 }, (_, index) => ({
  id: 'stable-' + index, type: 'dynamic', tags: [],
}));
let changingPage = 0;
const changedTotalError = await runReflectionScenario({
  readJson: async (result) => result.payload,
  get: async () => {
    changingPage += 1;
    return response({
      buckets: changingPage === 1 ? stableFirstPage : Array.from({ length: 2000 }, (_, index) => ({
        id: 'changed-' + index, type: 'dynamic', tags: [],
      })),
      count: changingPage === 1 ? 4000 : 3999,
    });
  },
});
let duplicatePage = 0;
const duplicateError = await runReflectionScenario({
  readJson: async (result) => result.payload,
  get: async () => {
    duplicatePage += 1;
    return response({
      buckets: duplicatePage === 1 ? stableFirstPage : Array.from({ length: 2000 }, (_, index) => ({
        id: index === 0 ? 'stable-1999' : 'second-' + index,
        type: 'dynamic', tags: [],
      })),
      count: 4000,
    });
  },
});

let historyGeneration = 0;
let releaseOldDetail;
const stalePanels = [];
const staleApi = {
  readJson: async (result) => result.payload,
  get: async (path) => {
    if (path.startsWith('/api/buckets/light')) {
      historyGeneration += 1;
      const current = historyGeneration === 1 ? 'old' : 'new';
      return response({
        buckets: [{
          id: 'reflection_daily_' + today + '-' + current,
          name: current === 'old' ? 'Old generation impression' : 'New generation impression',
          type: 'feel',
          tags: ['relationship_weather', 'daily_impression'],
          created: today + 'T04:00:00Z',
          date: today,
        }],
        count: 1,
      });
    }
    if (path.endsWith('-old')) {
      return new Promise((resolve) => {
        releaseOldDetail = () => resolve(response({ content: 'Old detail' }));
      });
    }
    if (path.endsWith('-new')) return response({ content: 'New detail' });
    throw new Error('unexpected stale GET ' + path);
  },
};
window.OmbreDashboardFeatureFactories[0]({
  api: staleApi,
  ui: app.ui,
  store: { invalidate() {} },
  registerPanel(panel) { stalePanels.push(panel); },
});
const staleReflection = stalePanels.find((panel) => panel.id === 'memory-reflection');
const staleRoot = fakeNode();
staleReflection.mount(staleRoot);
await staleReflection.activate();
await staleReflection.activate();
await new Promise((resolve) => setTimeout(resolve, 0));
releaseOldDetail();
await new Promise((resolve) => setTimeout(resolve, 0));

let detailRequests = 0;
let activeDetailRequests = 0;
let maxActiveDetailRequests = 0;
const boundedDetailPanels = [];
window.OmbreDashboardFeatureFactories[0]({
  api: {
    readJson: async (result) => result.payload,
        get: async (path) => {
          if (path.startsWith('/api/buckets/light')) {
            return response({
          buckets: Array.from({ length: 25 }, (_, index) => ({
            id: 'reflection-daily-bounded-' + index,
            name: 'Bounded impression ' + index,
            type: 'feel',
            tags: ['relationship_weather', 'daily_impression'],
            date: today,
          })),
              count: 25,
            });
          }
          if (path.startsWith('/api/search-raw?')) {
            return response({ items: [], count: 0 });
          }
          detailRequests += 1;
      activeDetailRequests += 1;
      maxActiveDetailRequests = Math.max(maxActiveDetailRequests, activeDetailRequests);
      await new Promise((resolve) => setTimeout(resolve, 0));
      activeDetailRequests -= 1;
      return response({ content: 'bounded detail' });
    },
  },
  ui: app.ui,
  registerPanel(panel) { boundedDetailPanels.push(panel); },
});
const boundedDetailPanel = boundedDetailPanels.find((panel) => panel.id === 'memory-reflection');
const boundedDetailRoot = fakeNode();
boundedDetailPanel.mount(boundedDetailRoot);
await boundedDetailPanel.activate();
for (let tick = 0; tick < 12; tick += 1) {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

const candidateConfirmResolvers = [];
const candidatePosts = [];
const guardedCandidatePanels = [];
window.OmbreDashboardFeatureFactories[0]({
  api: {
    readJson: async (result) => result.payload,
    get: async () => response({ items: [] }),
    post: async (_path, body) => {
      candidatePosts.push(body);
      return response({ status: 'ok' });
    },
  },
  ui: Object.assign({}, app.ui, {
    confirm: () => new Promise((resolve) => candidateConfirmResolvers.push(resolve)),
  }),
  store: { invalidate() {} },
  registerPanel(panel) { guardedCandidatePanels.push(panel); },
});
const guardedCandidatePanel = guardedCandidatePanels.find((panel) => panel.id === 'memory-chat-memory');
const guardedCandidateRoot = fakeNode();
guardedCandidatePanel.mount(guardedCandidateRoot);
await guardedCandidatePanel.activate();
function guardedCandidateButton(action) {
  const card = fakeNode();
  card.dataset.candidateId = 'guarded-candidate';
  card.dataset.editing = 'false';
  const button = fakeNode();
  button.dataset.action = action;
  button.textContent = action;
  button.closest = (selector) => selector === '[data-action]' ? button : card;
  return button;
}
const firstCandidateDecision = guardedCandidateRoot.dispatch(
  'click', guardedCandidateButton('confirm-candidate'),
);
await Promise.resolve();
const secondCandidateDecision = guardedCandidateRoot.dispatch(
  'click', guardedCandidateButton('reject-candidate'),
);
await Promise.resolve();
if (candidateConfirmResolvers.length > 1) {
  candidateConfirmResolvers[1](true);
  await secondCandidateDecision;
  candidateConfirmResolvers[0](true);
  await firstCandidateDecision;
} else {
  candidateConfirmResolvers[0](true);
  await Promise.all([firstCandidateDecision, secondCandidateDecision]);
}

const crossConfirmResolvers = [];
const crossPosts = [];
const crossPanels = [];
window.OmbreDashboardFeatureFactories[0]({
  api: {
    readJson: async (result) => result.payload,
    get: async () => response({ items: [] }),
    post: async (path, body) => {
      crossPosts.push({ path, body });
      return response({ status: 'ok' });
    },
  },
  ui: Object.assign({}, app.ui, {
    confirm: () => new Promise((resolve) => crossConfirmResolvers.push(resolve)),
  }),
  store: { invalidate() {} },
  registerPanel(panel) { crossPanels.push(panel); },
});
const crossPanel = crossPanels.find((panel) => panel.id === 'memory-chat-memory');
const crossRoot = fakeNode();
crossPanel.mount(crossRoot);
await crossPanel.activate();
crossRoot.querySelector('[name="chat_memory_date"]').value = today;
crossRoot.querySelector('[name="chat_memory_mode"]').value = 'review';
const crossCandidateFirst = crossRoot.dispatch(
  'click', guardedCandidateButton('confirm-candidate'),
);
await Promise.resolve();
const crossRunSecond = crossRoot.dispatch('click', actionButton('run-chat-memory'));
await Promise.resolve();
if (crossConfirmResolvers.length > 1) {
  crossConfirmResolvers[1](false);
  await crossRunSecond;
}
crossConfirmResolvers[0](true);
await crossCandidateFirst;

const reverseStart = crossConfirmResolvers.length;
const crossRunFirst = crossRoot.dispatch('click', actionButton('run-chat-memory'));
await Promise.resolve();
const crossCandidateSecond = crossRoot.dispatch(
  'click', guardedCandidateButton('reject-candidate'),
);
await Promise.resolve();
if (crossConfirmResolvers.length > reverseStart + 1) {
  crossConfirmResolvers[reverseStart + 1](false);
  await crossCandidateSecond;
}
crossConfirmResolvers[reverseStart](true);
await crossRunFirst;

const reflectionConfirmResolvers = [];
const guardedReflectionPosts = [];
const guardedReflectionPanels = [];
window.OmbreDashboardFeatureFactories[0]({
  api: {
    readJson: async (result) => result.payload,
    get: async () => response({ buckets: [], count: 0 }),
    post: async (path, body) => {
      guardedReflectionPosts.push({ path, body });
      return response({ status: 'ok' });
    },
  },
  ui: Object.assign({}, app.ui, {
    confirm: () => new Promise((resolve) => reflectionConfirmResolvers.push(resolve)),
  }),
  store: { invalidate() {} },
  registerPanel(panel) { guardedReflectionPanels.push(panel); },
});
const guardedReflectionPanel = guardedReflectionPanels.find((panel) => panel.id === 'memory-reflection');
const guardedReflectionRoot = fakeNode();
guardedReflectionPanel.mount(guardedReflectionRoot);
await guardedReflectionPanel.activate();
guardedReflectionRoot.querySelector('[name="activity_date"]').value = today;
const firstReflectionRun = guardedReflectionRoot.dispatch('click', actionButton('run-reflection'));
await Promise.resolve();
const siblingActivityRun = guardedReflectionRoot.dispatch('click', actionButton('run-activity'));
await Promise.resolve();
if (reflectionConfirmResolvers.length > 1) {
  reflectionConfirmResolvers[1](true);
  await siblingActivityRun;
  reflectionConfirmResolvers[0](true);
  await firstReflectionRun;
} else {
  reflectionConfirmResolvers[0](true);
  await Promise.all([firstReflectionRun, siblingActivityRun]);
}

const chatRunConfirmResolvers = [];
const guardedChatRunPosts = [];
const guardedChatRunPanels = [];
window.OmbreDashboardFeatureFactories[0]({
  api: {
    readJson: async (result) => result.payload,
    get: async () => response({ items: [] }),
    post: async (path, body) => {
      guardedChatRunPosts.push({ path, body });
      return response({ status: 'ok' });
    },
  },
  ui: Object.assign({}, app.ui, {
    confirm: () => new Promise((resolve) => chatRunConfirmResolvers.push(resolve)),
  }),
  store: { invalidate() {} },
  registerPanel(panel) { guardedChatRunPanels.push(panel); },
});
const guardedChatRunPanel = guardedChatRunPanels.find((panel) => panel.id === 'memory-chat-memory');
const guardedChatRunRoot = fakeNode();
guardedChatRunPanel.mount(guardedChatRunRoot);
await guardedChatRunPanel.activate();
guardedChatRunRoot.querySelector('[name="chat_memory_date"]').value = today;
guardedChatRunRoot.querySelector('[name="chat_memory_mode"]').value = 'review';
const firstChatRun = guardedChatRunRoot.dispatch('click', actionButton('run-chat-memory'));
await Promise.resolve();
const siblingChatRun = guardedChatRunRoot.dispatch('click', actionButton('run-chat-memory'));
await Promise.resolve();
if (chatRunConfirmResolvers.length > 1) {
  chatRunConfirmResolvers[1](true);
  await siblingChatRun;
  chatRunConfirmResolvers[0](true);
  await firstChatRun;
} else {
  chatRunConfirmResolvers[0](true);
  await Promise.all([firstChatRun, siblingChatRun]);
}

const reminderConfirmResolvers = [];
const reminderPatches = [];
const guardedReminderPanels = [];
window.OmbreDashboardFeatureFactories[0]({
  api: {
    readJson: async (result) => result.payload,
    get: async () => response({ reminders: [] }),
    patch: async (_path, body) => {
      reminderPatches.push(body);
      return response({ status: 'ok' });
    },
  },
  ui: Object.assign({}, app.ui, {
    confirm: () => new Promise((resolve) => reminderConfirmResolvers.push(resolve)),
  }),
  registerPanel(panel) { guardedReminderPanels.push(panel); },
});
const guardedReminderPanel = guardedReminderPanels.find((panel) => panel.id === 'memory-reminders');
const guardedReminderRoot = fakeNode();
guardedReminderPanel.mount(guardedReminderRoot);
await guardedReminderPanel.activate();
function guardedReminderButton(action) {
  const card = fakeNode();
  card.dataset.reminderId = 'guarded-reminder';
  const button = fakeNode();
  button.dataset.action = action;
  button.dataset.id = 'guarded-reminder';
  button.textContent = action;
  button.closest = (selector) => selector === '[data-action]' ? button : card;
  return button;
}
const archiveDecision = guardedReminderRoot.dispatch(
  'click', guardedReminderButton('archive-reminder'),
);
await Promise.resolve();
const siblingReminderDecision = guardedReminderRoot.dispatch(
  'click', guardedReminderButton('complete-reminder'),
);
await siblingReminderDecision;
reminderConfirmResolvers[0](true);
await archiveDecision;

let dreamDetailCalls = 0;
let releaseOldDream;
const guardedDreamPanels = [];
const dreamRecord = {
  dream_id: 'guarded-dream',
  local_date: today,
  ai_name: 'Aki',
  status: 'latent',
  has_body: true,
};
window.OmbreDashboardFeatureFactories[0]({
  api: {
    readJson: async (result) => result.payload,
    get: async (path) => {
      if (path === '/api/dreams?limit=50') return response({ records: [dreamRecord] });
      dreamDetailCalls += 1;
      if (dreamDetailCalls === 1) {
        return new Promise((resolve) => {
          releaseOldDream = () => resolve(response({ body: 'Old dream body' }));
        });
      }
      return response({ body: 'Fresh dream body' });
    },
  },
  ui: app.ui,
  registerPanel(panel) { guardedDreamPanels.push(panel); },
});
const guardedDreamPanel = guardedDreamPanels.find((panel) => panel.id === 'memory-dreams');
const guardedDreamRoot = fakeNode();
guardedDreamPanel.mount(guardedDreamRoot);
await guardedDreamPanel.activate();
function dreamToggleButton() {
  const row = fakeNode();
  row.dataset.dreamId = 'guarded-dream';
  const body = row.querySelector('[data-role="dream-body"]');
  const button = fakeNode();
  button.dataset.action = 'toggle-dream';
  button.closest = (selector) => selector === '[data-action]' ? button : row;
  return { button, body };
}
const oldDreamToggle = dreamToggleButton();
const oldDreamRequest = guardedDreamRoot.dispatch('click', oldDreamToggle.button);
await Promise.resolve();
await guardedDreamPanel.activate();
releaseOldDream();
await oldDreamRequest;
const freshDreamToggle = dreamToggleButton();
await guardedDreamRoot.dispatch('click', freshDreamToggle.button);

async function oversizedListState(panelId, role, payload) {
  const oversizedPanels = [];
  window.OmbreDashboardFeatureFactories[0]({
    api: {
      readJson: async (result) => result.payload,
      get: async () => response(payload),
    },
    ui: app.ui,
    registerPanel(panel) { oversizedPanels.push(panel); },
  });
  const panel = oversizedPanels.find((item) => item.id === panelId);
  const root = fakeNode();
  panel.mount(root);
  await panel.activate();
  return root.querySelector('[data-role="' + role + '"]').innerHTML;
}
const oversizedReminders = await oversizedListState(
  'memory-reminders', 'reminder-list', { reminders: Array(101).fill({}) },
);
const oversizedCandidates = await oversizedListState(
  'memory-chat-memory', 'candidate-list', { items: Array(101).fill({}) },
);
const oversizedDreams = await oversizedListState(
  'memory-dreams', 'dream-list', { records: Array(51).fill({}) },
);

const result = {
  getPaths: calls.filter((call) => call.method === 'GET').map((call) => call.path),
  decisions: calls.filter((call) => call.path === '/api/daily-chat-memory/confirm').map((call) => call.body),
  reflectionRuns: calls.filter((call) => call.path === '/api/reflection/run').map((call) => call.body),
  activityRuns: calls.filter((call) => call.path === '/api/daily-activity-summary/run').map((call) => call.body),
  reminders: roots['memory-reminders'].querySelector('[data-role="reminder-list"]').innerHTML,
  reflection: roots['memory-reflection'].querySelector('[data-role="reflection-day"]').innerHTML,
  candidates: chatRoot.querySelector('[data-role="candidate-list"]').innerHTML,
  dreams: roots['memory-dreams'].querySelector('[data-role="dream-list"]').innerHTML,
  darkroom: roots['memory-darkroom'].querySelector('[data-role="darkroom-status"]').innerHTML,
  capFetches: capCalls.length,
  capError: capRoot.querySelector('[data-role="reflection-calendar"]').innerHTML,
  changedTotalError,
  duplicateError,
  staleHydration: staleRoot.querySelector('[data-role="reflection-day"]').innerHTML,
  boundedDetailRequests: detailRequests,
  maxActiveDetailRequests,
  boundedDay: boundedDetailRoot.querySelector('[data-role="reflection-day"]').innerHTML,
  oversizedArrayError,
  candidateConfirmCount: candidateConfirmResolvers.length,
  candidatePosts,
  crossConfirmCount: crossConfirmResolvers.length,
  crossPosts,
  reflectionConfirmCount: reflectionConfirmResolvers.length,
  guardedReflectionPosts,
  chatRunConfirmCount: chatRunConfirmResolvers.length,
  guardedChatRunPosts,
  reminderPatches,
  dreamDetailCalls,
  freshDreamBody: freshDreamToggle.body.textContent,
  oversizedReminders,
  oversizedCandidates,
  oversizedDreams,
};
process.stdout.write(JSON.stringify(result));
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
""".replace("__SOURCE__", json.dumps(_module_source()))
    result = subprocess.run(
        [node, "-"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=harness,
    )
    runtime = json.loads(result.stdout)

    assert "/api/reminders?status=active&limit=100" in runtime["getPaths"]
    assert "/api/buckets/light?include_archive=false&limit=2000&offset=0&type=feel&tags=relationship_weather,daily_impression&sort=created_desc" in runtime["getPaths"]
    assert "/api/buckets/light?include_archive=false&limit=2000&offset=2000&type=feel&tags=relationship_weather,daily_impression&sort=created_desc" in runtime["getPaths"]
    assert "/api/daily-chat-memory/pending?status=pending&limit=100" in runtime["getPaths"]
    assert "/api/dreams?limit=50" in runtime["getPaths"]
    assert "/api/darkroom/status" in runtime["getPaths"]
    assert runtime["decisions"] == [
        {"candidate_ids": ["candidate-confirm"], "action": "confirm", "confirm": "WRITE"},
        {"candidate_ids": ["candidate-reject"], "action": "reject", "confirm": "REJECT"},
    ]
    assert runtime["reflectionRuns"] == [{"period": "daily", "force": True}]
    assert runtime["activityRuns"] == [{"date": runtime["activityRuns"][0]["date"], "force": False}]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", runtime["activityRuns"][0]["date"])
    assert 'data-state="empty"' in runtime["reminders"]
    assert "Older paginated daily impression" in runtime["reflection"]
    assert "当天发生了什么" in runtime["reflection"]
    assert "原始事件读取失败" in runtime["reflection"]
    assert 'role="alert"' in runtime["reflection"]
    assert runtime["capFetches"] == 1
    assert 'data-state="error"' in runtime["capError"]
    assert "超过安全读取上限" in runtime["capError"]
    assert 'data-state="error"' in runtime["oversizedArrayError"]
    assert "超过安全读取上限" in runtime["oversizedArrayError"]
    assert 'data-state="error"' in runtime["changedTotalError"]
    assert "分页读取期间发生变化" in runtime["changedTotalError"]
    assert 'data-state="error"' in runtime["duplicateError"]
    assert "重复记录" in runtime["duplicateError"]
    assert "New generation impression" in runtime["staleHydration"]
    assert "New detail" in runtime["staleHydration"]
    assert "Old generation impression" not in runtime["staleHydration"]
    assert runtime["boundedDetailRequests"] == 20
    assert runtime["maxActiveDetailRequests"] <= 4
    assert 'data-action="shift-reflection-day-page"' in runtime["boundedDay"]
    assert "第 1 / 2 页" in runtime["boundedDay"]
    assert "这一天没有保存的原始事件" in runtime["boundedDay"]
    assert 'role="status"' in runtime["boundedDay"]
    assert runtime["candidateConfirmCount"] == 1
    assert runtime["candidatePosts"] == [
        {"candidate_ids": ["guarded-candidate"], "action": "confirm", "confirm": "WRITE"}
    ]
    assert runtime["crossConfirmCount"] == 2
    assert [call["path"] for call in runtime["crossPosts"]] == [
        "/api/daily-chat-memory/confirm",
        "/api/daily-chat-memory/run",
    ]
    assert runtime["reflectionConfirmCount"] == 1
    assert runtime["guardedReflectionPosts"] == [
        {"path": "/api/reflection/run", "body": {"period": "daily", "force": False}}
    ]
    assert runtime["chatRunConfirmCount"] == 1
    assert runtime["guardedChatRunPosts"] == [
        {
            "path": "/api/daily-chat-memory/run",
            "body": {"date": runtime["guardedChatRunPosts"][0]["body"]["date"], "mode": "review", "force": False},
        }
    ]
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", runtime["guardedChatRunPosts"][0]["body"]["date"]
    )
    assert runtime["reminderPatches"] == [{"status": "archived"}]
    assert runtime["dreamDetailCalls"] == 2
    assert runtime["freshDreamBody"] == "Fresh dream body"
    for oversized in (
        runtime["oversizedReminders"],
        runtime["oversizedCandidates"],
        runtime["oversizedDreams"],
    ):
        assert 'data-state="error"' in oversized
        assert "超过" in oversized
    assert "Live memory candidate" in runtime["candidates"]
    assert 'name="candidate_title"' in runtime["candidates"]
    assert 'name="candidate_content"' in runtime["candidates"]
    assert 'data-state="empty"' in runtime["dreams"]
    assert "0 个 active 房间" in runtime["darkroom"]


def test_memory_care_uses_only_the_unified_app_contract() -> None:
    source = _module_source()

    assert "app.registerPanel" in source
    assert "app.api" in source
    assert "app.ui" in source
    for forbidden in ("authFetch", "loadBuckets", "dailyChatMemoryApiBase"):
        assert forbidden not in source
    assert "typeof BASE" not in source


def test_reflection_restores_dated_events_and_source_evidence_without_a_vault_scan() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the reflection parity contract")

    harness = """
(async function () {
const vm = require('vm');
const source = __SOURCE__;

function fakeNode() {
  const children = new Map();
  const listeners = Object.create(null);
  return {
    innerHTML: '', textContent: '', value: '', checked: false, hidden: false,
    disabled: false, dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener(name, listener) {
      (listeners[name] = listeners[name] || []).push(listener);
    },
    contains() { return true; },
    focus() {}, reset() {}, setAttribute() {}, getAttribute() { return null; },
    querySelector(selector) {
      if (!children.has(selector)) children.set(selector, fakeNode());
      return children.get(selector);
    },
    querySelectorAll() { return []; },
  };
}

const document = {
  baseURI: 'https://brain.example.test/memory-dashboard',
  getElementById: () => null,
  createElement: fakeNode,
  head: { appendChild() {} },
};
const window = { OmbreDashboardFeatureFactories: [], confirm: () => true };
vm.runInNewContext(source, {
  window, document, URL, console, setTimeout, clearTimeout, Map, Set, Date, Promise,
  Object, Array, String, Number, Boolean, RegExp, Error, encodeURIComponent,
});

const now = new Date();
const today = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const calls = [];
function response(payload) { return { ok: true, status: 200, payload }; }
const api = {
  readJson: async (result) => result.payload,
  get: async (path) => {
    calls.push(path);
    if (path.startsWith('/api/buckets/light?')) {
      return response({
        buckets: [{
          id: 'reflection_daily_' + today,
          name: 'Daily impression',
          type: 'feel',
          tags: ['relationship_weather', 'daily_impression'],
          date: today,
        }],
        count: 1,
      });
    }
    if (path === '/api/bucket/reflection_daily_' + today) {
      return response({
        id: 'reflection_daily_' + today,
        content: 'Daily impression body',
        metadata: {
          source_bucket_ids: ['dated-memory'],
          source_raw_event_ids: [77],
          source_conversation_turn_ids: ['turn-12'],
        },
      });
    }
    if (path === '/api/bucket/dated-memory') {
      return response({
        id: 'dated-memory', name: 'A dated memory', type: 'dynamic',
        date: today, source: 'conversation', importance: 8,
        content: 'The dated memory body',
      });
    }
    if (path.startsWith('/api/search-raw?')) {
      return response({
        ok: true,
        count: 2,
        items: [
          { id: 77, source_event_id: 'raw-77', role: 'user', source: 'relay', text: 'Matched raw evidence', created_at: today + 'T14:00:00Z' },
          { id: 78, source_event_id: 'raw-78', role: 'assistant', source: 'relay', text: 'Another event that day', created_at: today + 'T14:01:00Z' },
        ],
      });
    }
    throw new Error('unexpected GET ' + path);
  },
};
const panels = [];
window.OmbreDashboardFeatureFactories[0]({
  api,
  ui: {
    escape: (value) => String(value == null ? '' : value),
    escapeAttr: (value) => String(value == null ? '' : value),
    setStatus(element, message, tone) { element.textContent = message; element.dataset.tone = tone; },
    confirm: async () => true,
  },
  store: { invalidate() {} },
  registerPanel(panel) { panels.push(panel); },
});
const panel = panels.find((item) => item.id === 'memory-reflection');
const root = fakeNode();
panel.mount(root);
await panel.activate();
for (let index = 0; index < 12; index += 1) {
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}
process.stdout.write(JSON.stringify({
  html: root.querySelector('[data-role="reflection-day"]').innerHTML,
  calls,
}));
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
""".replace("__SOURCE__", json.dumps(_module_source()))

    result = subprocess.run(
        [node, "-"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=harness,
    )
    runtime = json.loads(result.stdout)
    html = runtime["html"]

    assert "Daily impression body" in html
    assert "当天发生了什么" in html
    assert "带日期的记忆事件" in html
    assert "A dated memory" in html
    assert "The dated memory body" in html
    assert "当天原始事件" in html
    assert "Matched raw evidence" in html
    assert "Another event that day" in html
    assert "来源证据" in html
    assert "参考记忆桶" in html
    assert "原始事件" in html
    assert "对话轮次" in html
    assert "turn-12" in html
    assert any(path.startswith("/api/search-raw?since=") for path in runtime["calls"])
    assert not any(path.startswith("/api/buckets?") for path in runtime["calls"])
    assert sum(path == "/api/bucket/dated-memory" for path in runtime["calls"]) == 1


def test_reflection_evidence_contract_is_bounded_and_accessible() -> None:
    source = _module_source()

    assert "rawEventLimit: 40" in source
    assert "sourceDetailLimit: 24" in source
    assert "limit=\' + state.rawEventLimit" in source
    assert "role=\"alert\"" in source
    assert "role=\"status\"" in source
    assert "aria-live=\"polite\"" in source
    assert "rawState.status === 'loading'" in source
    assert "ui.loading('读取来源证据')" in source
    assert "当天发生了什么" in source
    assert "source_raw_event_ids" in source
    assert "source_conversation_turn_ids" in source


def test_memory_care_covers_all_current_care_and_daily_routes() -> None:
    source = _module_source()
    required_routes = {
        "/api/reminders",
        "/api/reflection/run",
        "/api/buckets/light",
        "/api/bucket/",
        "/api/daily-activity-summary/run",
        "/api/daily-chat-memory/run",
        "/api/daily-chat-memory/pending",
        "/api/daily-chat-memory/confirm",
        "/api/dreams",
        "/api/darkroom/status",
    }

    for route in required_routes:
        assert route in source
    assert ".get(" in source
    assert ".post(" in source
    assert ".patch(" in source
    assert "offset += page.length" in source
    assert "var maxPages = 50" in source
    assert "var maxItems = 100000" in source
    assert "type=feel&tags=relationship_weather,daily_impression&sort=created_desc" in source
    assert "dayPageSize: 20" in source
    assert "detailConcurrency: 4" in source
    assert "limitedResponseArray(data, 'reminders', 100" in source
    assert "limitedResponseArray(data, 'items', 100" in source
    assert "limitedResponseArray(data, 'records', 50" in source
    assert "expectedTotal" in source
    assert "seenBucketIds" in source
    assert "日印象历史返回不完整" in source
    assert "日印象历史超过安全读取上限" in source
    assert "generation !== state.requestId" in source
    assert "generation !== state.requestId || !hasCurrentDream(id)" in source
    assert "expectedTotal" in source
    assert "seenBucketIds" in source


def test_memory_care_keeps_write_and_reject_confirmations_explicit() -> None:
    source = _module_source()

    assert "WRITE" in source
    assert "REJECT" in source
    assert "candidate_ids" in source
    assert "importance must be between 1 and 10" in source
    assert "confidence must be between 0 and 1" in source
    assert "confirmAction" in source
    assert "state.inFlight.has(id)" in source
    assert "state.inFlight.set(id, token)" in source
    assert "lockCandidateCard(card, true)" in source
    assert "reserveReflectionOperation" in source
    assert "reserveChatMemoryRun" in source
    assert "reserveReminder" in source
    assert "state.activeRun || state.inFlight.has(id)" in source
    assert "state.activeRun || state.inFlight.size" in source


def test_memory_care_has_visible_loading_empty_error_and_retry_states() -> None:
    source = _module_source()

    for marker in ("state('loading'", "state('empty'", "state('error'", "data-action=\"retry\""):
        assert marker in source


def test_memory_care_styles_are_scoped_and_responsive() -> None:
    styles = STYLES.read_text(encoding="utf-8")

    assert ".ob-memory-care" in styles
    assert "@media" in styles
    assert not re.search(r"(?m)^\s*body\s*\{", styles)
    assert ":root" not in styles
    assert " .tab" not in styles
    assert "prefers-reduced-motion" in styles


def test_memory_care_javascript_parses() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for JavaScript syntax verification")

    subprocess.run([node, "--check", str(MODULE)], cwd=ROOT, check=True)


def test_memory_care_rejects_paginated_history_drift_and_duplicate_bucket_ids() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to execute the dashboard feature contract")

    harness = """
(async function () {
const vm = require('vm');
const source = __SOURCE__;

function fakeNode() {
  const children = new Map();
  const listeners = Object.create(null);
  return {
    innerHTML: '', textContent: '', value: '', checked: false, hidden: false,
    disabled: false, dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener(name, listener) {
      (listeners[name] = listeners[name] || []).push(listener);
    },
    contains() { return true; },
    focus() {}, reset() {},
    setAttribute() {}, getAttribute() { return null; },
    querySelector(selector) {
      if (!children.has(selector)) children.set(selector, fakeNode());
      return children.get(selector);
    },
    querySelectorAll() { return []; },
  };
}

async function runScenario(kind) {
  const document = {
    baseURI: 'https://brain.example.test/memory-dashboard',
    getElementById: () => null,
    createElement: fakeNode,
    head: { appendChild() {} },
  };
  const window = { OmbreDashboardFeatureFactories: [], confirm: () => true };
  vm.runInNewContext(source, {
    window, document, URL, console, setTimeout, clearTimeout, Map, Set, Date, Promise,
    Object, Array, String, Number, Boolean, RegExp, Error, encodeURIComponent,
  });

  const pages = {
    drift: {
      '/api/buckets/light?include_archive=false&limit=2000&offset=0&type=feel&tags=relationship_weather,daily_impression&sort=created_desc': {
        buckets: Array.from({ length: 2000 }, (_, index) => ({
          id: 'reflection_daily_2026-07-' + String((index % 28) + 1).padStart(2, '0') + '-a-' + index,
          name: 'page0-' + index,
          type: 'feel',
          tags: ['relationship_weather', 'daily_impression'],
          created: '2026-07-18T00:00:00Z',
        })),
        count: 2002,
      },
      '/api/buckets/light?include_archive=false&limit=2000&offset=2000&type=feel&tags=relationship_weather,daily_impression&sort=created_desc': {
        buckets: [
          {
            id: 'reflection_daily_2026-07-19-z-0',
            name: 'tail-0',
            type: 'feel',
            tags: ['relationship_weather', 'daily_impression'],
            created: '2026-07-19T00:00:00Z',
          },
          {
            id: 'reflection_daily_2026-07-19-z-1',
            name: 'tail-1',
            type: 'feel',
            tags: ['relationship_weather', 'daily_impression'],
            created: '2026-07-19T00:00:00Z',
          },
        ],
        count: 2001,
      },
    },
      duplicate: {
        '/api/buckets/light?include_archive=false&limit=2000&offset=0&type=feel&tags=relationship_weather,daily_impression&sort=created_desc': {
          buckets: Array.from({ length: 2000 }, (_, index) => ({
            id: 'reflection_daily_2026-07-' + String((index % 28) + 1).padStart(2, '0') + '-b-' + index,
            name: 'dup-page0-' + index,
            type: 'feel',
            tags: ['relationship_weather', 'daily_impression'],
            created: '2026-07-18T00:00:00Z',
          })),
          count: 4000,
        },
        '/api/buckets/light?include_archive=false&limit=2000&offset=2000&type=feel&tags=relationship_weather,daily_impression&sort=created_desc': {
          buckets: Array.from({ length: 2000 }, (_, index) => ({
            id: index === 0
              ? 'reflection_daily_2026-07-01-b-0'
              : 'reflection_daily_2026-07-' + String((index % 28) + 1).padStart(2, '0') + '-c-' + index,
            name: 'dup-page1-' + index,
            type: 'feel',
            tags: ['relationship_weather', 'daily_impression'],
            created: '2026-07-18T00:00:00Z',
          })),
          count: 4000,
        },
      },
  }[kind];

  const api = {
    readJson: async (result) => result.payload,
    get: async (path) => {
      if (pages[path]) return { ok: true, status: 200, payload: pages[path] };
      if (path.startsWith('/api/bucket/')) {
        return { ok: true, status: 200, payload: { content: 'detail' } };
      }
      throw new Error('unexpected GET ' + path);
    },
  };
  const panels = [];
  const app = {
    api,
    ui: {
      escape: (value) => String(value == null ? '' : value),
      escapeAttr: (value) => String(value == null ? '' : value),
      setStatus(element, message, tone) { element.textContent = message; element.dataset.tone = tone; },
      confirm: async () => true,
    },
    store: { invalidate() {} },
    registerPanel(panel) { panels.push(panel); },
  };
  window.OmbreDashboardFeatureFactories[0](app);
  const panel = panels.find((item) => item.id === 'memory-reflection');
  const root = fakeNode();
  panel.mount(root);
  await panel.activate();
  return root.querySelector('[data-role="reflection-calendar"]').innerHTML;
}

const result = {
  drift: await runScenario('drift'),
  duplicate: await runScenario('duplicate'),
};
process.stdout.write(JSON.stringify(result));
})().catch((error) => {
console.error(error && error.stack ? error.stack : error);
process.exitCode = 1;
});
""".replace("__SOURCE__", json.dumps(_module_source()))

    result = subprocess.run(
        [node, "-"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=harness,
    )
    runtime = json.loads(result.stdout)

    assert 'data-state="error"' in runtime["drift"]
    assert "分页读取期间发生变化" in runtime["drift"]
    assert 'data-state="error"' in runtime["duplicate"]
    assert "重复记录" in runtime["duplicate"]
