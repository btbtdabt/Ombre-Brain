(function initModelsDataWorkspace() {
  'use strict';

  window.OmbreDashboardFeatureFactories = window.OmbreDashboardFeatureFactories || [];
  window.OmbreDashboardFeatureFactories.push(function modelsDataFactory(app) {
    if (!app || typeof app.registerPanel !== 'function') return;

    var CONFIG_RESOURCE = 'models-data:config';
    var EFFECTIVE_CONFIG_RESOURCE = 'models-data:effective-config';
    var MAX_VAULT_ARCHIVE_BYTES = 512 * 1024 * 1024;
    var CANONICAL_EDITOR_RESOURCES = Object.freeze({
      'models-upstream': 'gateway-upstreams',
      'models-dehydration': 'dehydration-settings',
      'models-reranker': 'reranker-settings',
      'models-persona': 'persona-model-settings',
      'models-dream': 'dream-model-settings',
      'models-relationship-memory': 'relationship-memory-settings',
      'models-portrait-settings': 'portrait-settings',
      'models-surfacing': 'memory-surfacing-settings',
    });
    var state = {
      roots: Object.create(null),
      upstreams: [],
    };

    function role(root, name) {
      return root && root.querySelector('[data-role="' + name + '"]');
    }

    function markCanonicalEditor(root, panelId) {
      var resource = CANONICAL_EDITOR_RESOURCES[panelId];
      if (!root || !resource) return;
      root.setAttribute('data-canonical-editor-resource', resource);
      root.setAttribute('data-canonical-editor-panel', panelId);
      root.setAttribute('data-canonical-editor-mounted', 'true');
    }

    function field(root, path) {
      return root && root.querySelector('[data-config-field="' + path + '"]');
    }

    function escapeAttribute(value) {
      var text = value == null ? '' : String(value);
      if (app.ui && typeof app.ui.escapeAttr === 'function') {
        return app.ui.escapeAttr(text);
      }
      return text.replace(/&/g, '&amp;').replace(/"/g, '&quot;')
        .replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function errorMessage(error, fallback) {
      if (error && error.message) return String(error.message);
      return fallback || 'Request failed';
    }

    function setStatus(root, message, tone) {
      var target = role(root, 'status');
      if (!target) return;
      target.textContent = message || '';
      target.dataset.tone = tone || '';
      target.hidden = !message;
    }

    function setBusy(root, busy) {
      if (!root) return;
      Array.prototype.forEach.call(root.querySelectorAll('[data-write-action]'), function (button) {
        button.disabled = Boolean(busy);
      });
      root.setAttribute('aria-busy', busy ? 'true' : 'false');
    }

    function clearWriteOnlySecretInputs(root) {
      if (!root || typeof root.querySelectorAll !== 'function') return;
      Array.prototype.forEach.call(
        root.querySelectorAll('[data-write-only-secret="true"]'),
        function (control) { control.value = ''; }
      );
    }

    function confirmAction(message) {
      if (app.ui && typeof app.ui.confirm === 'function') {
        return Promise.resolve(app.ui.confirm(message));
      }
      return Promise.resolve(window.confirm(message));
    }

    function promptAction(message) {
      if (app.ui && typeof app.ui.prompt === 'function') {
        return Promise.resolve(app.ui.prompt(message));
      }
      return Promise.resolve(window.prompt(message));
    }

    async function jsonResponse(response) {
      return app.api.readJson(response);
    }

    async function getJson(path, options) {
      return jsonResponse(await app.api.get(path, options));
    }

    async function postJson(path, body, options) {
      return jsonResponse(await app.api.post(path, body, options));
    }

    function loadConfig(scopeId, refresh) {
      return app.store.resource(CONFIG_RESOURCE, function (context) {
        return getJson('/api/config', { signal: context.signal });
      }, {
        scopeId: scopeId || null,
        refresh: Boolean(refresh),
        ttlMs: 15000,
      });
    }

    function getPath(source, path) {
      return path.split('.').reduce(function (value, part) {
        return value && typeof value === 'object' ? value[part] : undefined;
      }, source);
    }

    function numberValue(control, path) {
      var parsed = Number(control.value);
      if (!Number.isFinite(parsed)) throw new Error(path + ' must be a number');
      return parsed;
    }

    function readControl(root, spec) {
      var control = field(root, spec.path);
      if (!control) throw new Error('Missing editor field: ' + spec.path);
      if (spec.type === 'checkbox') return Boolean(control.checked);
      if (spec.type === 'number') return numberValue(control, spec.path);
      var value = String(control.value || '').trim();
      if (spec.secret && !value) return undefined;
      return value;
    }

    function collectSections(root, specs) {
      var patch = {};
      specs.forEach(function (spec) {
        var value = readControl(root, spec);
        if (value === undefined) return;
        var parts = spec.path.split('.');
        var section = parts.shift();
        if (!patch[section]) patch[section] = {};
        patch[section][parts.join('.')] = value;
      });
      return patch;
    }

    function populateFields(root, specs, config) {
      specs.forEach(function (spec) {
        var control = field(root, spec.path);
        if (!control) return;
        if (spec.secret) {
          control.value = '';
          var masked = getPath(config, spec.maskPath || (spec.path + '_masked'));
          control.placeholder = masked
            ? 'Configured (' + masked + ') · leave blank to keep it'
            : 'Leave blank to keep the current value';
          return;
        }
        var value = getPath(config, spec.path);
        if (value === undefined || value === null) value = spec.default;
        if (spec.type === 'select') value = normalizeSelectMode(spec.path, value);
        if (spec.type === 'checkbox') control.checked = Boolean(value);
        else {
          control.value = value == null ? '' : String(value);
          if (!control.value && spec.effectivePath) {
            var effective = getPath(config, spec.effectivePath);
            control.placeholder = effective ? 'Inherited: ' + String(effective) : '';
          }
        }
      });
    }

    function normalizeSelectMode(path, value) {
      var normalized = String(value == null ? '' : value).trim().toLowerCase().replace(/_/g, '-');
      if (path === 'reflection.thinking_mode') {
        if (['enabled', 'enable', 'on', 'true', 'thinking'].indexOf(normalized) >= 0) return 'enabled';
        if (['disabled', 'disable', 'off', 'false', 'non-thinking'].indexOf(normalized) >= 0) return 'disabled';
        return '';
      }
      if (path === 'gateway.retrieval_mode') {
        if (normalized === 'legacy') return 'bucket';
        return normalized === 'bucket' ? 'bucket' : 'graph';
      }
      if (path === 'gateway.direct_render_mode') {
        return ['auto', 'compact', 'full'].indexOf(normalized) >= 0 ? normalized : 'auto';
      }
      return value;
    }

    function optionMarkup(option) {
      var item = typeof option === 'string' ? { value: option, label: option || 'Default' } : option;
      return '<option value="' + escapeAttribute(item.value) + '">' + escapeAttribute(item.label) + '</option>';
    }

    function fieldMarkup(spec) {
      var id = 'models-field-' + spec.path.replace(/[^a-z0-9_-]+/gi, '-');
      var attributes = ' id="' + escapeAttribute(id) + '" data-config-field="' + escapeAttribute(spec.path) + '"';
      if (spec.min !== undefined) attributes += ' min="' + escapeAttribute(spec.min) + '"';
      if (spec.max !== undefined) attributes += ' max="' + escapeAttribute(spec.max) + '"';
      if (spec.step !== undefined) attributes += ' step="' + escapeAttribute(spec.step) + '"';
      if (spec.required) attributes += ' required';
      if (spec.secret) attributes += ' data-write-only-secret="true"';
      var control;
      if (spec.type === 'select') {
        control = '<select' + attributes + '>' + spec.options.map(optionMarkup).join('') + '</select>';
      } else if (spec.type === 'textarea') {
        control = '<textarea' + attributes + ' rows="' + (spec.rows || 3) + '"></textarea>';
      } else if (spec.type === 'checkbox') {
        control = '<input type="checkbox"' + attributes + ' />';
      } else {
        control = '<input type="' + (spec.secret ? 'password' : (spec.type || 'text')) + '"' + attributes +
          (spec.secret ? ' autocomplete="new-password" spellcheck="false"' : '') + ' />';
      }
      return '<label class="models-data-field' + (spec.type === 'checkbox' ? ' is-toggle' : '') + '" for="' + escapeAttribute(id) + '">' +
        '<span>' + escapeAttribute(spec.label) + '</span>' + control +
        (spec.help ? '<small>' + escapeAttribute(spec.help) + '</small>' : '') + '</label>';
    }

    function sectionMarkup(title, description, specs) {
      return '<section class="models-data-card"><header><div><h3>' + escapeAttribute(title) + '</h3>' +
        (description ? '<p>' + escapeAttribute(description) + '</p>' : '') + '</div></header>' +
        '<div class="models-data-grid">' + specs.map(fieldMarkup).join('') + '</div></section>';
    }

    var PERSIST_REQUIRED_PANELS = Object.freeze({
      'models-upstream': true,
      'models-dehydration': true,
      'models-reranker': true,
      'models-persona': true,
      'models-dream': true,
      'models-relationship-memory': true,
      'models-surfacing': true,
    });

    function writeActionsMarkup(panelId) {
      var runtimeAction = PERSIST_REQUIRED_PANELS[panelId]
        ? ''
        : '<button type="button" data-write-action="runtime">Apply now</button>';
      return '<div class="models-data-writebar">' +
        runtimeAction +
        '<button type="button" data-write-action="persist" class="primary">Save to config</button>' +
        '<button type="button" data-write-action="persist-env">Save config + new keys</button>' +
        '<button type="button" data-action="reload">Reload</button>' +
        '<span>' + (PERSIST_REQUIRED_PANELS[panelId] ? 'Gateway changes are always saved. ' : '') +
        'Blank key fields never erase an existing secret.</span></div>';
    }

    var DEHYDRATION_FIELDS = [
      { path: 'dehydration.model', label: 'Tagging model', default: '' },
      { path: 'dehydration.base_url', label: 'Tagging base URL', default: '' },
      { path: 'dehydration.api_key', label: 'Tagging API key', secret: true, maskPath: 'dehydration.api_key_masked' },
      { path: 'dehydration.max_tokens', label: 'Maximum output tokens', type: 'number', min: 64, max: 32000, step: 1, default: 1024 },
      { path: 'dehydration.temperature', label: 'Temperature', type: 'number', min: 0, max: 2, step: 0.05, default: 0.1 },
      { path: 'dehydration.timeout_seconds', label: 'Timeout (seconds)', type: 'number', min: 1, max: 300, step: 1, default: 30 },
      { path: 'dehydration.api_format', label: 'API format', type: 'select', options: ['openai_compat', 'anthropic', 'gemini'], default: 'openai_compat' },
    ];
    var DEHYDRATION_PRESETS = Object.freeze({
      deepseek: { api_format: 'openai_compat', base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
      gemini: { api_format: 'openai_compat', base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/', model: 'gemini-2.5-flash-lite' },
      siliconflow: { api_format: 'openai_compat', base_url: 'https://api.siliconflow.cn/v1', model: 'deepseek-ai/DeepSeek-V3' },
      anthropic: { api_format: 'anthropic', base_url: 'https://api.anthropic.com', model: 'claude-3-5-haiku-latest' },
      custom: { api_format: 'openai_compat', base_url: '', model: '' },
    });
    var DOMAIN_SENTINEL_FIELDS = [
      { path: 'gateway.domain_sentinel_enabled', label: 'Enable domain sentinel', type: 'checkbox', default: true },
      { path: 'gateway.domain_sentinel_model', label: 'Sentinel model', default: '' },
      { path: 'gateway.domain_sentinel_base_url', label: 'Sentinel base URL', default: '' },
      { path: 'gateway.domain_sentinel_api_key', label: 'Sentinel API key', secret: true, maskPath: 'gateway.domain_sentinel_api_key_masked' },
    ];
    var RERANKER_FIELDS = [
      { path: 'reranker.enabled', label: 'Enable reranker', type: 'checkbox', default: false },
      { path: 'reranker.model', label: 'Model', default: '' },
      { path: 'reranker.base_url', label: 'Base URL', default: '', effectivePath: 'reranker.effective_base_url', help: 'Leave blank to inherit the embedding or dehydration provider.' },
      { path: 'reranker.api_key', label: 'API key', secret: true, maskPath: 'reranker.api_key_masked' },
      { path: 'reranker.timeout_seconds', label: 'Timeout (seconds)', type: 'number', min: 1, max: 120, step: 1, default: 15 },
      { path: 'reranker.candidate_limit', label: 'Candidate limit', type: 'number', min: 1, max: 100, step: 1, default: 30 },
      { path: 'reranker.score_weight', label: 'Score weight', type: 'number', min: 0, max: 1, step: 0.05, default: 0.7 },
    ];
    var PERSONA_FIELDS = [
      { path: 'persona.enabled', label: 'Enable Persona', type: 'checkbox', default: true },
      { path: 'persona.event_recording_enabled', label: 'Record persona events', type: 'checkbox', default: true },
      { path: 'persona.conflict_nudge_enabled', label: 'Conflict guidance', type: 'checkbox', default: false },
      { path: 'persona.model', label: 'Model', default: '', effectivePath: 'persona.effective_model', help: 'Leave blank to use the runtime default shown as the placeholder.' },
      { path: 'persona.base_url', label: 'Base URL', default: '', effectivePath: 'persona.effective_base_url', help: 'Leave blank to use the runtime default shown as the placeholder.' },
      { path: 'persona.api_key', label: 'API key', secret: true, maskPath: 'persona.api_key_masked' },
    ];
    var DREAM_FIELDS = [
      { path: 'dream.enabled', label: 'Enable Dream', type: 'checkbox', default: true },
      { path: 'dream.auto_enabled', label: 'Automatic dream generation', type: 'checkbox', default: true },
      { path: 'dream.surface_enabled', label: 'Allow dreams to surface', type: 'checkbox', default: true },
      { path: 'dream.inject_enabled', label: 'Inject surfaced dreams', type: 'checkbox', default: false },
      { path: 'dream.retain_after_inject', label: 'Retain after injection', type: 'checkbox', default: true },
      { path: 'dream.model', label: 'Model', default: '', effectivePath: 'dream.effective_model', help: 'Leave blank to use the runtime default shown as the placeholder.' },
      { path: 'dream.base_url', label: 'Base URL', default: '', effectivePath: 'dream.effective_base_url', help: 'Leave blank to use the runtime default shown as the placeholder.' },
      { path: 'dream.api_key', label: 'API key', secret: true, maskPath: 'dream.api_key_masked' },
      { path: 'dream.daily_hour', label: 'Daily hour (0–23)', type: 'number', min: 0, max: 23, step: 1, default: 3 },
      { path: 'dream.daily_probability', label: 'Daily probability', type: 'number', min: 0, max: 1, step: 0.05, default: 0.4 },
      { path: 'dream.min_material_count', label: 'Minimum material count', type: 'number', min: 1, max: 20, step: 1, default: 5 },
      { path: 'dream.material_window_hours', label: 'Material window (hours)', type: 'number', min: 1, max: 168, step: 1, default: 48 },
      { path: 'dream.identity_anchor_id', label: 'Identity anchor ID', default: '' },
    ];
    var REFLECTION_FIELDS = [
      { path: 'reflection.enabled', label: 'Enable relationship memory', type: 'checkbox', default: true },
      { path: 'reflection.auto_enabled', label: 'Automatic reflection', type: 'checkbox', default: true },
      { path: 'reflection.daily_enabled', label: 'Daily reflection', type: 'checkbox', default: true },
      { path: 'reflection.daily_min_memory_items', label: 'Minimum daily memory items', type: 'number', min: 0, max: 100, step: 1, default: 5 },
      { path: 'reflection.daily_conversation_turn_limit', label: 'Conversation turn limit', type: 'number', min: 0, max: 80, step: 1, default: 12 },
      { path: 'reflection.daily_chat_memory_mode', label: 'Daily chat memory mode', type: 'select', options: ['auto', 'review', 'off'], default: 'review' },
      { path: 'reflection.daily_chat_memory_turn_limit', label: 'Daily chat memory turn limit', type: 'number', min: 0, max: 10000, step: 1, default: 0 },
      { path: 'reflection.memory_affect_anchor_enabled', label: 'Memory affect anchors', type: 'checkbox', default: false },
      { path: 'reflection.relationship_weather_affect_anchor_enabled', label: 'Relationship-weather affect anchors', type: 'checkbox', default: false },
      { path: 'reflection.model', label: 'Model', default: '', effectivePath: 'reflection.effective_model', help: 'Leave blank to inherit Persona or Dehydration.' },
      { path: 'reflection.thinking_mode', label: 'Thinking mode', type: 'select', options: ['', 'disabled', 'enabled'], default: '' },
      { path: 'reflection.base_url', label: 'Base URL', default: '', effectivePath: 'reflection.effective_base_url', help: 'Leave blank to inherit Embedding, Persona, or Dehydration.' },
      { path: 'reflection.api_key', label: 'API key', secret: true, maskPath: 'reflection.api_key_masked' },
    ];
    var SELF_ANCHOR_FIELDS = [
      { path: 'self_anchor.entry_bucket_id', label: 'Self-anchor entry bucket ID', default: '', help: 'The explicit handoff entry for identity and relationship continuity.' },
    ];
    var PORTRAIT_FIELDS = [
      { path: 'portrait.enabled', label: 'Enable portrait', type: 'checkbox', default: true },
      { path: 'portrait.auto_enabled', label: 'Automatic updates', type: 'checkbox', default: true },
      { path: 'portrait.auto_initial_enabled', label: 'Automatic initial portrait', type: 'checkbox', default: false },
      { path: 'portrait.daily_enabled', label: 'Daily processing', type: 'checkbox', default: true },
      { path: 'portrait.material_limit', label: 'Material limit', type: 'number', min: 1, max: 100, step: 1, default: 18 },
      { path: 'portrait.first_run_material_limit', label: 'First-run material limit', type: 'number', min: 1, max: 500, step: 1, default: 160 },
      { path: 'portrait.user_rewrite_evidence_delta', label: 'User rewrite evidence delta', type: 'number', min: 1, max: 100, step: 1, default: 10 },
      { path: 'portrait.manual_suppress_days', label: 'Manual suppression (days)', type: 'number', min: 1, max: 90, step: 1, default: 14 },
    ];
    var SURFACING_GATEWAY_FIELDS = [
      { path: 'gateway.cooldown_hours', label: 'Recall cooldown (hours)', type: 'number', min: 0, max: 720, step: 0.5, default: 6 },
      { path: 'gateway.skip_recent_rounds', label: 'Skip recent rounds', type: 'number', min: 0, max: 10000, step: 1, default: 5 },
      { path: 'gateway.recent_context_cooldown_hours', label: 'Recent-context cooldown (hours)', type: 'number', min: 0, max: 720, step: 0.5, default: 6 },
      { path: 'gateway.recent_context_reentry_idle_hours', label: 'Re-entry idle threshold (hours)', type: 'number', min: 0, max: 8760, step: 0.5, default: 24 },
      { path: 'gateway.recent_context_budget', label: 'Recent-context token budget', type: 'number', min: 0, max: 50000, step: 1, default: 300 },
      { path: 'gateway.recalled_memory_budget', label: 'Recalled-memory token budget', type: 'number', min: 0, max: 50000, step: 1, default: 900 },
      { path: 'gateway.related_memory_budget', label: 'Related-memory token budget', type: 'number', min: 0, max: 50000, step: 1, default: 220 },
      { path: 'gateway.memory_detail_recall_enabled', label: 'Detailed memory recall', type: 'checkbox', default: false },
      { path: 'gateway.memory_detail_recall_max_ids', label: 'Detailed recall max IDs', type: 'number', min: 1, max: 50, step: 1, default: 3 },
      { path: 'gateway.memory_detail_recall_budget', label: 'Detailed recall token budget', type: 'number', min: 0, max: 50000, step: 1, default: 1200 },
      { path: 'gateway.current_inner_state_interval_rounds', label: 'Inner-state interval (rounds)', type: 'number', min: 0, max: 10000, step: 1, default: 15 },
      { path: 'gateway.direct_render_mode', label: 'Direct render mode', type: 'select', options: ['auto', 'compact', 'full'], default: 'auto' },
      { path: 'gateway.retrieval_mode', label: 'Retrieval mode', type: 'select', options: ['graph', 'bucket'], default: 'graph' },
      { path: 'gateway.operit_context_rewrite_enabled', label: 'Operit context rewrite', type: 'checkbox', default: false },
      { path: 'gateway.word_map_hint_enabled', label: 'Word-map hints', type: 'checkbox', default: false },
      { path: 'gateway.query_planner_enabled', label: 'Query planner', type: 'checkbox', default: false },
    ];
    var RECALL_FIELDS = [
      { path: 'recall.query_resurface_enabled', label: 'Allow low-hit query resurfacing', type: 'checkbox', default: false },
    ];
    var DIFFUSION_FIELDS = [
      { path: 'memory_diffusion.enabled', label: 'Enable memory diffusion', type: 'checkbox', default: true },
      { path: 'memory_diffusion.top_k', label: 'Top K', type: 'number', min: 1, max: 100, step: 1, default: 4 },
      { path: 'memory_diffusion.min_activation', label: 'Minimum activation', type: 'number', min: 0, max: 1, step: 0.01, default: 0.18 },
      { path: 'memory_diffusion.chain_walk_enabled', label: 'Enable chain walk', type: 'checkbox', default: false },
      { path: 'memory_diffusion.chain_max_hops', label: 'Maximum chain hops', type: 'number', min: 1, max: 20, step: 1, default: 6 },
      { path: 'memory_diffusion.chain_min_confidence', label: 'Minimum chain confidence', type: 'number', min: 0, max: 1, step: 0.01, default: 0.72 },
      { path: 'memory_diffusion.chain_max_frontier', label: 'Maximum chain frontier', type: 'number', min: 1, max: 1000, step: 1, default: 24 },
    ];
    var SURFACING_LIMIT_FIELDS = [
      { path: 'surfacing.breath_max_results', label: 'Breath maximum results', type: 'number', min: 1, max: 50, step: 1, default: 10 },
      { path: 'surfacing.breath_max_tokens', label: 'Breath token limit', type: 'number', min: 500, max: 20000, step: 100, default: 4000 },
      { path: 'surfacing.feel_max_tokens', label: 'Feel token limit', type: 'number', min: 500, max: 20000, step: 100, default: 4000 },
    ];

    function parseList(value) {
      return String(value || '').split(/[\r\n,]+/).map(function (item) {
        return item.trim();
      }).filter(Boolean);
    }

    function parseSecretLines(value) {
      var normalized = String(value == null ? '' : value).replace(/\r\n?/g, '\n');
      return normalized ? normalized.split('\n') : [];
    }

    function parseModels(value) {
      return String(value || '').split(/[\r\n,]+/).map(function (item) {
        var line = item.trim();
        if (!line) return null;
        var separator = line.indexOf('=>');
        if (separator < 0) return line;
        var id = line.slice(0, separator).trim();
        var upstreamModel = line.slice(separator + 2).trim();
        if (!id || !upstreamModel) throw new Error('Model aliases must use public-id => upstream-id');
        return { id: id, upstream_model: upstreamModel };
      }).filter(Boolean);
    }

    function upstreamRowValues(row) {
      function value(name, preserveWhitespace) {
        var control = row.querySelector('[data-upstream-field="' + name + '"]');
        var raw = String(control && control.value || '');
        return preserveWhitespace ? raw : raw.trim();
      }
      var name = value('name');
      var baseUrl = value('base_url');
      if (!name) throw new Error('Every upstream needs a unique name.');
      if (!baseUrl) throw new Error(name + ' needs a base URL.');
      return {
        name: name,
        protocol: value('protocol') || 'openai',
        base_url: baseUrl,
        api_key_envs: parseList(value('api_key_envs')),
        api_key_values: parseSecretLines(value('api_key_values', true)),
        default_model: value('default_model'),
        prompt_cache: value('prompt_cache'),
        prompt_cache_retention: value('prompt_cache_retention'),
        anthropic_version: value('anthropic_version'),
        anthropic_beta: value('anthropic_beta'),
        gemini_base_url: value('gemini_base_url'),
        gemini_auth: value('gemini_auth'),
        models: parseModels(value('models')),
      };
    }

    function buildUpstreamPatch(root) {
      var names = Object.create(null);
      var upstreams = Array.prototype.map.call(root.querySelectorAll('[data-upstream-row]'), function (row) {
        var upstream = upstreamRowValues(row);
        if (names[upstream.name]) throw new Error('Duplicate upstream name: ' + upstream.name);
        names[upstream.name] = true;
        return upstream;
      });
      return { gateway: { upstreams: upstreams } };
    }

    function buildDehydrationPatch(root) {
      return collectSections(root, DEHYDRATION_FIELDS.concat(DOMAIN_SENTINEL_FIELDS));
    }

    function dehydrationToolsMarkup() {
      return '<section class="models-data-card"><header><div><h3>Provider tools</h3>' +
        '<p>Start from a known provider tuple, discover remote models, and test the saved runtime connection.</p></div></header>' +
        '<div class="models-data-grid"><label class="models-data-field"><span>Provider preset</span>' +
        '<select data-role="dehydration-preset"><option value="">Choose a preset</option>' +
        '<option value="deepseek">DeepSeek</option><option value="gemini">Gemini</option>' +
        '<option value="siliconflow">SiliconFlow</option><option value="anthropic">Anthropic</option>' +
        '<option value="custom">Custom OpenAI-compatible</option></select></label>' +
        '<div class="models-data-field"><span>Actions</span><div class="models-data-row">' +
        '<button type="button" data-action="apply-dehydration-preset">Apply preset</button>' +
        '<button type="button" data-action="discover-dehydration-models">Discover models</button>' +
        '<button type="button" data-action="test-dehydration">Test saved connection</button></div></div></div>' +
        '<p data-role="dehydration-readiness" class="models-data-note" aria-live="polite"></p>' +
        '<div data-role="dehydration-model-list" class="models-data-model-list"></div></section>';
    }

    function updateDehydrationReadiness(root, config) {
      var readiness = role(root, 'dehydration-readiness');
      if (!readiness) return;
      var configured = Boolean(getPath(config || {}, 'dehydration.api_key_masked'));
      readiness.dataset.tone = configured ? 'success' : 'warning';
      readiness.textContent = configured
        ? 'A saved API key is available. Model discovery can use it without exposing the value.'
        : 'No saved API key detected. Enter one and use “Save config + new keys” before relying on tagging or the health test.';
    }

    function applyDehydrationPreset(root) {
      var presetControl = role(root, 'dehydration-preset');
      var preset = DEHYDRATION_PRESETS[String(presetControl && presetControl.value || '')];
      if (!preset) {
        setStatus(root, 'Choose a provider preset first.', 'warning');
        return;
      }
      field(root, 'dehydration.api_format').value = preset.api_format;
      field(root, 'dehydration.base_url').value = preset.base_url;
      field(root, 'dehydration.model').value = preset.model;
      setStatus(root, 'Preset applied locally. Review it, then save.', 'success');
    }

    async function discoverDehydrationModels(root) {
      var keyControl = field(root, 'dehydration.api_key');
      var typedKey = String(keyControl && keyControl.value || '').trim();
      var hasSavedKey = Boolean(
        getPath(state.currentConfig || {}, 'dehydration.api_key_masked')
      );
      if (!typedKey && !hasSavedKey) {
        setStatus(root, 'Enter an API key or save one before discovering models.', 'warning');
        return;
      }
      setBusy(root, true);
      setStatus(root, 'Discovering remote models…', 'loading');
      try {
        var result = await postJson('/api/models', {
          api_key: typedKey || '__use_current__',
          base_url: String(field(root, 'dehydration.base_url').value || '').trim(),
          api_format: String(field(root, 'dehydration.api_format').value || 'openai_compat'),
        }, { timeoutMs: 30000 });
        var models = result && Array.isArray(result.models) ? result.models : [];
        if (!result || result.ok === false || !models.length) {
          throw new Error(result && result.error || 'No models were returned.');
        }
        var list = role(root, 'dehydration-model-list');
        if (list) {
          list.innerHTML = models.map(function (model) {
            var value = String(model || '');
            return '<button type="button" data-action="choose-dehydration-model" data-model="' +
              escapeAttribute(value) + '">' + escapeAttribute(value) + '</button>';
          }).join('');
        }
        setStatus(root, 'Discovered ' + models.length + ' model(s). Choose one below.', 'success');
      } catch (error) {
        setStatus(root, 'Model discovery failed: ' + errorMessage(error), 'error');
      } finally {
        setBusy(root, false);
      }
    }

    async function testDehydrationConnection(root) {
      setBusy(root, true);
      setStatus(root, 'Testing the saved dehydration connection…', 'loading');
      try {
        var result = await postJson('/api/test/dehydration', {}, { timeoutMs: 30000 });
        if (!result || result.ok === false) {
          throw new Error(result && result.error || 'Connection test failed.');
        }
        setStatus(root, result.message || 'Saved dehydration connection is healthy.', 'success');
      } catch (error) {
        setStatus(root, 'Connection test failed: ' + errorMessage(error), 'error');
      } finally {
        setBusy(root, false);
      }
    }

    function handleDehydrationAction(root, event) {
      var button = event.target.closest('[data-action]');
      if (!button) return;
      var action = button.dataset.action;
      if (action === 'apply-dehydration-preset') applyDehydrationPreset(root);
      if (action === 'discover-dehydration-models') discoverDehydrationModels(root);
      if (action === 'test-dehydration') testDehydrationConnection(root);
      if (action === 'choose-dehydration-model') {
        field(root, 'dehydration.model').value = String(button.dataset.model || '');
        setStatus(root, 'Model selected. Review it, then save.', 'success');
      }
    }

    function buildRerankerPatch(root) {
      return collectSections(root, RERANKER_FIELDS);
    }

    function buildPersonaPatch(root) {
      return collectSections(root, PERSONA_FIELDS);
    }

    function buildDreamPatch(root) {
      return collectSections(root, DREAM_FIELDS);
    }

    function buildRelationshipMemoryPatch(root) {
      return collectSections(root, REFLECTION_FIELDS.concat(SELF_ANCHOR_FIELDS));
    }

    function buildPortraitPatch(root) {
      return collectSections(root, PORTRAIT_FIELDS);
    }

    function buildSurfacingPatch(root) {
      return collectSections(root, SURFACING_GATEWAY_FIELDS.concat(RECALL_FIELDS, DIFFUSION_FIELDS, SURFACING_LIMIT_FIELDS));
    }

    var CONFIG_PATCH_BUILDERS = {
      'models-upstream': buildUpstreamPatch,
      'models-dehydration': buildDehydrationPatch,
      'models-reranker': buildRerankerPatch,
      'models-persona': buildPersonaPatch,
      'models-dream': buildDreamPatch,
      'models-relationship-memory': buildRelationshipMemoryPatch,
      'models-portrait-settings': buildPortraitPatch,
      'models-surfacing': buildSurfacingPatch,
    };

    function configSaveOutcome(result, reloaded, runtimeOnly) {
      var messages = [];
      var tone = 'success';
      var gatewayRestart = Boolean(result && result.gateway_restart_required);
      var brainRestart = Boolean(result && (
        result.brain_restart_required
        || (result.restart_required && !gatewayRestart)
      ));

      if (runtimeOnly) {
        messages.push('Applied to this process. Use Save to config for restart durability.');
      } else if (result && result.gateway_live_apply_failed) {
        messages.push('Saved durably, but live apply to the external ombre-gateway service failed. Restart the external ombre-gateway service to finish applying this change.');
        tone = 'warning';
      } else if (gatewayRestart) {
        messages.push('Saved durably. Restart the external ombre-gateway service to apply the remaining Gateway-owned settings.');
        tone = 'warning';
      } else if (result && result.gateway_live_apply_applied) {
        messages.push('Saved and applied live to the external ombre-gateway service.');
      } else {
        messages.push('Saved and applied.');
      }

      if (brainRestart) {
        messages.push('Restart Ombre Brain from the header to apply its startup settings.');
        tone = 'warning';
      }
      if (!reloaded) {
        messages.push('The save succeeded, but the refreshed values could not be loaded.');
        tone = 'warning';
      }
      return { message: messages.join(' '), tone: tone, brainRestart: brainRestart };
    }

    async function saveConfigPanel(panelId, mode) {
      var root = state.roots[panelId];
      var builder = CONFIG_PATCH_BUILDERS[panelId];
      if (!root || !builder) return;
      var patch;
      try {
        patch = builder(root);
      } catch (error) {
        setStatus(root, errorMessage(error, 'Invalid configuration'), 'error');
        return;
      }
      var runtimeOnly = mode === 'runtime' && !PERSIST_REQUIRED_PANELS[panelId];
      patch.persist = !runtimeOnly;
      patch.persist_env = mode === 'persist-env';
      if (patch.persist_env) {
        var accepted = await confirmAction('Save the newly entered API keys to the private .env file? Existing blank key fields stay unchanged.');
        if (!accepted) return;
      }
      setBusy(root, true);
      setStatus(root, runtimeOnly ? 'Applying runtime configuration…' : 'Saving configuration…', 'loading');
      try {
        var result = await postJson('/api/config', patch, { timeoutMs: 60000 });
        clearWriteOnlySecretInputs(root);
        app.store.invalidate([CONFIG_RESOURCE, EFFECTIVE_CONFIG_RESOURCE]);
        var reloaded = await loadAndPopulate(panelId, null, true, true);
        var outcome = configSaveOutcome(result, reloaded, runtimeOnly);
        if (app.ui && typeof app.ui.setRestartRequired === 'function') {
          app.ui.setRestartRequired(
            outcome.brainRestart,
            outcome.brainRestart ? 'Ombre Brain startup settings are waiting' : ''
          );
        }
        setStatus(root, outcome.message, outcome.tone);
      } catch (error) {
        setStatus(root, 'Save failed: ' + errorMessage(error), 'error');
      } finally {
        setBusy(root, false);
      }
    }

    function bindConfigActions(root, panelId) {
      root.addEventListener('click', function (event) {
        if (panelId === 'models-dehydration') {
          handleDehydrationAction(root, event);
        }
        var button = event.target.closest('[data-write-action], [data-action="reload"]');
        if (!button) return;
        if (button.dataset.writeAction) saveConfigPanel(panelId, button.dataset.writeAction);
        else loadAndPopulate(panelId, null, true);
      });
    }

    function configPanelMarkup(panelId, title, description, sections) {
      return '<header class="models-data-header"><div><h2>' + escapeAttribute(title) + '</h2><p>' + escapeAttribute(description) + '</p></div></header>' +
        '<div class="models-data-status" data-role="status" aria-live="polite" hidden></div>' +
        '<div class="models-data-stack">' + sections.join('') + '</div>' + writeActionsMarkup(panelId);
    }

    function mountConfigPanel(root, panelId, title, description, sections) {
      state.roots[panelId] = root;
      root.classList.add('models-data-panel');
      markCanonicalEditor(root, panelId);
      root.innerHTML = configPanelMarkup(panelId, title, description, sections);
      bindConfigActions(root, panelId);
    }

    function renderUpstreamRows(root) {
      var list = role(root, 'upstream-list');
      if (!list) return;
      list.textContent = '';
      if (!state.upstreams.length) {
        var empty = document.createElement('p');
        empty.className = 'models-data-empty';
        empty.textContent = 'No upstream providers configured. Add one to expose models through the Gateway.';
        list.appendChild(empty);
        return;
      }
      state.upstreams.forEach(function (upstream, index) {
        var row = document.createElement('section');
        row.className = 'models-data-card models-data-upstream';
        row.dataset.upstreamRow = String(index);
        row.innerHTML = '<header><div><h3 data-role="provider-title">Upstream provider</h3><p data-role="provider-key-state"></p></div>' +
          '<button type="button" class="danger" data-action="remove-upstream" data-index="' + index + '">Remove</button></header>' +
          '<div class="models-data-grid">' +
          '<label class="models-data-field"><span>Name</span><input data-upstream-field="name" required /></label>' +
          '<label class="models-data-field"><span>Protocol</span><select data-upstream-field="protocol"><option value="openai">OpenAI compatible</option><option value="anthropic">Anthropic</option></select></label>' +
          '<label class="models-data-field models-data-span-2"><span>Base URL</span><input data-upstream-field="base_url" required /></label>' +
          '<label class="models-data-field"><span>Default model</span><input data-upstream-field="default_model" /></label>' +
          '<label class="models-data-field"><span>Prompt cache</span><select data-upstream-field="prompt_cache"><option value="">Default</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic automatic</option><option value="anthropic_explicit">Anthropic explicit</option></select></label>' +
          '<label class="models-data-field"><span>Cache retention</span><select data-upstream-field="prompt_cache_retention"><option value="">Default</option><option value="24h">24h (OpenAI)</option><option value="1h">1h (Anthropic)</option></select></label>' +
          '<label class="models-data-field"><span>Anthropic version</span><input data-upstream-field="anthropic_version" /></label>' +
          '<label class="models-data-field models-data-span-2"><span>Anthropic beta flags</span><input data-upstream-field="anthropic_beta" /></label>' +
          '<label class="models-data-field models-data-span-2"><span>Gemini native base URL</span><input data-upstream-field="gemini_base_url" placeholder="https://generativelanguage.googleapis.com/v1beta" /><small>Optional native generateContent endpoint base for Gemini models.</small></label>' +
          '<label class="models-data-field"><span>Gemini authentication</span><select data-upstream-field="gemini_auth"><option value="">Automatic</option><option value="bearer">Bearer</option><option value="google">x-goog-api-key</option><option value="both">Both headers</option></select></label>' +
          '<label class="models-data-field"><span>API key env names</span><textarea rows="3" data-upstream-field="api_key_envs" placeholder="OMBRE_GATEWAY_PROVIDER_API_KEY"></textarea><small>One dedicated OMBRE_GATEWAY_*_API_KEY variable per line.</small></label>' +
          '<label class="models-data-field"><span>New API key values</span><textarea rows="3" data-upstream-field="api_key_values" data-write-only-secret="true" autocomplete="new-password" spellcheck="false"></textarea><small>Positional: one line per env name. Keep blank lines when rotating a later slot. Only written by “Save config + new keys”.</small></label>' +
          '<label class="models-data-field models-data-span-2"><span>Models</span><textarea rows="5" data-upstream-field="models" placeholder="public-model\npublic-alias => provider-model"></textarea><small>One model per line. Use public-id =&gt; upstream-id for aliases.</small></label>' +
          '</div>';
        function assign(name, value) {
          var control = row.querySelector('[data-upstream-field="' + name + '"]');
          if (control) control.value = value == null ? '' : String(value);
        }
        assign('name', upstream.name);
        assign('protocol', upstream.protocol || 'openai');
        assign('base_url', upstream.base_url);
        assign('default_model', upstream.default_model);
        assign('prompt_cache', upstream.prompt_cache);
        assign('prompt_cache_retention', upstream.prompt_cache_retention);
        assign('anthropic_version', upstream.anthropic_version);
        assign('anthropic_beta', upstream.anthropic_beta);
        assign('gemini_base_url', upstream.gemini_base_url);
        assign('gemini_auth', upstream.gemini_auth);
        assign('api_key_envs', (upstream.api_key_envs || []).join('\n'));
        assign('models', (upstream.models || []).map(function (model) {
          return typeof model === 'string' ? model : model.id + (model.upstream_model && model.upstream_model !== model.id ? ' => ' + model.upstream_model : '');
        }).join('\n'));
        role(row, 'provider-title').textContent = upstream.name || 'New upstream';
        var keyState = role(row, 'provider-key-state');
        keyState.textContent = upstream.key_count
          ? upstream.key_count + ' key slot(s) available'
          : (upstream.has_direct_api_key ? 'A legacy direct key is preserved server-side' : 'No key detected');
        list.appendChild(row);
      });
    }

    function mountUpstream(root) {
      var panelId = 'models-upstream';
      state.roots[panelId] = root;
      root.classList.add('models-data-panel');
      markCanonicalEditor(root, panelId);
      root.innerHTML = '<header class="models-data-header"><div><h2>Gateway upstream models</h2>' +
        '<p>The canonical editor for provider routing, public model aliases, prompt caching and secret slots.</p></div>' +
        '<button type="button" data-action="add-upstream">Add provider</button></header>' +
        '<div class="models-data-status" data-role="status" aria-live="polite" hidden></div>' +
        '<div class="models-data-stack" data-role="upstream-list"></div>' + writeActionsMarkup(panelId);
      bindConfigActions(root, panelId);
      root.addEventListener('click', function (event) {
        var button = event.target.closest('[data-action="add-upstream"], [data-action="remove-upstream"]');
        if (!button) return;
        if (button.dataset.action === 'add-upstream') {
          state.upstreams.push({ protocol: 'openai', api_key_envs: [], models: [] });
        } else {
          var index = Number(button.dataset.index);
          if (Number.isInteger(index)) state.upstreams.splice(index, 1);
        }
        renderUpstreamRows(root);
      });
    }

    async function loadAndPopulate(panelId, scopeId, refresh, preserveStatus) {
      var root = state.roots[panelId];
      if (!root) return false;
      if (!preserveStatus) setStatus(root, 'Loading configuration…', 'loading');
      try {
        var config = await loadConfig(scopeId, refresh);
        state.currentConfig = config;
        if (panelId === 'models-upstream') {
          state.upstreams = Array.isArray(config.gateway && config.gateway.upstreams)
            ? config.gateway.upstreams.map(function (item) { return Object.assign({}, item); })
            : [];
          renderUpstreamRows(root);
        } else if (panelId === 'models-dehydration') {
          populateFields(root, DEHYDRATION_FIELDS.concat(DOMAIN_SENTINEL_FIELDS), config);
          updateDehydrationReadiness(root, config);
        } else if (panelId === 'models-reranker') {
          populateFields(root, RERANKER_FIELDS, config);
        } else if (panelId === 'models-persona') {
          populateFields(root, PERSONA_FIELDS, config);
        } else if (panelId === 'models-dream') {
          populateFields(root, DREAM_FIELDS, config);
        } else if (panelId === 'models-relationship-memory') {
          populateFields(root, REFLECTION_FIELDS.concat(SELF_ANCHOR_FIELDS), config);
        } else if (panelId === 'models-portrait-settings') {
          populateFields(root, PORTRAIT_FIELDS, config);
        } else if (panelId === 'models-surfacing') {
          populateFields(root, SURFACING_GATEWAY_FIELDS.concat(RECALL_FIELDS, DIFFUSION_FIELDS, SURFACING_LIMIT_FIELDS), config);
        }
        if (!preserveStatus) setStatus(root, 'Configuration loaded.', 'success');
        return true;
      } catch (error) {
        if (!preserveStatus) {
          setStatus(root, 'Load failed: ' + errorMessage(error), 'error');
        }
        return false;
      }
    }

    function mountCanonicalEmbeddingEditor(root) {
      var documentRef = window.document;
      var editor = documentRef && documentRef.querySelector(
        '[data-canonical-editor-resource="embedding-settings"][data-canonical-editor-panel="models-embeddings"]'
      );
      var host = role(root, 'canonical-editor-host');
      if (!editor || !host) {
        setStatus(root, 'The canonical embedding editor could not be mounted.', 'error');
        return false;
      }
      if (typeof editor.removeAttribute === 'function') {
        editor.removeAttribute('data-unified-superseded-by');
      }
      editor.setAttribute('data-canonical-editor-mounted', 'true');
      editor.hidden = false;
      host.appendChild(editor);
      return true;
    }

    function populateCanonicalEmbeddingEditor(root, config) {
      var embedding = config && config.embedding && typeof config.embedding === 'object'
        ? config.embedding
        : {};
      function setValue(id, value) {
        var control = root.querySelector('#' + id);
        if (control) control.value = value == null ? '' : String(value);
        return control;
      }
      setValue('cfg-emb-enabled', embedding.enabled ? 'true' : 'false');
      setValue('cfg-emb-model', embedding.model || '');
      setValue('cfg-emb-base-url', embedding.base_url || '');
      setValue('cfg-emb-format', embedding.api_format || 'openai_compat');
      var backend = setValue('cfg-emb-backend', embedding.backend || 'api');
      var note = root.querySelector('#cfg-emb-backend-note');
      var options = Array.isArray(embedding.backend_options) ? embedding.backend_options : [];
      var selected = options.find(function (item) {
        return item && String(item.value) === String(backend && backend.value);
      });
      if (note) note.textContent = selected && selected.note ? String(selected.note) : '';
      if (backend) {
        backend.onchange = function () {
          var match = options.find(function (item) {
            return item && String(item.value) === String(backend.value);
          });
          if (note) note.textContent = match && match.note ? String(match.note) : '';
        };
      }
      var key = root.querySelector('#cfg-emb-api-key');
      if (key) {
        key.value = '';
        key.placeholder = embedding.api_key_masked
          ? 'Current: ' + String(embedding.api_key_masked) + ' (leave blank to keep)'
          : 'Not configured';
      }
    }

    async function activateEmbeddings(context) {
      var root = state.roots['models-embeddings'];
      if (!root) return;
      setStatus(root, 'Loading embedding configuration…', 'loading');
      try {
        var config = await loadConfig(context && context.scopeId, false);
        populateCanonicalEmbeddingEditor(root, config);
        if (typeof window.onEmbFormatChange === 'function') window.onEmbFormatChange();
        setStatus(root, 'Embedding configuration loaded.', 'success');
      } catch (error) {
        setStatus(root, 'Load failed: ' + errorMessage(error), 'error');
      }
      ['refreshEmbInfo', 'refreshEnvConfig', 'loadLocalEmbStatus'].forEach(function (name) {
        if (typeof window[name] !== 'function') return;
        Promise.resolve(window[name]()).catch(function () {});
      });
    }

    function mountEmbeddings(root) {
      state.roots['models-embeddings'] = root;
      root.classList.add('models-data-panel');
      root.innerHTML = '<header class="models-data-header"><div><h2>Embeddings</h2><p>The single canonical editor for provider configuration, API keys, health checks, local models, backfill and migration.</p></div></header>' +
        '<div class="models-data-status" data-role="status" aria-live="polite" hidden></div>' +
        '<div class="models-data-canonical-host" data-role="canonical-editor-host"></div>';
      mountCanonicalEmbeddingEditor(root);
    }

    function mountEffectiveConfig(root) {
      state.roots['models-effective-config'] = root;
      root.classList.add('models-data-panel');
      root.innerHTML = '<header class="models-data-header"><div><h2>Effective configuration</h2><p>Read-only merged runtime report with secret values redacted by the server.</p></div><button type="button" data-action="refresh-effective">Refresh</button></header>' +
        '<div class="models-data-status" data-role="status" aria-live="polite" hidden></div><pre class="models-data-json" data-role="effective-json"></pre>';
      root.addEventListener('click', function (event) {
        if (event.target.closest('[data-action="refresh-effective"]')) loadEffectiveConfig(null, true);
      });
    }

    async function loadEffectiveConfig(scopeId, refresh) {
      var root = state.roots['models-effective-config'];
      if (!root) return;
      setStatus(root, 'Loading effective configuration…', 'loading');
      try {
        var report = await app.store.resource(EFFECTIVE_CONFIG_RESOURCE, function (context) {
          return getJson('/api/config/effective', { signal: context.signal });
        }, { scopeId: scopeId || null, refresh: Boolean(refresh), ttlMs: 10000 });
        role(root, 'effective-json').textContent = JSON.stringify(report, null, 2);
        setStatus(root, 'Effective configuration loaded.', 'success');
      } catch (error) {
        role(root, 'effective-json').textContent = '';
        setStatus(root, 'Load failed: ' + errorMessage(error), 'error');
      }
    }

    async function downloadVault(root) {
      if (state.vaultExportPending) return;
      state.vaultExportPending = true;
      setBusy(root, true);
      setStatus(root, 'Preparing and verifying the vault archive…', 'loading');
      var handedOff = false;
      try {
        var prepared = await postJson('/api/backup/export/prepare', {}, { timeoutMs: 0 });
        var ticket = String(prepared && prepared.ticket || '');
        if (!prepared || prepared.ok === false || !ticket) {
          throw new Error(prepared && prepared.error || 'The export ticket was not created.');
        }
        var link = document.createElement('a');
        var exportUrl = typeof app.apiUrl === 'function'
          ? app.apiUrl('/api/backup/export')
          : '/api/backup/export';
        link.href = exportUrl + '?ticket=' + encodeURIComponent(ticket);
        link.download = '';
        link.rel = 'noopener';
        document.body.appendChild(link);
        link.click();
        link.remove();
        handedOff = true;
        setStatus(root, 'Verified archive handed to the browser. Waiting for the server stream to finish…', 'loading');
        while (true) {
          var exportStatus = await getJson('/api/backup/export/status', {
            timeoutMs: 15000,
            retries: 0,
          });
          if (!exportStatus || !exportStatus.active) break;
          await new Promise(function (resolve) { setTimeout(resolve, 350); });
        }
        state.vaultExportPending = false;
        setBusy(root, false);
        setStatus(root, 'The server finished the archive stream. Check browser downloads for the file.', 'success');
      } catch (error) {
        if (handedOff) {
          setStatus(root, 'The download started, but completion could not be verified: ' + errorMessage(error) + '. Export remains locked in this panel to prevent overlap; reload after checking browser downloads.', 'warning');
        } else {
          state.vaultExportPending = false;
          setBusy(root, false);
          setStatus(root, 'Export failed: ' + errorMessage(error), 'error');
        }
      }
    }

    function validateVaultArchive(file) {
      if (!file) throw new Error('Choose a vault archive first.');
      if (!file.size) throw new Error('The selected archive is empty.');
      if (file.size > MAX_VAULT_ARCHIVE_BYTES) throw new Error('The archive is larger than 512 MB.');
      if (!/\.zip$/i.test(file.name || '')) throw new Error('Vault restore only accepts .zip archives.');
      var allowedTypes = ['', 'application/zip', 'application/x-zip-compressed', 'application/octet-stream'];
      if (allowedTypes.indexOf(String(file.type || '').toLowerCase()) < 0) {
        throw new Error('The selected file is not recognized as a ZIP archive.');
      }
      return file;
    }

    function restoreVaultOutcome(result) {
      function count(value) {
        var number = Number(value);
        return Number.isFinite(number) && number >= 0 ? Math.floor(number) : 0;
      }
      var created = count(result && result.created);
      var overwritten = count(result && result.overwritten);
      var restoredIds = result && Array.isArray(result.restored_ids) ? result.restored_ids : [];
      var indexErrors = result && result.derived_indexes && Array.isArray(result.derived_indexes.errors)
        ? result.derived_indexes.errors
        : [];
      var message = 'Vault restore complete: ' + created + ' created, ' + overwritten +
        ' overwritten, ' + restoredIds.length + ' restored ID(s).';
      if (indexErrors.length) {
        message += ' Derived-index refresh reported ' + indexErrors.length +
          ' error(s); the source memories were restored, but affected indexes may need repair.';
      }
      return { message: message, tone: indexErrors.length ? 'warning' : 'success' };
    }

    async function restoreVault(root, mode) {
      var input = role(root, 'vault-file');
      var file;
      try {
        file = validateVaultArchive(input && input.files && input.files[0]);
      } catch (error) {
        setStatus(root, errorMessage(error), 'error');
        return;
      }
      if (mode === 'overwrite') {
        var phrase = await promptAction('Overwrite can replace existing vault files. Type OVERWRITE to continue.');
        if (phrase !== 'OVERWRITE') {
          setStatus(root, 'Overwrite restore cancelled.', 'warning');
          return;
        }
      } else {
        var accepted = await confirmAction('Restore missing vault data from ' + file.name + '? Existing files will be skipped.');
        if (!accepted) return;
      }
      setBusy(root, true);
      setStatus(root, 'Uploading and verifying vault archive. Keep this page open; large restores can take several minutes…', 'loading');
      try {
        var form = new FormData();
        form.append('file', file, file.name);
        var response = await app.api.upload('/api/backup/restore?mode=' + encodeURIComponent(mode), form, { timeoutMs: 0 });
        var result = await app.api.readJson(response);
        app.store.invalidate(['buckets', 'memory:buckets', CONFIG_RESOURCE, EFFECTIVE_CONFIG_RESOURCE]);
        var outcome = restoreVaultOutcome(result);
        setStatus(root, outcome.message, outcome.tone);
        if (input) input.value = '';
      } catch (error) {
        setStatus(root, 'Restore failed: ' + errorMessage(error), 'error');
      } finally {
        setBusy(root, false);
      }
    }

    function mountFullVault(root) {
      state.roots['models-full-vault'] = root;
      root.classList.add('models-data-panel');
      root.innerHTML = '<header class="models-data-header"><div><h2>Verified full vault</h2><p>Portable ZIP backup and validated restore for the complete current memory vault.</p></div></header>' +
        '<div class="models-data-status" data-role="status" aria-live="polite" hidden></div>' +
        '<div class="models-data-vault-grid"><section class="models-data-card"><h3>Export</h3><p>Create a server-verified archive and download it locally.</p><button type="button" data-write-action="download-vault">Download full vault</button></section>' +
        '<section class="models-data-card"><h3>Restore</h3><p>Skip mode adds missing items. Overwrite mode requires an explicit typed confirmation.</p>' +
        '<label class="models-data-file"><span>Vault ZIP archive</span><input type="file" data-role="vault-file" accept=".zip,application/zip" /></label>' +
        '<div class="models-data-row"><button type="button" data-write-action="restore-skip">Restore missing</button><button type="button" class="danger" data-write-action="restore-overwrite">Overwrite restore</button></div></section></div>';
      root.addEventListener('click', function (event) {
        var button = event.target.closest('[data-write-action]');
        if (!button) return;
        if (button.dataset.writeAction === 'download-vault') downloadVault(root);
        if (button.dataset.writeAction === 'restore-skip') restoreVault(root, 'skip');
        if (button.dataset.writeAction === 'restore-overwrite') restoreVault(root, 'overwrite');
      });
    }

    function mountDehydration(root) {
      mountConfigPanel(root, 'models-dehydration', 'Dehydration & tagging', 'Configure the canonical compression/tagging model and the domain sentinel used by the Gateway.', [
        dehydrationToolsMarkup(),
        sectionMarkup('Dehydration model', 'Compresses and tags durable memories.', DEHYDRATION_FIELDS),
        sectionMarkup('Domain sentinel', 'Detects whether a conversation belongs to a special memory domain.', DOMAIN_SENTINEL_FIELDS),
      ]);
    }

    function mountReranker(root) {
      mountConfigPanel(root, 'models-reranker', 'Reranker', 'Tune the optional semantic reranker applied after recall candidate generation.', [
        sectionMarkup('Reranker model', 'Runtime changes are immediate; Gateway workers should restart after provider changes.', RERANKER_FIELDS),
      ]);
    }

    function mountPersona(root) {
      mountConfigPanel(root, 'models-persona', 'Persona Settings', 'Controls persona-state inference and event recording, without duplicating the Persona State viewer.', [
        sectionMarkup('Persona inference', 'The API key is returned only as a masked readiness signal.', PERSONA_FIELDS),
      ]);
    }

    function mountDream(root) {
      mountConfigPanel(root, 'models-dream', 'Dream model', 'Configure dream generation, surfacing and its daily material window.', [
        sectionMarkup('Dream generation', 'The Dream viewer remains in Memory; this is its single settings editor.', DREAM_FIELDS),
      ]);
    }

    function mountRelationshipMemory(root) {
      mountConfigPanel(root, 'models-relationship-memory', 'Relationship memory', 'Reflection, daily chat-memory extraction and the explicit self-anchor handoff entry.', [
        sectionMarkup('Reflection & daily memory', 'Controls automatic relationship-memory processing.', REFLECTION_FIELDS),
        sectionMarkup('Self anchor', 'A stable identity/relationship entry point used for explicit handoff.', SELF_ANCHOR_FIELDS),
      ]);
    }

    function mountPortraitSettings(root) {
      mountConfigPanel(root, 'models-portrait-settings', 'Portrait settings', 'Tune automatic portrait material selection. Portrait content itself remains in the Memory workspace.', [
        sectionMarkup('Portrait automation', 'Limits and suppression windows for evidence-based portrait updates.', PORTRAIT_FIELDS),
      ]);
    }

    function mountSurfacing(root) {
      mountConfigPanel(root, 'models-surfacing', 'Memory surfacing', 'Recall budgets, resurfacing policy, diffusion graph traversal and Breath/Feel limits.', [
        sectionMarkup('Gateway recall', 'Controls what the Gateway can inject and how much context it may use.', SURFACING_GATEWAY_FIELDS),
        sectionMarkup('Query resurfacing', 'Allows an unrelated old echo only when a query has low confidence.', RECALL_FIELDS),
        sectionMarkup('Memory diffusion', 'Controls graph-based activation and optional chain walking.', DIFFUSION_FIELDS),
        sectionMarkup('Surface limits', 'Shared Breath and Feel result/token caps.', SURFACING_LIMIT_FIELDS),
      ]);
    }

    function configActivation(panelId) {
      return function (context) {
        return loadAndPopulate(panelId, context && context.scopeId, false);
      };
    }

    app.registerPanel({
      id: 'models-upstream',
      workspace: 'models-data',
      label: 'Upstream Models',
      order: 10,
      mount: mountUpstream,
      activate: configActivation('models-upstream'),
    });
    app.registerPanel({
      id: 'models-dehydration',
      workspace: 'models-data',
      label: 'Dehydration',
      order: 20,
      mount: mountDehydration,
      activate: configActivation('models-dehydration'),
    });
    app.registerPanel({
      id: 'models-embeddings',
      workspace: 'models-data',
      label: 'Embeddings',
      order: 30,
      mount: mountEmbeddings,
      activate: activateEmbeddings,
    });
    app.registerPanel({
      id: 'models-reranker',
      workspace: 'models-data',
      label: 'Reranker',
      order: 40,
      mount: mountReranker,
      activate: configActivation('models-reranker'),
    });
    app.registerPanel({
      id: 'models-persona',
      workspace: 'models-data',
      label: 'Persona Settings',
      order: 50,
      mount: mountPersona,
      activate: configActivation('models-persona'),
    });
    app.registerPanel({
      id: 'models-dream',
      workspace: 'models-data',
      label: 'Dream',
      order: 60,
      mount: mountDream,
      activate: configActivation('models-dream'),
    });
    app.registerPanel({
      id: 'models-relationship-memory',
      workspace: 'models-data',
      label: 'Relationship Memory',
      order: 70,
      mount: mountRelationshipMemory,
      activate: configActivation('models-relationship-memory'),
    });
    app.registerPanel({
      id: 'models-portrait-settings',
      workspace: 'models-data',
      label: 'Portrait Settings',
      order: 80,
      mount: mountPortraitSettings,
      activate: configActivation('models-portrait-settings'),
    });
    app.registerPanel({
      id: 'models-surfacing',
      workspace: 'models-data',
      label: 'Surfacing',
      order: 90,
      mount: mountSurfacing,
      activate: configActivation('models-surfacing'),
    });
    app.registerPanel({
      id: 'models-effective-config',
      workspace: 'models-data',
      label: 'Effective Config',
      order: 100,
      mount: mountEffectiveConfig,
      activate: function (context) { return loadEffectiveConfig(context && context.scopeId, false); },
    });
    app.registerPanel({
      id: 'models-full-vault',
      workspace: 'models-data',
      label: 'Full Vault',
      order: 110,
      mount: mountFullVault,
      activate: function () { return Promise.resolve(); },
    });
  });
})();
