from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "frontend" / "dashboard-assets" / "shared-bucket-studio.js"
STYLES = ROOT / "frontend" / "dashboard-assets" / "shared-bucket-studio.css"
DASHBOARD = ROOT / "frontend" / "dashboard.html"


def test_shared_buckets_exposes_advanced_mode_without_a_second_visible_panel() -> None:
    source = ASSET.read_text(encoding="utf-8")
    dashboard = DASHBOARD.read_text(encoding="utf-8")
    mode_surface = dashboard + "\n" + source

    assert "id: 'shared-bucket-studio'" not in source
    assert 'data-bucket-mode="basic"' in mode_surface
    assert 'data-bucket-mode="advanced"' in mode_surface
    assert 'data-panel-id="shared-bucket-studio"' not in dashboard
    assert "shared-bucket-studio.js" in dashboard
    assert "shared-bucket-studio.css" in dashboard
    assert "button.setAttribute('tabindex', '0')" in source
    assert "selected ? '0' : '-1'" not in source


def test_shared_bucket_studio_covers_advanced_bucket_and_raw_workflows() -> None:
    source = ASSET.read_text(encoding="utf-8")

    for endpoint in (
        "/api/buckets/light?include_archive=",
        "/api/buckets?sort=created_desc&include_archive=",
        "/api/search?q=",
        "/api/bucket/",
        "/api/memories",
        "/api/buckets/bulk-update",
        "/api/buckets/delete",
        "/comments",
        "/api/moments?bucket_id=",
        "/api/ingest-raw",
        "/api/search-raw",
        "/api/edges",
        "/api/domain-taxonomy",
    ):
        assert endpoint in source

    for visible_contract in (
        "Light list",
        "Full list",
        "Raw Markdown",
        "Event date",
        "Year rings",
        "Integrated moments",
        "Raw event ingest",
        "Raw event search",
        "Memory edges",
        "Domain taxonomy",
    ):
        assert visible_contract in source


def test_shared_bucket_studio_bounds_data_and_uses_safe_write_boundaries() -> None:
    source = ASSET.read_text(encoding="utf-8")

    assert "MAX_LIST_RESULTS = 200" in source
    assert "MAX_RAW_RESULTS = 50" in source
    assert "MAX_EDGE_RESULTS = 200" in source
    assert "MAX_CONTENT_LENGTH = 100000" in source
    assert "MAX_COMMENT_LENGTH = 4000" in source
    assert "maxlength=\"100000\"" in source
    assert "maxlength=\"4000\"" in source
    assert "state.requests" in source
    assert "state.readController" in source
    assert "requestOptions.signal" in source
    assert "readController.abort()" in source
    assert "state.writes" in source
    assert "beginWrite(state" in source
    assert "finishWrite(state" in source
    assert "await confirmAction(" in source
    assert "confirm: 'DELETE'" in source
    assert "app.api" in source
    assert "authFetch" not in source
    assert not re.search(r"(?<![.\w])fetch\s*\(", source)
    assert "onclick=" not in source
    assert "innerHTML = error" not in source
    assert "Authorization" in source
    assert "tokenInput.value = ''" in source


def test_shared_bucket_studio_styles_are_scoped_and_responsive() -> None:
    css = STYLES.read_text(encoding="utf-8")

    assert ".shared-bucket-studio" in css
    assert '[data-panel="shared-bucket-studio"]' in css
    assert "@media (max-width: 820px)" in css
    assert ".shared-bucket-studio__grid" in css
    assert ".shared-bucket-studio__raw" in css


