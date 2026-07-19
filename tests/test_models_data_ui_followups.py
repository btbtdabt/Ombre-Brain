from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "frontend" / "dashboard-assets" / "models-data.js"


def _run_node(script: str) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the dashboard runtime contract test")
    return subprocess.run(
        [node, "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_vault_export_uses_same_origin_navigation_without_buffering_a_blob() -> None:
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(ASSET))}, 'utf8');
        const registered = [];
        const apiCalls = [];
        let appended = null;
        const link = {{
          href: '', download: null, rel: '', clicked: false, removed: false,
          click() {{ this.clicked = true; }},
          remove() {{ this.removed = true; }},
        }};
        const document = {{
          body: {{ appendChild(node) {{ appended = node; }} }},
          createElement(tag) {{
            if (tag !== 'a') throw new Error('unexpected element: ' + tag);
            return link;
          }},
        }};
        const window = {{
          OmbreDashboardFeatureFactories: [], document,
          confirm: () => true, prompt: () => null,
          URL: {{
            createObjectURL() {{ throw new Error('archive must not be buffered'); }},
            revokeObjectURL() {{ throw new Error('object URL must not be used'); }},
          }},
        }};
        vm.runInNewContext(source, {{
          window, document, console, Promise, setTimeout, clearTimeout,
          FormData: class FormData {{}},
        }}, {{ filename: 'models-data.js' }});
        const app = {{
          registerPanel(panel) {{ registered.push(panel); }},
          commands: {{}}, ui: {{}},
          apiUrl(path) {{ return 'https://brain.example/ombre' + path; }},
          api: {{
            post(path) {{ apiCalls.push(['POST', path]); return Promise.resolve({{ kind: 'prepare' }}); }},
            get(path) {{ apiCalls.push(['GET', path]); return Promise.resolve({{ kind: 'status' }}); }},
            readJson(value) {{
              if (value.kind === 'prepare') return Promise.resolve({{ ok: true, ticket: 'one-time-ticket' }});
              if (value.kind === 'status') return Promise.resolve({{ ok: true, active: false }});
              return Promise.resolve(value);
            }},
          }},
          store: {{ invalidate() {{}}, resource() {{ return Promise.resolve({{}}); }} }},
        }};
        window.OmbreDashboardFeatureFactories[0](app);

        const status = {{ textContent: '', dataset: {{}}, hidden: true }};
        const listeners = {{}};
        const root = {{
          classList: {{ add() {{}} }}, innerHTML: '', setAttribute() {{}},
          querySelector(selector) {{
            if (selector === '[data-role="status"]') return status;
            return null;
          }},
          querySelectorAll() {{ return []; }},
          addEventListener(name, handler) {{ listeners[name] = handler; }},
        }};
        registered.find((panel) => panel.id === 'models-full-vault').mount(root);
        const button = {{ dataset: {{ writeAction: 'download-vault' }} }};
        listeners.click({{ target: {{ closest() {{ return button; }} }} }});
        listeners.click({{ target: {{ closest() {{ return button; }} }} }});

        setTimeout(() => {{
          const expectedCalls = [['POST', '/api/backup/export/prepare'], ['GET', '/api/backup/export/status']];
          if (JSON.stringify(apiCalls) !== JSON.stringify(expectedCalls)) {{
            throw new Error('ticket preparation/status flow missing or duplicated: ' + JSON.stringify(apiCalls));
          }}
          if (appended !== link || !link.clicked || !link.removed) throw new Error('download navigation was not dispatched');
          if (link.href !== 'https://brain.example/ombre/api/backup/export?ticket=one-time-ticket') throw new Error('wrong export URL: ' + link.href);
          if (!/server finished/i.test(status.textContent)) throw new Error('completion status missing: ' + status.textContent);
          process.stdout.write('ok');
        }}, 20);
        """
    )
    completed = _run_node(script)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "ok"


def test_vault_export_surfaces_prepare_auth_busy_and_archive_errors_without_navigation() -> None:
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(ASSET))}, 'utf8');
        const registered = [];
        const failures = ['401 login required', '409 backup operation active', 'archive creation failed'];
        let linksCreated = 0;
        const document = {{
          body: {{ appendChild() {{}} }},
          createElement() {{ linksCreated += 1; return {{ click() {{}}, remove() {{}} }}; }},
        }};
        const window = {{ OmbreDashboardFeatureFactories: [], document, confirm: () => true, prompt: () => null }};
        vm.runInNewContext(source, {{
          window, document, console, Promise, setTimeout, clearTimeout,
          FormData: class FormData {{}}, encodeURIComponent,
        }}, {{ filename: 'models-data.js' }});
        const app = {{
          registerPanel(panel) {{ registered.push(panel); }},
          commands: {{}}, ui: {{}}, apiUrl(path) {{ return path; }},
          api: {{
            post() {{ return Promise.reject(new Error(failures.shift())); }},
            readJson(value) {{ return Promise.resolve(value); }},
          }},
          store: {{ invalidate() {{}}, resource() {{ return Promise.resolve({{}}); }} }},
        }};
        window.OmbreDashboardFeatureFactories[0](app);
        const status = {{ textContent: '', dataset: {{}}, hidden: true }};
        const listeners = {{}};
        const root = {{
          classList: {{ add() {{}} }}, innerHTML: '', setAttribute() {{}},
          querySelector(selector) {{ return selector === '[data-role="status"]' ? status : null; }},
          querySelectorAll() {{ return []; }},
          addEventListener(name, handler) {{ listeners[name] = handler; }},
        }};
        registered.find((panel) => panel.id === 'models-full-vault').mount(root);
        const button = {{ dataset: {{ writeAction: 'download-vault' }} }};
        function click() {{ listeners.click({{ target: {{ closest() {{ return button; }} }} }}); }}

        (async () => {{
          for (const expected of ['401', '409', 'archive creation failed']) {{
            click();
            for (let attempt = 0; attempt < 30 && !status.textContent.includes(expected); attempt += 1) {{
              await new Promise((resolve) => setTimeout(resolve, 1));
            }}
            if (!status.textContent.includes(expected) || status.dataset.tone !== 'error') {{
              throw new Error('prepare error was not surfaced: ' + status.textContent);
            }}
          }}
          if (linksCreated !== 0) throw new Error('failed prepare navigated to an export');
          process.stdout.write('ok');
        }})().catch((error) => {{
          console.error(error && error.stack || error);
          process.exitCode = 1;
        }});
        """
    )
    completed = _run_node(script)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "ok"