def test_shared_bucket_studio_runtime_routes_reads_and_writes_without_retrying_writes() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the Dashboard module runtime contract")

    script = textwrap.dedent(
        r"""
        const assert = require('assert');
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync(__ASSET__, 'utf8');
        const factories = [];
        const window = { OmbreDashboardFeatureFactories: factories, confirm: () => true };
        let advancedRoot;
        let basicRoot;
        const listView = { dataset: {} };
        const document = {
          head: null,
          getElementById(id) {
            if (id === 'bucket-advanced-mode') return advancedRoot;
            if (id === 'bucket-basic-mode') return basicRoot;
            if (id === 'list-view') return listView;
            return null;
          },
          querySelectorAll() { return []; },
        };
        window.document = document;
        vm.runInNewContext(source, {
          window, document, console, Promise, URLSearchParams, AbortController,
          setTimeout, clearTimeout,
        }, { filename: 'shared-bucket-studio.js' });
        assert.strictEqual(factories.length, 1);

        function response(payload, status = 200) {
          return { ok: status >= 200 && status < 300, status, json: async () => payload };
        }
        const calls = [];
        let globalUnauthorizedCalls = 0;
        const api = {
          get: async (path, options) => {
            calls.push({ method: 'GET', path, options });
            if (path.startsWith('/api/buckets/light')) return response({ buckets: [{ id: 'memory-1', name: '<unsafe>' }], count: 1 });
            if (path.startsWith('/api/buckets?sort=created_desc&include_archive=')) {
              return response({ buckets: [{ id: 'memory-1', name: '<unsafe>' }], count: 501 });
            }
            if (path === '/api/domain-taxonomy') return response({ domains: [{ key: 'general', label: '<General>' }] });
            if (path === '/api/edges') return response({ edges: [{ source: '<one>', target: 'two' }] });
            if (path.startsWith('/api/search?q=')) return response([{ id: 'memory-1', name: '<unsafe>' }]);
            if (path.startsWith('/api/bucket/')) return response({ id: 'memory-1', content: '<raw>', metadata: { name: '<title>', comments: [] } });
            if (path.startsWith('/api/moments?')) return response({ status: 'ok', moments: [{ moment_id: 'm1', text: '<moment>' }], edges: [] });
            if (path.startsWith('/api/search-raw?')) return response({ events: [{ id: 'raw-1', text: '<raw-event>' }] });
            throw new Error('unexpected GET ' + path);
          },
          post: async (path, body, options) => {
            calls.push({ method: 'POST', path, body, options });
            if (path === '/api/search-raw') return response({ events: [] });
            if (path === '/api/ingest-raw') return response({ status: 'ok', ingested: 1 });
            if (path === '/api/memories') {
              if (options.headers.Authorization === 'Bearer wrong-token') {
                if (!Object.prototype.hasOwnProperty.call(options, 'onUnauthorized')) {
                  globalUnauthorizedCalls += 1;
                } else if (typeof options.onUnauthorized === 'function') {
                  await options.onUnauthorized(response({}, 401));
                }
                return response({ error: 'Invalid memory-write token' }, 401);
              }
              return response({ status: 'created', id: 'memory-2' });
            }
            if (path === '/api/buckets/bulk-update') return response({ changed_count: 1 });
            if (path === '/api/buckets/delete') return response({ deleted: 1 });
            if (path.endsWith('/comments')) return response({ status: 'commented' });
            throw new Error('unexpected POST ' + path);
          },
          patch: async (path, body, options) => {
            calls.push({ method: 'PATCH', path, body, options });
            return response({ status: 'updated', id: 'memory-1' });
          },
          delete: async (path, body, options) => {
            calls.push({ method: 'DELETE', path, body, options });
            return response({ status: 'deleted' });
          },
          readJson: async (res) => {
            const payload = await res.json();
            if (!res.ok) throw new Error(payload.error || 'Request failed (' + res.status + ')');
            return payload;
          },
        };

        class Element {
          constructor() {
            this.innerHTML = ''; this.textContent = ''; this.value = ''; this.checked = false;
            this.disabled = false; this.hidden = false; this.dataset = {}; this.attrs = {}; this.style = {};
          }
          setAttribute(name, value) { this.attrs[name] = String(value); }
          removeAttribute(name) { delete this.attrs[name]; }
          querySelector() { return null; }
          querySelectorAll() { return []; }
        }
        class Root extends Element {
          constructor() { super(); this.elements = {}; this.listeners = {}; this.classList = { add() {} }; }
          querySelector(selector) {
            if (!this.elements[selector]) this.elements[selector] = new Element();
            return this.elements[selector];
          }
          querySelectorAll() { return []; }
          addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
          removeEventListener(type, listener) {
            this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== listener);
          }
          contains() { return true; }
          emit(type, target) {
            for (const listener of this.listeners[type] || []) listener({ target, preventDefault() {} });
          }
        }
        function action(name, data = {}) {
          return { dataset: Object.assign({ action: name }, data), closest: () => actionEl };
          var actionEl;
        }
        function button(name, data = {}) {
          const el = new Element();
          el.dataset = Object.assign({ action: name }, data);
          el.closest = () => el;
          return el;
        }
        function form(name, elements) {
          return {
            dataset: { submit: name }, elements,
            matches: (selector) => selector === 'form[data-submit]',
            querySelector: () => null,
          };
        }
        function field(value = '', checked = false) { return { value, checked, disabled: false }; }
        function wait() { return new Promise((resolve) => setTimeout(resolve, 30)); }

        (async () => {
          const routerCalls = [];
          const confirmations = [];
          const app = {
            api,
            ui: {
              escape: (value) => String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
              setStatus: (el, message, tone) => { el.textContent = message; el.dataset.tone = tone || ''; },
              confirm: async (message) => { confirmations.push(message); return true; },
            },
            router: { go: (...args) => routerCalls.push(args) },
            commands: { refreshBuckets: () => {} },
          };
          advancedRoot = new Root();
          basicRoot = new Element();
          factories[0](app);
          const root = advancedRoot;
          factories[0](app);
          assert.strictEqual(root.listeners.click.length, 1, 're-init must replace the old click listener');
          assert.strictEqual(root.listeners.submit.length, 1, 're-init must replace the old submit listener');
          app.commands.setBucketMode('advanced', { navigate: false, context: { state: { params: {} } } });
          await wait();
          assert(calls.some((call) => call.method === 'GET' && call.path.startsWith('/api/buckets/light?')));
          assert(calls.some((call) => call.path === '/api/domain-taxonomy'));
          assert(calls.some((call) => call.path === '/api/edges'));
          const initialRead = calls.find((call) => call.method === 'GET');
          assert(initialRead.options.signal instanceof AbortSignal, 'advanced reads must be abortable');
          assert(root.querySelector('[data-role="bucket-list"]').innerHTML.includes('&lt;unsafe&gt;'));

          app.commands.setBucketMode('basic', { navigate: false });
          assert.strictEqual(initialRead.options.signal.aborted, true, 'leaving advanced mode aborts reads');
          app.commands.setBucketMode('advanced', { navigate: false, context: { state: { params: {} } } });
          await wait();

          root.emit('click', button('load-full'));
          await wait();
          assert(calls.some((call) => call.path === '/api/buckets?sort=created_desc&include_archive=0&limit=200'));
          assert.strictEqual(root.querySelector('[data-role="list-status"]').textContent, 'Full list: showing 1 of 501.');

          root.querySelector('[data-role="include-archive"]').checked = true;
          root.emit('click', button('load-full'));
          await wait();
          assert(calls.some((call) => call.path === '/api/buckets?sort=created_desc&include_archive=1&limit=200'));

          root.emit('submit', form('bucket-search', { q: field('hello world') }));
          await wait();
          assert(calls.some((call) => call.path === '/api/search?q=hello%20world'));

          root.emit('submit', form('create-memory', {
            title: field('Title'), content: field('Body'), event_date: field('2026-07-18'),
            domain: field('general'), write_token: field('secret-token'),
          }));
          await wait();
          const create = calls.find((call) => call.path === '/api/memories');
          assert.strictEqual(create.options.headers.Authorization, 'Bearer secret-token');
          assert.strictEqual(create.body.date, '2026-07-18');

          root.emit('submit', form('create-memory', {
            title: field('Title'), content: field('Body'), event_date: field(''),
            domain: field('general'), write_token: field('wrong-token'),
          }));
          await wait();
          assert.strictEqual(globalUnauthorizedCalls, 0, 'alternate credentials must not log out the Dashboard session');
          assert.strictEqual(
            root.querySelector('[data-role="create-status"]').textContent,
            'Create failed: Invalid memory-write token',
          );

          root.emit('submit', form('edit-bucket', {
            bucket_id: field('memory-1'), title: field('Edited'), event_date: field('2026-07-17'), content: field('Edited body'),
          }));
          await wait();
          const edit = calls.find((call) => call.method === 'PATCH');
          assert.strictEqual(
            JSON.stringify(edit.body),
            JSON.stringify({ name: 'Edited', date: '2026-07-17', content: 'Edited body' }),
          );

          const selected = button('toggle-select', { bucketId: 'memory-1' });
          selected.checked = true;
          root.emit('click', selected);
          root.emit('submit', form('bulk-update', {
            domain: field('general'), tags_add: field('one,two'), tags_remove: field(''), status: field('active'),
          }));
          await wait();
          assert(confirmations.length >= 1, 'bulk mutation must confirm');

          root.emit('submit', form('add-comment', {
            bucket_id: field('memory-1'), content: field('A year ring'), kind: field('feel'),
          }));
          await wait();
          assert(calls.some((call) => call.method === 'POST' && call.path === '/api/bucket/memory-1/comments'));

          root.emit('click', button('delete-comment', { bucketId: 'memory-1', commentId: 'comment-1' }));
          await wait();
          assert(calls.some((call) => call.method === 'DELETE' && call.path === '/api/bucket/memory-1/comments/comment-1'));

          const selectedForDelete = button('toggle-select', { bucketId: 'memory-1' });
          selectedForDelete.checked = true;
          root.emit('click', selectedForDelete);
          root.emit('click', button('bulk-delete'));
          await wait();
          const bulkDelete = calls.find((call) => call.path === '/api/buckets/delete');
          assert.strictEqual(JSON.stringify(bulkDelete.body), JSON.stringify({ bucket_ids: ['memory-1'], confirm: 'DELETE' }));

          root.emit('submit', form('ingest-raw', {
            source: field('dashboard'), role: field('user'), text: field('raw body'),
            session_id: field('session-1'), conversation_id: field('conversation-1'),
          }));
          root.emit('click', button('raw-search-get'));
          root.emit('click', button('raw-search-post'));
          await wait();
          assert(calls.some((call) => call.path === '/api/ingest-raw'));
          assert(calls.some((call) => call.method === 'GET' && call.path.startsWith('/api/search-raw?')));
          assert(calls.some((call) => call.method === 'POST' && call.path === '/api/search-raw'));

          root.emit('click', button('open-basic-buckets'));
          assert.strictEqual(JSON.stringify(routerCalls[0]), JSON.stringify(['shared', 'shared-buckets', {}]));

          for (const call of calls.filter((item) => item.method !== 'GET')) {
            assert.strictEqual(call.options && call.options.retries, 0, 'writes must explicitly disable retries');
          }
        })().catch((error) => { console.error(error); process.exitCode = 1; });
        """
    ).replace("__ASSET__", json.dumps(str(ASSET)))

    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_failed_bucket_detail_load_clears_stale_mutation_targets() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the Dashboard module runtime contract")

    script = textwrap.dedent(
        r"""
        const assert = require('assert');
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync(__ASSET__, 'utf8');
        const factories = [];
        const window = { OmbreDashboardFeatureFactories: factories, confirm: () => true };
        let advancedRoot;
        let basicRoot;
        const listView = { dataset: {} };
        const document = {
          head: null,
          getElementById(id) {
            if (id === 'bucket-advanced-mode') return advancedRoot;
            if (id === 'bucket-basic-mode') return basicRoot;
            if (id === 'list-view') return listView;
            return null;
          },
          querySelectorAll() { return []; },
        };
        window.document = document;
        vm.runInNewContext(source, {
          window, document, console, Promise, URLSearchParams, AbortController,
          setTimeout, clearTimeout,
        }, { filename: 'shared-bucket-studio.js' });

        function response(payload) {
          return { ok: true, status: 200, json: async () => payload };
        }
        const calls = [];
        const api = {
          get: async (path, options) => {
            calls.push({ method: 'GET', path, options });
            if (path.startsWith('/api/buckets/light')) return response({ buckets: [], count: 0 });
            if (path === '/api/domain-taxonomy') return response({ domains: [] });
            if (path === '/api/edges') return response({ edges: [] });
            if (path === '/api/bucket/memory-1') {
              return response({ id: 'memory-1', content: 'old body', metadata: { name: 'Old title', comments: [] } });
            }
            if (path === '/api/bucket/missing') throw new Error('not found');
            if (path.startsWith('/api/moments?')) return response({ moments: [], edges: [] });
            throw new Error('unexpected GET ' + path);
          },
          post: async (path, body, options) => {
            calls.push({ method: 'POST', path, body, options });
            return response({ status: 'ok' });
          },
          patch: async (path, body, options) => {
            calls.push({ method: 'PATCH', path, body, options });
            return response({ status: 'ok' });
          },
          readJson: async (res) => res.json(),
        };

        class Element {
          constructor() {
            this.innerHTML = ''; this.textContent = ''; this.value = ''; this.checked = false;
            this.disabled = false; this.hidden = false; this.dataset = {}; this.attrs = {}; this.style = {};
          }
          setAttribute(name, value) { this.attrs[name] = String(value); }
          removeAttribute(name) { delete this.attrs[name]; }
          querySelectorAll() { return []; }
        }
        class Form extends Element {
          constructor(name, fields) {
            super();
            this.dataset.submit = name;
            this.elements = Object.fromEntries(fields.map((field) => [field, new Element()]));
          }
          matches(selector) { return selector === 'form[data-submit]'; }
          querySelectorAll() { return Object.values(this.elements); }
        }
        class Root extends Element {
          constructor() {
            super();
            this.elements = {
              'form[data-submit="edit-bucket"]': new Form('edit-bucket', ['bucket_id', 'title', 'event_date', 'content']),
              'form[data-submit="add-comment"]': new Form('add-comment', ['bucket_id', 'kind', 'content']),
            };
            this.listeners = {};
            this.classList = { add() {} };
          }
          querySelector(selector) {
            if (!this.elements[selector]) this.elements[selector] = new Element();
            return this.elements[selector];
          }
          querySelectorAll() { return []; }
          addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
          contains() { return true; }
          emit(type, target) {
            for (const listener of this.listeners[type] || []) listener({ target, preventDefault() {} });
          }
        }
        function button(action, bucketId) {
          const value = new Element();
          value.dataset = { action, bucketId };
          value.closest = () => value;
          return value;
        }
        function wait() { return new Promise((resolve) => setTimeout(resolve, 20)); }

        (async () => {
          const app = {
            api,
            ui: {
              escape: (value) => String(value == null ? '' : value),
              setStatus: (element, message, tone) => { element.textContent = message; element.dataset.tone = tone; },
              confirm: async () => true,
            },
            router: { go() {} },
            commands: {},
          };
          advancedRoot = new Root();
          basicRoot = new Element();
          factories[0](app);
          const root = advancedRoot;
          const editForm = root.querySelector('form[data-submit="edit-bucket"]');
          const commentForm = root.querySelector('form[data-submit="add-comment"]');
          await app.commands.setBucketMode('advanced', { navigate: false, context: { state: { params: {} } } });

          root.emit('click', button('open-bucket', 'memory-1'));
          await wait();
          assert.strictEqual(editForm.elements.bucket_id.value, 'memory-1');
          assert.strictEqual(commentForm.elements.bucket_id.value, 'memory-1');
          assert.strictEqual(editForm.elements.title.disabled, false);

          root.emit('click', button('open-bucket', 'missing'));
          assert.strictEqual(editForm.elements.bucket_id.value, '', 'new loads must immediately clear the old edit target');
          assert.strictEqual(commentForm.elements.bucket_id.value, '', 'new loads must immediately clear the old comment target');
          assert.strictEqual(editForm.elements.title.disabled, true, 'detail editing stays disabled until the new load succeeds');
          assert.strictEqual(commentForm.elements.content.disabled, true, 'commenting stays disabled until the new load succeeds');

          root.emit('submit', editForm);
          root.emit('submit', commentForm);
          await wait();

          assert.strictEqual(calls.filter((call) => call.method === 'PATCH').length, 0);
          assert.strictEqual(calls.filter((call) => call.method === 'POST' && call.path.includes('/comments')).length, 0);
          assert.strictEqual(editForm.elements.bucket_id.value, '');
          assert.strictEqual(commentForm.elements.bucket_id.value, '');
          assert.strictEqual(editForm.elements.title.disabled, true);
          assert.strictEqual(commentForm.elements.content.disabled, true);
        })().catch((error) => { console.error(error); process.exitCode = 1; });
        """
    ).replace("__ASSET__", json.dumps(str(ASSET)))

    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