def test_vault_restore_reports_canonical_counts_and_index_refresh_warnings() -> None:
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(ASSET))}, 'utf8');
        const registered = [];
        const restoreResult = {{
          created: 2,
          overwritten: 1,
          restored_ids: ['memory-1', 'memory-2', 'memory-3'],
          restored_count: 999,
          restored: ['legacy-field'],
          derived_indexes: {{ refreshed: 2, errors: [{{ bucket_id: 'memory-3', error: 'index failed' }}] }},
        }};
        class FormData {{ append() {{}} }}
        const window = {{
          OmbreDashboardFeatureFactories: [], confirm: () => true, prompt: () => null,
        }};
        vm.runInNewContext(source, {{
          window, console, Promise, setTimeout, clearTimeout, FormData,
        }}, {{ filename: 'models-data.js' }});
        let uploadOptions = null;
        const app = {{
          registerPanel(panel) {{ registered.push(panel); }},
          commands: {{}},
          ui: {{ confirm() {{ return true; }} }},
          api: {{
            upload(_path, _form, options) {{ uploadOptions = options; return Promise.resolve({{ ok: true }}); }},
            readJson() {{ return Promise.resolve(restoreResult); }},
          }},
          store: {{ invalidate() {{}}, resource() {{ return Promise.resolve({{}}); }} }},
        }};
        window.OmbreDashboardFeatureFactories[0](app);

        const status = {{ textContent: '', dataset: {{}}, hidden: true }};
        const input = {{
          files: [{{ name: 'vault.zip', size: 1024, type: 'application/zip' }}],
          value: 'vault.zip',
        }};
        const listeners = {{}};
        const root = {{
          classList: {{ add() {{}} }}, innerHTML: '', setAttribute() {{}},
          querySelector(selector) {{
            if (selector === '[data-role="status"]') return status;
            if (selector === '[data-role="vault-file"]') return input;
            return null;
          }},
          querySelectorAll() {{ return []; }},
          addEventListener(name, handler) {{ listeners[name] = handler; }},
        }};
        registered.find((panel) => panel.id === 'models-full-vault').mount(root);
        const button = {{ dataset: {{ writeAction: 'restore-skip' }} }};
        listeners.click({{ target: {{ closest() {{ return button; }} }} }});

        setTimeout(() => {{
          const message = status.textContent;
          if (!message.includes('2 created')) throw new Error('created count missing: ' + message);
          if (!message.includes('1 overwritten')) throw new Error('overwrite count missing: ' + message);
          if (!message.includes('3 restored ID(s)')) throw new Error('restored IDs missing: ' + message);
          if (!message.includes('Derived-index refresh reported 1 error(s)')) throw new Error('index warning missing: ' + message);
          if (message.includes('999')) throw new Error('legacy restore count was used');
          if (status.dataset.tone !== 'warning') throw new Error('index errors must produce warning tone');
          if (input.value !== '') throw new Error('successful restore did not clear file input');
          if (!uploadOptions || uploadOptions.timeoutMs !== 0) throw new Error('restore still has an aborting client timeout');
          process.stdout.write('ok');
        }}, 30);
        """
    )
    completed = _run_node(script)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "ok"


def test_successful_config_post_clears_write_only_secrets_before_refresh() -> None:
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(ASSET))}, 'utf8');
        const registered = [];
        let postCount = 0;
        let refreshCount = 0;
        const window = {{ OmbreDashboardFeatureFactories: [], confirm: () => true, prompt: () => null }};
        vm.runInNewContext(source, {{
          window, console, Promise, setTimeout, clearTimeout,
          FormData: class FormData {{}},
        }}, {{ filename: 'models-data.js' }});
        const app = {{
          registerPanel(panel) {{ registered.push(panel); }},
          commands: {{}}, ui: {{}},
          api: {{
            post() {{ postCount += 1; return Promise.resolve({{ ok: true }}); }},
            readJson() {{ return Promise.resolve({{}}); }},
          }},
          store: {{
            invalidate() {{}},
            resource() {{ refreshCount += 1; return new Promise(() => {{}}); }},
          }},
        }};
        window.OmbreDashboardFeatureFactories[0](app);

        function saveTarget(button) {{
          return {{ closest(selector) {{
            return selector.includes('data-write-action') ? button : null;
          }} }};
        }}

        const dehydrationSecret = {{ value: 'dehydration-secret', type: 'password' }};
        const sentinelSecret = {{ value: 'sentinel-secret', type: 'password' }};
        const configControls = Object.create(null);
        function configControl(path) {{
          if (path === 'dehydration.api_key') return dehydrationSecret;
          if (path === 'gateway.domain_sentinel_api_key') return sentinelSecret;
          if (!configControls[path]) {{
            const isNumber = /(tokens|seconds|temperature)$/.test(path);
            configControls[path] = {{
              checked: true,
              value: path.endsWith('api_format') ? 'openai_compat' : (isNumber ? '1' : 'configured'),
            }};
          }}
          return configControls[path];
        }}
        const dehydrationStatus = {{ textContent: '', dataset: {{}}, hidden: true }};
        const dehydrationListeners = {{}};
        const dehydrationRoot = {{
          classList: {{ add() {{}} }}, innerHTML: '', setAttribute() {{}},
          querySelector(selector) {{
            if (selector === '[data-role="status"]') return dehydrationStatus;
            const match = selector.match(/data-config-field="([^"]+)/);
            return match ? configControl(match[1]) : null;
          }},
          querySelectorAll(selector) {{
            if (selector === '[data-write-only-secret="true"]') return [dehydrationSecret, sentinelSecret];
            return [];
          }},
          addEventListener(name, handler) {{ dehydrationListeners[name] = handler; }},
        }};
        registered.find((panel) => panel.id === 'models-dehydration').mount(dehydrationRoot);

        const upstreamSecret = {{ value: 'upstream-secret' }};
        const upstreamValues = {{
          name: 'provider', protocol: 'openai', base_url: 'https://models.example/v1',
          api_key_envs: 'OMBRE_GATEWAY_PROVIDER_API_KEY', api_key_values: upstreamSecret.value,
          default_model: 'model', prompt_cache: '', prompt_cache_retention: '',
          anthropic_version: '', anthropic_beta: '', gemini_base_url: '', gemini_auth: '', models: 'model',
        }};
        const upstreamRow = {{ querySelector(selector) {{
          const match = selector.match(/data-upstream-field="([^"]+)/);
          if (!match) return null;
          return match[1] === 'api_key_values' ? upstreamSecret : {{ value: upstreamValues[match[1]] || '' }};
        }} }};
        const upstreamStatus = {{ textContent: '', dataset: {{}}, hidden: true }};
        const upstreamListeners = {{ click: [] }};
        const upstreamRoot = {{
          classList: {{ add() {{}} }}, innerHTML: '', setAttribute() {{}},
          querySelector(selector) {{ return selector === '[data-role="status"]' ? upstreamStatus : null; }},
          querySelectorAll(selector) {{
            if (selector === '[data-upstream-row]') return [upstreamRow];
            if (selector === '[data-write-only-secret="true"]') return [upstreamSecret];
            return [];
          }},
          addEventListener(name, handler) {{ (upstreamListeners[name] ||= []).push(handler); }},
        }};
        registered.find((panel) => panel.id === 'models-upstream').mount(upstreamRoot);

        const persistButton = {{ dataset: {{ writeAction: 'persist' }} }};
        dehydrationListeners.click({{ target: saveTarget(persistButton) }});
        for (const handler of upstreamListeners.click) {{
          handler({{ target: saveTarget(persistButton) }});
        }}

        setTimeout(() => {{
          if (postCount !== 2 || refreshCount !== 2) throw new Error('writes did not reach the pending refresh boundary');
          if (dehydrationSecret.value || sentinelSecret.value || upstreamSecret.value) {{
            throw new Error('write-only secrets remained populated while refresh was pending');
          }}
          process.stdout.write('ok');
        }}, 30);
        """
    )
    completed = _run_node(script)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "ok"


def test_dehydration_api_formats_round_trip_without_being_blanked() -> None:
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(ASSET))}, 'utf8');
        const registered = [];
        const posts = [];
        let currentConfig = null;
        const window = {{ OmbreDashboardFeatureFactories: [], confirm: () => true, prompt: () => null }};
        vm.runInNewContext(source, {{
          window, console, Promise, setTimeout, clearTimeout,
          FormData: class FormData {{}},
        }}, {{ filename: 'models-data.js' }});
        const app = {{
          registerPanel(panel) {{ registered.push(panel); }},
          commands: {{}}, ui: {{}},
          api: {{
            post(_path, body) {{ posts.push(body); return Promise.resolve({{ ok: true }}); }},
            readJson() {{ return Promise.resolve({{}}); }},
          }},
          store: {{
            invalidate() {{}},
            resource() {{ return Promise.resolve(currentConfig); }},
          }},
        }};
        window.OmbreDashboardFeatureFactories[0](app);
        const panel = registered.find((item) => item.id === 'models-dehydration');

        function makeRoot() {{
          let allowedFormats = [];
          let markup = '';
          const listeners = {{}};
          const status = {{ textContent: '', dataset: {{}}, hidden: true }};
          const controls = Object.create(null);
          const formatControl = {{ _value: '' }};
          Object.defineProperty(formatControl, 'value', {{
            get() {{ return this._value; }},
            set(value) {{
              const normalized = String(value == null ? '' : value);
              this._value = allowedFormats.includes(normalized) ? normalized : '';
            }},
          }});
          function control(path) {{
            if (path === 'dehydration.api_format') return formatControl;
            if (!controls[path]) controls[path] = {{ value: '', checked: false }};
            return controls[path];
          }}
          const root = {{
            classList: {{ add() {{}} }}, setAttribute() {{}},
            querySelector(selector) {{
              if (selector === '[data-role="status"]') return status;
              const match = selector.match(/data-config-field="([^"]+)/);
              return match ? control(match[1]) : null;
            }},
            querySelectorAll(selector) {{
              if (selector === '[data-write-only-secret="true"]') {{
                return [control('dehydration.api_key'), control('gateway.domain_sentinel_api_key')];
              }}
              return [];
            }},
            addEventListener(name, handler) {{ listeners[name] = handler; }},
            save() {{
              const button = {{ dataset: {{ writeAction: 'persist' }} }};
              listeners.click({{ target: {{ closest(selector) {{
                return selector.includes('data-write-action') ? button : null;
              }} }} }});
            }},
          }};
          Object.defineProperty(root, 'innerHTML', {{
            get() {{ return markup; }},
            set(value) {{
              markup = String(value);
              const select = markup.match(/<select[^>]*data-config-field="dehydration[.]api_format"[^>]*>([\\s\\S]*?)<\\/select>/);
              allowedFormats = select
                ? Array.from(select[1].matchAll(/<option value="([^"]*)"/g), (match) => match[1])
                : [];
            }},
          }});
          return {{ root, formatControl }};
        }}

        (async () => {{
          for (const apiFormat of ['openai_compat', 'anthropic', 'gemini']) {{
            const mounted = makeRoot();
            panel.mount(mounted.root);
            currentConfig = {{
              dehydration: {{
                model: 'model', base_url: 'https://models.example/v1', api_key_masked: '***',
                max_tokens: 1024, temperature: 0.1, timeout_seconds: 30, api_format: apiFormat,
              }},
              gateway: {{
                domain_sentinel_enabled: true, domain_sentinel_model: '',
                domain_sentinel_base_url: '', domain_sentinel_api_key_masked: '',
              }},
            }};
            await panel.activate({{ scopeId: 'format-' + apiFormat }});
            if (mounted.formatControl.value !== apiFormat) {{
              throw new Error(apiFormat + ' loaded as ' + JSON.stringify(mounted.formatControl.value));
            }}
            const before = posts.length;
            mounted.root.save();
            for (let attempt = 0; attempt < 20 && posts.length === before; attempt += 1) {{
              await new Promise((resolve) => setTimeout(resolve, 1));
            }}
            const saved = posts[before];
            if (!saved || saved.dehydration.api_format !== apiFormat) {{
              throw new Error(apiFormat + ' saved as ' + JSON.stringify(saved && saved.dehydration.api_format));
            }}
          }}
          process.stdout.write('ok');
        }})().catch((error) => {{
          console.error(error && error.stack || error);
          process.exitCode = 1;
        }});
        """
    )
    completed = _run_node(script)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "ok"


def test_legacy_runtime_modes_are_canonicalized_without_silent_save() -> None:
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(ASSET))}, 'utf8');
        const registered = [];
        const posts = [];
        let currentConfig = {{}};
        const window = {{ OmbreDashboardFeatureFactories: [], confirm: () => true, prompt: () => null }};
        vm.runInNewContext(source, {{
          window, console, Promise, setTimeout, clearTimeout,
          FormData: class FormData {{}},
        }}, {{ filename: 'models-data.js' }});
        const app = {{
          registerPanel(panel) {{ registered.push(panel); }},
          commands: {{}}, ui: {{}},
          api: {{
            post(_path, body) {{ posts.push(body); return Promise.resolve({{ ok: true }}); }},
            readJson(value) {{ return Promise.resolve(value || {{}}); }},
          }},
          store: {{
            invalidate() {{}},
            resource() {{ return Promise.resolve(currentConfig); }},
          }},
        }};
        window.OmbreDashboardFeatureFactories[0](app);

        function makeRoot() {{
          const listeners = {{}};
          const status = {{ textContent: '', dataset: {{}}, hidden: true }};
          const controls = Object.create(null);
          const allowed = Object.create(null);
          let markup = '';
          function control(path) {{
            if (controls[path]) return controls[path];
            const item = {{ checked: false, _value: '', placeholder: '' }};
            Object.defineProperty(item, 'value', {{
              get() {{ return this._value; }},
              set(value) {{
                const normalized = String(value == null ? '' : value);
                this._value = allowed[path] && !allowed[path].includes(normalized)
                  ? '' : normalized;
              }},
            }});
            controls[path] = item;
            return item;
          }}
          const root = {{
            classList: {{ add() {{}} }}, setAttribute() {{}},
            querySelector(selector) {{
              if (selector === '[data-role="status"]') return status;
              const match = selector.match(/data-config-field="([^"]+)/);
              return match ? control(match[1]) : null;
            }},
            querySelectorAll() {{ return []; }},
            addEventListener(name, handler) {{ listeners[name] = handler; }},
            save() {{
              const button = {{ dataset: {{ writeAction: 'persist' }} }};
              listeners.click({{ target: {{ closest(selector) {{
                return selector.includes('data-write-action') ? button : null;
              }} }} }});
            }},
          }};
          Object.defineProperty(root, 'innerHTML', {{
            get() {{ return markup; }},
            set(value) {{
              markup = String(value);
              for (const match of markup.matchAll(/<select[^>]*data-config-field="([^"]+)"[^>]*>([\\s\\S]*?)<\\/select>/g)) {{
                allowed[match[1]] = Array.from(
                  match[2].matchAll(/<option value="([^"]*)"/g),
                  (option) => option[1]
                );
              }}
            }},
          }});
          return {{ root, control }};
        }}

        async function waitForPost(before) {{
          for (let attempt = 0; attempt < 30 && posts.length === before; attempt += 1) {{
            await new Promise((resolve) => setTimeout(resolve, 1));
          }}
          if (posts.length === before) throw new Error('save did not post');
          return posts[before];
        }}

        (async () => {{
          const relationship = registered.find((item) => item.id === 'models-relationship-memory');
          const relationshipRoot = makeRoot();
          relationship.mount(relationshipRoot.root);
          const thinkingAliases = [
            ['on', 'enabled'], ['true', 'enabled'], ['thinking', 'enabled'],
            ['off', 'disabled'], ['false', 'disabled'], ['non_thinking', 'disabled'],
          ];
          for (const [alias, expected] of thinkingAliases) {{
            currentConfig = {{ reflection: {{ thinking_mode: alias }}, self_anchor: {{}} }};
            const beforeLoad = posts.length;
            await relationship.activate({{ scopeId: 'thinking-' + alias }});
            if (posts.length !== beforeLoad) throw new Error('activation silently persisted ' + alias);
            if (relationshipRoot.control('reflection.thinking_mode').value !== expected) {{
              throw new Error(alias + ' loaded as ' + relationshipRoot.control('reflection.thinking_mode').value);
            }}
            relationshipRoot.root.save();
            const saved = await waitForPost(beforeLoad);
            if (saved.reflection.thinking_mode !== expected) {{
              throw new Error(alias + ' saved as ' + saved.reflection.thinking_mode);
            }}
          }}

          const surfacing = registered.find((item) => item.id === 'models-surfacing');
          const surfacingRoot = makeRoot();
          surfacing.mount(surfacingRoot.root);
          currentConfig = {{
            gateway: {{ retrieval_mode: 'legacy', direct_render_mode: 'COMPACT' }},
            recall: {{}}, memory_diffusion: {{}}, surfacing: {{}},
          }};
          const beforeLoad = posts.length;
          await surfacing.activate({{ scopeId: 'legacy-retrieval' }});
          if (posts.length !== beforeLoad) throw new Error('activation silently persisted legacy retrieval');
          if (surfacingRoot.control('gateway.retrieval_mode').value !== 'bucket') {{
            throw new Error('legacy retrieval was blanked');
          }}
          if (surfacingRoot.control('gateway.direct_render_mode').value !== 'compact') {{
            throw new Error('direct-render alias was blanked');
          }}
          surfacingRoot.root.save();
          const saved = await waitForPost(beforeLoad);
          if (saved.gateway.retrieval_mode !== 'bucket' || saved.gateway.direct_render_mode !== 'compact') {{
            throw new Error('canonical gateway modes were not round-tripped');
          }}
          process.stdout.write('ok');
        }})().catch((error) => {{
          console.error(error && error.stack || error);
          process.exitCode = 1;
        }});
        """
    )
    completed = _run_node(script)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "ok"


def test_dehydration_panel_keeps_presets_discovery_health_and_readiness() -> None:
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(ASSET))}, 'utf8');
        const registered = [];
        const calls = [];
        let currentConfig = {{
          dehydration: {{
            model: '', base_url: '', api_format: 'openai_compat',
            api_key_masked: '', max_tokens: 1024, temperature: 0.1, timeout_seconds: 30,
          }},
          gateway: {{
            domain_sentinel_enabled: true, domain_sentinel_model: '',
            domain_sentinel_base_url: '', domain_sentinel_api_key_masked: '',
          }},
        }};
        const window = {{ OmbreDashboardFeatureFactories: [], confirm: () => true, prompt: () => null }};
        vm.runInNewContext(source, {{
          window, console, Promise, setTimeout, clearTimeout,
          FormData: class FormData {{}},
        }}, {{ filename: 'models-data.js' }});
        const app = {{
          registerPanel(panel) {{ registered.push(panel); }},
          commands: {{}}, ui: {{}},
          api: {{
            post(path, body) {{ calls.push([path, body]); return Promise.resolve({{ path, body }}); }},
            readJson(response) {{
              if (response.path === '/api/models') return Promise.resolve({{ ok: true, models: ['model-a', 'model-b'] }});
              if (response.path === '/api/test/dehydration') return Promise.resolve({{ ok: true, message: 'connection healthy' }});
              return Promise.resolve(response || {{}});
            }},
          }},
          store: {{
            invalidate() {{}},
            resource() {{ return Promise.resolve(currentConfig); }},
          }},
        }};
        window.OmbreDashboardFeatureFactories[0](app);

        const listeners = {{ click: [] }};
        const status = {{ textContent: '', dataset: {{}}, hidden: true }};
        const preset = {{ value: '' }};
        const readiness = {{ textContent: '', dataset: {{}} }};
        const modelList = {{ innerHTML: '' }};
        const controls = Object.create(null);
        function control(path) {{
          if (!controls[path]) controls[path] = {{ value: '', checked: false, placeholder: '' }};
          return controls[path];
        }}
        const root = {{
          classList: {{ add() {{}} }}, innerHTML: '', setAttribute() {{}},
          querySelector(selector) {{
            if (selector === '[data-role="status"]') return status;
            if (selector === '[data-role="dehydration-preset"]') return preset;
            if (selector === '[data-role="dehydration-readiness"]') return readiness;
            if (selector === '[data-role="dehydration-model-list"]') return modelList;
            const match = selector.match(/data-config-field="([^"]+)/);
            return match ? control(match[1]) : null;
          }},
          querySelectorAll() {{ return []; }},
          addEventListener(name, handler) {{ (listeners[name] ||= []).push(handler); }},
        }};
        function click(button) {{
          const event = {{ target: {{ closest(selector) {{
            if (selector === '[data-action]') return button.dataset.action ? button : null;
            if (selector.includes('[data-action="reload"]')) return button.dataset.action === 'reload' ? button : null;
            if (selector.includes('[data-write-action]')) return button.dataset.writeAction ? button : null;
            return null;
          }} }} }};
          for (const handler of listeners.click) handler(event);
        }}

        (async () => {{
          const panel = registered.find((item) => item.id === 'models-dehydration');
          panel.mount(root);
          await panel.activate({{ scopeId: 'dehydration-tools' }});
          if (!/No saved API key/.test(readiness.textContent) || readiness.dataset.tone !== 'warning') {{
            throw new Error('missing-key readiness warning was not rendered');
          }}

          preset.value = 'deepseek';
          click({{ dataset: {{ action: 'apply-dehydration-preset' }} }});
          if (control('dehydration.base_url').value !== 'https://api.deepseek.com/v1') throw new Error('preset URL missing');
          if (control('dehydration.model').value !== 'deepseek-chat') throw new Error('preset model missing');
          if (control('dehydration.api_format').value !== 'openai_compat') throw new Error('preset format missing');

          control('dehydration.api_key').value = 'typed-secret';
          click({{ dataset: {{ action: 'discover-dehydration-models' }} }});
          for (let attempt = 0; attempt < 30 && !modelList.innerHTML; attempt += 1) {{
            await new Promise((resolve) => setTimeout(resolve, 1));
          }}
          const discovery = calls.find((call) => call[0] === '/api/models');
          if (!discovery || discovery[1].api_key !== 'typed-secret') throw new Error('model discovery did not use the typed key');
          if (!modelList.innerHTML.includes('model-a') || !modelList.innerHTML.includes('model-b')) throw new Error('remote models not rendered');

          click({{ dataset: {{ action: 'choose-dehydration-model', model: 'model-b' }} }});
          if (control('dehydration.model').value !== 'model-b') throw new Error('remote model selection failed');

          click({{ dataset: {{ action: 'test-dehydration' }} }});
          for (let attempt = 0; attempt < 30 && !/healthy/.test(status.textContent); attempt += 1) {{
            await new Promise((resolve) => setTimeout(resolve, 1));
          }}
          if (!calls.some((call) => call[0] === '/api/test/dehydration')) throw new Error('health test was not called');
          if (!/healthy/.test(status.textContent)) throw new Error('health result missing: ' + status.textContent);

          currentConfig = Object.assign({{}}, currentConfig, {{
            dehydration: Object.assign({{}}, currentConfig.dehydration, {{ api_key_masked: 'sk-***' }}),
          }});
          await panel.activate({{ scopeId: 'dehydration-ready' }});
          if (readiness.dataset.tone !== 'success' || !/saved API key/.test(readiness.textContent)) {{
            throw new Error('saved-key readiness was not rendered');
          }}
          process.stdout.write('ok');
        }})().catch((error) => {{
          console.error(error && error.stack || error);
          process.exitCode = 1;
        }});
        """
    )
    completed = _run_node(script)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "ok"
