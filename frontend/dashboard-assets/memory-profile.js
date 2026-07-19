(function () {
  'use strict';

  window.OmbreDashboardFeatureFactories = window.OmbreDashboardFeatureFactories || [];
  window.OmbreDashboardFeatureFactories.push(function memoryProfileFactory(app) {
    var state = {
      roots: Object.create(null),
      personaSession: '',
      portrait: null,
      portraitActions: [],
      portraitEdit: null,
      profileFacts: [],
      profileProposals: [],
      anchorProposals: [],
      anchorProposalBucket: {},
      profileProposalWrites: new Set(),
      anchorProposalWrites: new Set(),
      reads: Object.create(null),
    };

    var READ_TIMEOUT_MS = 15000;

    function escapeHtml(value) {
      return app.ui.escape(value == null ? '' : String(value));
    }

    function escapeAttribute(value) {
      return app.ui.escapeAttr(value == null ? '' : String(value));
    }

    function errorMessage(data, fallback) {
      if (data && typeof data === 'object') {
        return String(data.error || data.reason || data.detail || fallback || '操作失败');
      }
      return String(fallback || '操作失败');
    }

    async function apiJson(method, path, options) {
      if (!app.api || typeof app.api[method] !== 'function') {
        throw new Error('Dashboard API client is unavailable');
      }
      var requestOptions = Object.assign({}, options || {});
      delete requestOptions.body;
      var response = options && options.body !== undefined
        ? await app.api[method](path, options.body, requestOptions)
        : await app.api[method](path, requestOptions);
      var data = {};
      var parseError = null;
      try {
        data = typeof app.api.readJson === 'function'
          ? await app.api.readJson(response)
          : await response.json();
      } catch (error) {
        parseError = error;
        data = error && error.payload && typeof error.payload === 'object' ? error.payload : {};
      }
      if (!response || !response.ok) {
        throw new Error(errorMessage(data, parseError && parseError.message || response && response.statusText));
      }
      if (parseError) throw parseError;
      return data || {};
    }

    function readTracker(name) {
      if (!state.reads[name]) {
        state.reads[name] = {
          active: false,
          generation: 0,
          scopeId: '',
          controller: null,
        };
      }
      return state.reads[name];
    }

    function activateReads(name, context) {
      var tracker = readTracker(name);
      var scopeId = String(context && context.scopeId || '');
      if (tracker.scopeId && scopeId && tracker.scopeId !== scopeId && tracker.controller) {
        tracker.controller.abort();
        tracker.generation += 1;
      }
      tracker.scopeId = scopeId || tracker.scopeId;
      tracker.active = true;
    }

    function beginRead(name) {
      var tracker = readTracker(name);
      if (!tracker.active) return null;
      if (tracker.controller) tracker.controller.abort();
      tracker.generation += 1;
      tracker.controller = new window.AbortController();
      return {
        generation: tracker.generation,
        scopeId: tracker.scopeId,
        controller: tracker.controller,
        signal: tracker.controller.signal,
      };
    }

    function isCurrentRead(name, request) {
      var tracker = readTracker(name);
      return Boolean(request && tracker.active &&
        tracker.generation === request.generation &&
        tracker.scopeId === request.scopeId &&
        tracker.controller === request.controller &&
        !request.signal.aborted);
    }

    function deactivateReads(name) {
      var tracker = readTracker(name);
      tracker.active = false;
      tracker.generation += 1;
      if (tracker.controller) tracker.controller.abort();
      tracker.controller = null;
      tracker.scopeId = '';
    }

    function rootFor(name) {
      return state.roots[name] || null;
    }

    function role(root, name) {
      return root && root.querySelector('[data-role="' + name + '"]');
    }

    function setStatus(root, name, message, tone) {
      var element = role(root, name);
      if (!element) return;
      app.ui.setStatus(element, message || '', tone || '');
    }

    async function confirmAction(message) {
      if (typeof app.ui.confirm === 'function') {
        return Boolean(await app.ui.confirm(message));
      }
      return window.confirm(message);
    }

    function clamp(value, minimum, maximum) {
      var number = Number(value);
      if (!Number.isFinite(number)) number = 0;
      return Math.max(minimum, Math.min(maximum, number));
    }

    function fixed(value, digits) {
      return Number(value || 0).toFixed(digits);
    }

    function rows(value) {
      return Array.isArray(value) ? value : [];
    }

    function empty(message) {
      return '<div class="ob-empty">' + escapeHtml(message) + '</div>';
    }

    function loading(message) {
      return '<div class="ob-loading" role="status">' + escapeHtml(message) + '</div>';
    }

    function errorBlock(message) {
      return '<div class="ob-error" role="alert">' + escapeHtml(message) + '</div>';
    }

    function statusChip(label, tone) {
      return '<span class="ob-chip' + (tone ? ' ' + escapeAttribute(tone) : '') + '">' +
        escapeHtml(label) + '</span>';
    }

    function formatTimeAgo(value) {
      var time = Date.parse(value || '');
      if (!Number.isFinite(time)) return String(value || '');
      var seconds = Math.max(0, Math.floor((Date.now() - time) / 1000));
      if (seconds < 60) return seconds + 's前';
      var minutes = Math.floor(seconds / 60);
      if (minutes < 60) return minutes + 'm前';
      var hours = Math.floor(minutes / 60);
      if (hours < 24) return hours + 'h前';
      var days = Math.floor(hours / 24);
      if (days < 30) return days + 'd前';
      return Math.floor(days / 30) + 'mo前';
    }

    function personaMeter(label, value, tone) {
      var percent = Math.round(clamp(value, 0, 1) * 100);
      return '<div class="ob-persona-meter">' +
        '<span>' + escapeHtml(label) + '</span>' +
        '<div class="ob-meter-track"><span class="' + escapeAttribute(tone || '') + '" style="width:' + percent + '%"></span></div>' +
        '<strong>' + percent + '</strong>' +
      '</div>';
    }

    function personaDeltas(event) {
      var parts = [];
      [
        ['affect', event.affect_delta || {}],
        ['rel', event.relationship_delta || {}],
        ['trait', event.personality_delta || {}],
      ].forEach(function (entry) {
        Object.keys(entry[1]).forEach(function (key) {
          var value = Number(entry[1][key] || 0);
          if (!Number.isFinite(value) || Math.abs(value) < 0.0001) return;
          parts.push(statusChip(entry[0] + '.' + key + ' ' + (value > 0 ? '+' : '') + value.toFixed(3), 'delta'));
        });
      });
      return parts.length ? '<div class="ob-chip-row">' + parts.join('') + '</div>' : '';
    }

    function renderPersonaSessions(sessions, activeSession) {
      var known = sessions.some(function (session) {
        return String(session.session_id || '') === activeSession;
      });
      var options = sessions.slice();
      if (activeSession && !known) {
        options.unshift({ session_id: activeSession, mood_label: 'current' });
      }
      if (!options.length) {
        options.push({ session_id: activeSession || 'dashboard-preview', mood_label: 'current' });
      }
      return options.map(function (session) {
        var id = String(session.session_id || '');
        var label = id + (session.mood_label ? ' · ' + session.mood_label : '');
        return '<option value="' + escapeAttribute(id) + '"' + (id === activeSession ? ' selected' : '') + '>' +
          escapeHtml(label) + '</option>';
      }).join('');
    }

    function renderPersonaEvents(events) {
      if (!events.length) {
        return empty('还没有 Persona 事件。通过 Gateway 聊一轮后，这里会出现评估结果和状态增量。');
      }
      return '<div class="ob-persona-events">' + events.map(function (event) {
        var thought = event.inner_thought || event.residue || event.perceived_intent || event.error || 'no thought recorded';
        var trigger = event.surface_trigger || event.perceived_intent || '';
        return '<article class="ob-persona-event">' +
          '<div class="ob-event-kind">' + escapeHtml(event.event_type || 'unknown') + '</div>' +
          '<div class="ob-event-body"><strong>' + escapeHtml(thought) + '</strong>' +
            (trigger ? '<small>' + escapeHtml(trigger) + '</small>' : '') +
            (event.residue && event.residue !== thought ? '<p>' + escapeHtml(event.residue) + '</p>' : '') +
            '<small>' + escapeHtml(event.mood_label || 'mood unset') + ' · confidence ' + fixed(event.confidence, 2) +
              ' · #' + escapeHtml(event.message_hash || '') + '</small>' +
            personaDeltas(event) +
          '</div>' +
          '<time>' + escapeHtml(formatTimeAgo(event.created_at)) + '</time>' +
        '</article>';
      }).join('') + '</div>';
    }

    function renderPersona(data) {
      var root = rootFor('persona');
      if (!root) return;
      var dashboardState = data.state || {};
      var affect = dashboardState.affect || {};
      var relationship = dashboardState.relationship || {};
      var personality = dashboardState.personality || {};
      var config = data.config || {};
      var sessions = rows(data.sessions);
      var activeSession = String(data.active_session_id || state.personaSession || 'dashboard-preview');
      state.personaSession = activeSession;
      role(root, 'session').innerHTML =
        '<label>Session<select data-action="persona-session">' + renderPersonaSessions(sessions, activeSession) + '</select></label>' +
        '<button type="button" data-action="persona-refresh">刷新</button>';
      role(root, 'content').innerHTML =
        '<div class="ob-persona-grid">' +
          '<section class="ob-card ob-persona-mood">' +
            '<div class="ob-persona-orb" style="opacity:' + (0.72 + clamp(affect.arousal || 0.34, 0, 1) * 0.28).toFixed(2) + '"></div>' +
            '<div><h3>' + escapeHtml(affect.mood_label || 'warm_neutral') + '</h3>' +
              '<p>V' + fixed(affect.valence, 2) + ' · A' + fixed(affect.arousal, 2) + ' · session ' + escapeHtml(activeSession) + '</p>' +
              '<blockquote>' + escapeHtml(affect.inner_thought || affect.residue || '当前没有明显余味。') + '</blockquote>' +
              '<div class="ob-chip-row">' +
                statusChip(config.enabled ? 'engine on' : 'engine paused', config.enabled ? 'active' : 'muted') +
                statusChip(config.event_recording_enabled === false ? 'events paused' : 'events on', config.event_recording_enabled === false ? 'muted' : 'active') +
                statusChip(config.api_ready ? 'LLM ready' : 'fallback state', config.api_ready ? 'active' : 'muted') +
                statusChip(config.model || 'model unset', '') +
              '</div>' +
            '</div>' +
          '</section>' +
          '<section class="ob-card"><h3>Affect / Relationship</h3>' +
            personaMeter('情绪效价 valence', affect.valence, 'warm') +
            personaMeter('唤醒度 arousal', affect.arousal, '') +
            personaMeter('温柔 tenderness', affect.tenderness, 'warm') +
            personaMeter('占有 possessiveness', affect.possessiveness, 'guard') +
            personaMeter('想念 longing', affect.longing, 'warm') +
            personaMeter('安全 security', affect.security, 'warm') +
            personaMeter('保护 protective', affect.protective_drive, 'guard') +
            personaMeter('亲和 affinity', relationship.affinity, 'warm') +
            personaMeter('主导 dominance', relationship.dominance, '') +
            personaMeter('防御 defensiveness', relationship.defensiveness, 'guard') +
            personaMeter('信任 trust', relationship.trust, 'warm') +
          '</section>' +
          '<section class="ob-card"><h3>Personality</h3>' +
            personaMeter('开放 openness', personality.openness, '') +
            personaMeter('自律 conscientiousness', personality.conscientiousness, '') +
            personaMeter('外向 extraversion', personality.extraversion, '') +
            personaMeter('温柔 agreeableness', personality.agreeableness, 'warm') +
            personaMeter('敏感 neuroticism', personality.neuroticism, 'guard') +
          '</section>' +
        '</div>' +
        '<section class="ob-card"><h3>Recent Persona Events</h3>' + renderPersonaEvents(rows(data.events)) + '</section>';
    }

    async function loadPersona(sessionId) {
      var root = rootFor('persona');
      if (!root) return;
      if (sessionId !== undefined) state.personaSession = String(sessionId || '');
      var request = beginRead('persona');
      if (!request) return;
      role(root, 'content').innerHTML = loading('读取 Persona 状态…');
      var path = '/api/persona?events_limit=20&sessions_limit=30';
      if (state.personaSession) path += '&session_id=' + encodeURIComponent(state.personaSession);
      try {
        var data = await apiJson('get', path, {
          signal: request.signal,
          timeoutMs: READ_TIMEOUT_MS,
        });
        if (!isCurrentRead('persona', request)) return;
        renderPersona(data);
      } catch (error) {
        if (!isCurrentRead('persona', request)) return;
        role(root, 'content').innerHTML = errorBlock('加载失败: ' + error.message);
      }
    }

    function mountPersona(root) {
      state.roots.persona = root;
      root.classList.add('ob-memory-profile');
      root.innerHTML = '<header class="ob-panel-header"><div><h2>Persona State</h2>' +
        '<p>查看网关注入链路里的当前内在状态：人格慢变，关系中速变化，session 情绪按半衰期回落。</p></div>' +
        '<div class="ob-inline" data-role="session"></div></header>' +
        '<div data-role="content">' + loading('加载 Persona 状态…') + '</div>';
      root.addEventListener('click', function (event) {
        var button = event.target.closest('[data-action]');
        if (button && button.dataset.action === 'persona-refresh') loadPersona();
      });
      root.addEventListener('change', function (event) {
        var select = event.target.closest('[data-action="persona-session"]');
        if (select) loadPersona(select.value);
      });
    }

    function portraitScopeLabel(scope) {
      return {
        user: 'User Portrait',
        persona: '自我总入口 · 现在的我',
        relationship: 'Relationship Portrait',
      }[scope] || scope;
    }

    function portraitError(data) {
      var labels = {
        generator_unavailable: '生成模型未就绪；画像会复用脱水模型，请先配置脱水模型。',
        generator_error: '生成模型调用失败。',
        stable_locked: 'Stable 已锁定，请先解锁后再重新生成。',
        stable_not_generated: 'Stable 没有生成；当前依据不足，或模型输出被 evidence gate 拒绝。',
        stable_not_changed: '模型没有产出新的 Stable 版本。',
        daily_disabled: '每日画像维护已关闭。',
        portrait_disabled: 'Portrait 引擎已关闭。',
        missing_scope_decisions: '模型没有完成全部画像判断。',
      };
      var reason = String(data && data.reason || '');
      return labels[reason] || errorMessage(data, '生成失败');
    }

    function portraitScopeState(scope) {
      return state.portrait && state.portrait.portrait && state.portrait.portrait[scope] || {};
    }

    function addPortraitAction(spec) {
      var index = state.portraitActions.length;
      state.portraitActions.push(spec);
      return index;
    }

    function renderEvidence(evidence) {
      var valid = rows(evidence).filter(function (item) {
        return item && (item.bucket_id || item.moment_id || item.session_id);
      });
      if (!valid.length) return '';
      return '<div class="ob-evidence"><strong>evidence</strong>' + valid.slice(0, 6).map(function (item) {
        var parts = [];
        if (item.bucket_id) parts.push('#' + item.bucket_id);
        if (item.moment_id) parts.push(item.moment_id);
        if (item.session_id) parts.push('session ' + item.session_id);
        return '<span>' + escapeHtml(parts.join(' · ')) + '</span>';
      }).join('') + '</div>';
    }

    function portraitDate(item) {
      var dates = rows(item.source_dates).filter(Boolean);
      if (item.source_date) dates.unshift(item.source_date);
      if (item.time_label) return 'time ' + item.time_label;
      if (dates.length) return 'source ' + dates.slice(0, 3).join(', ');
      if (item.last_seen_date) return 'seen ' + item.last_seen_date;
      return item.updated_at || item.created_at || '';
    }

    function renderPortraitItem(item, spec, editable) {
      item = item || {};
      var text = item.text || item.summary || item.fact || item.reason || '';
      var meta = [];
      ['scope', 'status', 'profile_kind', 'predicate', 'object'].forEach(function (key) {
        if (item[key]) meta.push(item[key]);
      });
      if (item.confidence != null) meta.push('confidence ' + fixed(item.confidence, 2));
      if (item.count) meta.push('seen ' + item.count);
      var date = portraitDate(item);
      if (date) meta.push(date);
      var actionIndex = spec ? addPortraitAction(spec) : -1;
      return '<article class="ob-portrait-item"><p>' + escapeHtml(text) + '</p>' +
        (meta.length ? '<div class="ob-chip-row">' + meta.map(function (part) { return statusChip(part, ''); }).join('') + '</div>' : '') +
        renderEvidence(item.evidence) +
        (spec ? '<div class="ob-row-actions">' +
          (editable ? '<button type="button" data-action="portrait-edit-item" data-action-index="' + actionIndex + '">编辑</button>' : '') +
          '<button type="button" class="danger" data-action="portrait-delete-item" data-action-index="' + actionIndex + '">删除</button></div>' : '') +
      '</article>';
    }

    function renderPortraitItems(items, emptyText, specBuilder, editable) {
      var list = rows(items);
      if (!list.length) return empty(emptyText || '没有记录。');
      return list.map(function (item, index) {
        return renderPortraitItem(item, specBuilder ? specBuilder(item, index) : null, editable !== false);
      }).join('');
    }

    function renderStableEditor(scope, scopeState) {
      var history = rows(scopeState.stable_history).slice().reverse().slice(0, 8);
      return '<div class="ob-stable-editor">' +
        '<textarea data-role="stable-' + escapeAttribute(scope) + '" rows="5" placeholder="Stable 尚未生成。">' + escapeHtml(scopeState.stable || '') + '</textarea>' +
        '<div class="ob-row-actions">' +
          '<button type="button" data-action="portrait-save-stable" data-scope="' + escapeAttribute(scope) + '">保存</button>' +
          '<button type="button" data-action="portrait-lock-stable" data-scope="' + escapeAttribute(scope) + '" data-locked="' + (scopeState.stable_locked === true ? 'false' : 'true') + '">' +
            (scopeState.stable_locked === true ? '解锁自动更新' : '锁定') + '</button>' +
          (scopeState.stable ? '<button type="button" class="danger" data-action="portrait-clear-stable" data-scope="' + escapeAttribute(scope) + '">清空</button>' : '') +
        '</div>' +
        (history.length ? '<details><summary>历史版本 ' + history.length + '</summary>' + history.map(function (item) {
          var revision = Number(item.revision || 0);
          return '<article class="ob-history-row"><small>revision ' + revision + ' · ' + escapeHtml(item.source || 'unknown') +
            ' · ' + escapeHtml(item.updated_at || '') + '</small><p>' + escapeHtml(item.text || '') + '</p>' +
            '<button type="button" data-action="portrait-rollback-stable" data-scope="' + escapeAttribute(scope) + '" data-revision="' + revision + '">回退到这里</button></article>';
        }).join('') + '</details>' : '') +
      '</div>';
    }

    function renderGenerationEvidence(scope, scopeState) {
      var midTerm = scopeState.mid_term ? [{
        text: scopeState.mid_term,
        evidence: scopeState.mid_term_evidence || [],
        source_dates: scopeState.mid_term_source_dates || [],
        source_date: scopeState.mid_term_source_date || '',
        updated_at: scopeState.mid_term_updated_at || '',
      }] : [];
      var midHtml = renderPortraitItems(midTerm, '还没有 current delta。', function (item) {
        return { area: 'portrait', scope: scope, layer: 'mid_term', text: item.text || '' };
      });
      var stagingHtml = renderPortraitItems(rows(scopeState.staging_pool).slice(0, 12), 'staging pool 为空。', function (item, index) {
        return { area: 'portrait', scope: scope, layer: 'staging_pool', index: index, text: item.text || '' };
      });
      var recentHtml = renderPortraitItems(rows(scopeState.recent_buffer).slice(0, 12), 'recent buffer 为空。', function (item, index) {
        return { area: 'portrait', scope: scope, layer: 'recent_buffer', index: index, text: item.text || '' };
      });
      return '<details class="ob-evidence-details"><summary>生成依据</summary>' +
        '<h4>current delta</h4>' + midHtml +
        '<h4>staging pool</h4>' + stagingHtml +
        '<h4>recent buffer</h4>' + recentHtml +
      '</details>';
    }

    function renderPortraitScope(scope) {
      var scopeState = portraitScopeState(scope);
      var generation = state.portrait && state.portrait.generation_status && state.portrait.generation_status[scope] || {};
      var blockers = rows(generation.blockers);
      return '<section class="ob-card ob-portrait-scope">' +
        '<header><div><h3>' + escapeHtml(portraitScopeLabel(scope)) + '</h3><div class="ob-chip-row">' +
          statusChip('revision ' + Number(scopeState.stable_revision || 0), '') +
          statusChip(scopeState.stable_locked === true ? 'locked' : 'auto update', scopeState.stable_locked === true ? 'muted' : 'active') +
          (scopeState.stable_source ? statusChip(scopeState.stable_source, '') : '') +
          (scope === 'user' ? statusChip('新增依据 ' + Number(generation.added_since_stable || 0) + '/' + Number(generation.rewrite_threshold || 10), '') : '') +
          blockers.map(function (item) { return statusChip(item, 'muted'); }).join('') +
        '</div></div><button type="button" data-action="portrait-maintain" data-scope="' + escapeAttribute(scope) + '">重新生成</button></header>' +
        '<div class="ob-stable-preview' + (scopeState.stable ? '' : ' empty') + '">' + escapeHtml(scopeState.stable || 'Stable 尚未生成。') + '</div>' +
        '<details><summary>编辑、锁定与历史</summary>' + renderStableEditor(scope, scopeState) + renderGenerationEvidence(scope, scopeState) + '</details>' +
      '</section>';
    }

    function renderPortraitState(data) {
      var root = rootFor('portrait');
      if (!root) return;
      state.portrait = data || {};
      state.portraitActions = [];
      var selfEntry = state.portrait.self_anchor_entry || {};
      var personaState = portraitScopeState('persona');
      var health = state.portrait.evidence_health || {};
      role(root, 'summary').innerHTML = '<div class="ob-chip-row">' +
        statusChip('auto maintained', 'active') +
        statusChip('engine ' + (state.portrait.enabled === false ? 'off' : 'on'), state.portrait.enabled === false ? 'muted' : 'active') +
        statusChip('auto ' + (state.portrait.auto_enabled === false ? 'off' : 'on'), state.portrait.auto_enabled === false ? 'muted' : 'active') +
        statusChip('daily ' + (state.portrait.daily_enabled === false ? 'off' : 'on'), state.portrait.daily_enabled === false ? 'muted' : 'active') +
        statusChip('generator ' + (state.portrait.generator_ready === true ? 'on' : 'off'), state.portrait.generator_ready === true ? 'active' : 'muted') +
        statusChip('model ' + (state.portrait.generator_model || '—'), '') +
        statusChip('last run ' + (state.portrait.last_run_date || '—'), '') +
        statusChip('updated ' + (state.portrait.updated_at || '—'), '') +
        statusChip('state ' + (state.portrait.state_path || '—'), '') +
        (Number(health.removed_evidence || 0) ? statusChip('已清理悬空依据 ' + Number(health.removed_evidence), 'muted') : '') +
      '</div>';

      var focusItems = rows(state.portrait.current_focus_items);
      var timeline = rows(state.portrait.recent_timeline);
      var timelineArea = 'recent_timeline';
      if (!timeline.length) {
        timeline = rows(state.portrait.recent_activities);
        timelineArea = 'recent_activities';
      }
      var stableCandidates = rows(state.portrait.stable_candidates).slice(-8).reverse();
      var factCandidates = rows(state.portrait.profile_fact_candidates).slice(-8).reverse();
      role(root, 'content').innerHTML =
        '<section class="ob-card ob-self-anchor"><header><h3>自我总入口</h3></header>' +
          '<p>' + escapeHtml(selfEntry.text || selfEntry.name || selfEntry.bucket_id || '还没有自我总入口。') + '</p>' +
          renderEvidence(selfEntry.bucket_id ? [{ bucket_id: selfEntry.bucket_id }] : []) +
          '<h4>现在的我 · 自动生长</h4><div class="ob-stable-preview' + (personaState.stable ? '' : ' empty') + '">' +
            escapeHtml(personaState.stable || '还没有长出新的自我理解。') + '</div>' +
        '</section>' +
        '<section class="ob-card"><header><h3>Current Focus · handoff 实际注入</h3><button type="button" data-action="portrait-show-add">添加</button></header>' +
          '<form class="ob-inline-form" data-role="recent-form" hidden><input data-role="recent-text" maxlength="2000" placeholder="最近事项" />' +
            '<button type="submit" data-action="portrait-add-recent">保存</button><button type="button" data-action="portrait-hide-add">取消</button></form>' +
          renderPortraitItems(focusItems, '最近 7 天没有 current focus。', function (item, index) {
            // current_focus_items is a date-filtered, sorted view of recent_activities.
            // The mutation API intentionally owns recent_activities and falls back to
            // expected text when the visible view index differs from its stored index.
            return { area: 'recent_activities', layer: 'recent_activities', index: index, text: item.text || '' };
          }) +
        '</section>' +
        '<form class="ob-card ob-inline-form" data-role="item-editor" hidden><h3>编辑生成依据</h3><textarea data-role="item-editor-text" rows="4"></textarea>' +
          '<button type="submit" data-action="portrait-save-item">保存</button><button type="button" data-action="portrait-cancel-item">取消</button></form>' +
        '<div class="ob-portrait-grid">' + ['user', 'persona', 'relationship'].map(renderPortraitScope).join('') + '</div>' +
        '<details class="ob-card"><summary>生成记录与候选</summary><div class="ob-portrait-grid">' +
          '<section><h3>Recent Timeline</h3>' + renderPortraitItems(timeline.slice(0, 8), '还没有 recent timeline。', function (item, index) {
            return { area: timelineArea, index: index, text: item.text || '' };
          }, false) + '</section>' +
          '<section><h3>Stable Candidates</h3>' + renderPortraitItems(stableCandidates, '没有候选。', function (item, index) {
            return { area: 'stable_candidates', layer: 'stable_candidates', index: index, text: item.text || '' };
          }) + '</section>' +
          '<section><h3>Profile Fact Candidates</h3>' + renderPortraitItems(factCandidates, '没有候选。', function (item, index) {
            return { area: 'profile_fact_candidates', layer: 'profile_fact_candidates', index: index, text: item.text || '' };
          }) + '</section>' +
        '</div></details>';
    }

    async function loadPortrait() {
      var root = rootFor('portrait');
      if (!root) return;
      var request = beginRead('portrait');
      if (!request) return;
      role(root, 'summary').textContent = '读取中…';
      role(root, 'content').innerHTML = loading('加载 Portrait State…');
      try {
        var data = await apiJson('get', '/api/portrait-state', {
          signal: request.signal,
          timeoutMs: READ_TIMEOUT_MS,
        });
        if (!isCurrentRead('portrait', request)) return;
        renderPortraitState(data);
      } catch (error) {
        if (!isCurrentRead('portrait', request)) return;
        role(root, 'summary').textContent = '读取失败';
        role(root, 'content').innerHTML = errorBlock('加载失败: ' + error.message);
      }
    }

    async function maintainPortrait(scope) {
      var root = rootFor('portrait');
      setStatus(root, 'status', '生成中…', 'loading');
      try {
        var data = await apiJson('post', '/api/portrait-maintain', { body: { force: true, scope: scope || '' } });
        var targets = scope ? [scope] : ['user', 'persona', 'relationship'];
        var changed = targets.filter(function (target) {
          return data.generation && data.generation[target] && data.generation[target].changed === true;
        });
        var unchanged = targets.filter(function (target) {
          return data.generation && data.generation[target] && data.generation[target].changed !== true;
        });
        if (unchanged.length && (scope || !changed.length)) {
          throw new Error(portraitError({ reason: unchanged.some(function (target) {
            return data.generation[target].stable_present !== true;
          }) ? 'stable_not_generated' : 'stable_not_changed' }));
        }
        var materials = data.materials || {};
        var prefix = unchanged.length
          ? '部分更新（未变化：' + unchanged.map(portraitScopeLabel).join('、') + '）：'
          : (scope ? portraitScopeLabel(scope) + ' ' : '') + '已生成：';
        setStatus(root, 'status', prefix +
          [data.status || 'ok', data.date || '', 'buckets ' + (materials.buckets == null ? '—' : materials.buckets),
            'persona events ' + (materials.persona_events == null ? '—' : materials.persona_events),
            'rejected ' + rows(data.rejected).length].filter(Boolean).join(' · '), 'success');
        await loadPortrait();
      } catch (error) {
        setStatus(root, 'status', '生成失败: ' + error.message, 'error');
        await loadPortrait();
      }
    }

    async function addRecentActivity(root) {
      var input = role(root, 'recent-text');
      var text = String(input && input.value || '').trim();
      if (!text) {
        setStatus(root, 'status', '最近事项不能为空。', 'error');
        return;
      }
      try {
        await apiJson('post', '/api/portrait-state/items', { body: { area: 'recent_activities', text: text } });
        setStatus(root, 'status', '最近事项已添加。', 'success');
        await loadPortrait();
      } catch (error) {
        setStatus(root, 'status', '添加失败: ' + error.message, 'error');
      }
    }

    async function savePortraitItem(root) {
      if (!state.portraitEdit) return;
      var input = role(root, 'item-editor-text');
      var text = String(input && input.value || '').trim();
      if (!text) {
        setStatus(root, 'status', '生成依据不能为空。', 'error');
        return;
      }
      var body = Object.assign({}, state.portraitEdit, { expected_text: state.portraitEdit.text || '', text: text });
      try {
        await apiJson('put', '/api/portrait-state/items', { body: body });
        state.portraitEdit = null;
        setStatus(root, 'status', '生成依据已更新。', 'success');
        await loadPortrait();
      } catch (error) {
        setStatus(root, 'status', '编辑失败: ' + error.message, 'error');
      }
    }

    async function deletePortraitItem(root, spec) {
      if (!await confirmAction('删除这条画像记录？此操作只针对当前选中的记录。')) return;
      try {
        await apiJson('delete', '/api/portrait-state/items', { body: Object.assign({}, spec, { confirm: 'DELETE' }) });
        setStatus(root, 'status', '已删除画像记录。', 'success');
        await loadPortrait();
      } catch (error) {
        setStatus(root, 'status', '删除失败: ' + error.message, 'error');
      }
    }

    async function saveStable(root, scope) {
      var scopeState = portraitScopeState(scope);
      var input = role(root, 'stable-' + scope);
      var text = String(input && input.value || '').trim();
      if (!text) {
        setStatus(root, 'status', 'stable 不能为空；需要清空时请使用清空按钮。', 'error');
        return;
      }
      try {
        await apiJson('put', '/api/portrait-state/stable', { body: {
          scope: scope,
          text: text,
          expected_revision: Number(scopeState.stable_revision || 0),
        } });
        setStatus(root, 'status', 'stable 已保存。', 'success');
        await loadPortrait();
      } catch (error) {
        setStatus(root, 'status', '保存失败: ' + error.message, 'error');
      }
    }

    async function setStableLock(root, scope, locked) {
      var scopeState = portraitScopeState(scope);
      try {
        await apiJson('post', '/api/portrait-state/stable/lock', { body: {
          scope: scope,
          locked: locked,
          expected_revision: Number(scopeState.stable_revision || 0),
        } });
        setStatus(root, 'status', locked ? 'stable 已锁定。' : 'stable 已恢复后台自动更新。', 'success');
        await loadPortrait();
      } catch (error) {
        setStatus(root, 'status', '锁定状态更新失败: ' + error.message, 'error');
      }
    }

    async function clearStable(root, scope) {
      var scopeState = portraitScopeState(scope);
      if (!scopeState.stable || !await confirmAction('清空这段 stable portrait？当前文本会保留在历史版本里。')) return;
      try {
        await apiJson('delete', '/api/portrait-state/items', { body: {
          confirm: 'DELETE', area: 'portrait', scope: scope, layer: 'stable', text: scopeState.stable,
        } });
        setStatus(root, 'status', 'stable 已清空，旧文本仍在历史版本里。', 'success');
        await loadPortrait();
      } catch (error) {
        setStatus(root, 'status', '清空失败: ' + error.message, 'error');
      }
    }

    async function rollbackStable(root, scope, targetRevision) {
      if (!await confirmAction('回退 stable 到 revision ' + targetRevision + '？当前版本仍会保留在历史里。')) return;
      var scopeState = portraitScopeState(scope);
      try {
        await apiJson('post', '/api/portrait-state/stable/rollback', { body: {
          scope: scope,
          target_revision: Number(targetRevision),
          expected_revision: Number(scopeState.stable_revision || 0),
        } });
        setStatus(root, 'status', 'stable 已回退，并保留当前版本。', 'success');
        await loadPortrait();
      } catch (error) {
        setStatus(root, 'status', '回退失败: ' + error.message, 'error');
      }
    }

    async function resetPortrait(root) {
      var input = role(root, 'reset-input');
      if (String(input && input.value || '').trim() !== 'RESET') {
        setStatus(root, 'status', '请输入 RESET 才能清空 Portrait State。', 'error');
        return;
      }
      if (!await confirmAction('最后确认：清空整个 Portrait State？下一次生成将按第一次生成运行。')) return;
      try {
        await apiJson('post', '/api/portrait-state/reset', { body: { confirm: 'RESET' } });
        setStatus(root, 'status', '已清空画像；下一次手动生成会按第一次生成。', 'success');
        await loadPortrait();
      } catch (error) {
        setStatus(root, 'status', '清空失败: ' + error.message, 'error');
      }
    }

    function mountPortrait(root) {
      state.roots.portrait = root;
      root.classList.add('ob-memory-profile');
      root.innerHTML = '<header class="ob-panel-header"><div><h2>Portrait State</h2>' +
        '<p>后台每天维护的换窗画像；只在 breath/handoff 开场恢复，不随普通对话逐轮注入。</p></div>' +
        '<div class="ob-row-actions"><button type="button" data-action="portrait-refresh">刷新</button>' +
          '<button type="button" data-action="portrait-maintain">手动生成</button>' +
          '<button type="button" class="danger" data-action="portrait-show-reset">清空画像</button></div></header>' +
        '<div class="ob-reset-confirm" data-role="reset-form" hidden><label>输入 RESET 确认清空<input data-role="reset-input" autocomplete="off" /></label>' +
          '<button type="button" class="danger" data-action="portrait-reset">确认清空</button>' +
          '<button type="button" data-action="portrait-hide-reset">取消</button></div>' +
        '<div class="ob-status" data-role="status" aria-live="polite"></div>' +
        '<div data-role="summary">加载中…</div><div data-role="content">' + loading('加载 Portrait State…') + '</div>';
      root.addEventListener('submit', function (event) {
        event.preventDefault();
        var action = event.submitter && event.submitter.dataset.action;
        if (action === 'portrait-add-recent') addRecentActivity(root);
        if (action === 'portrait-save-item') savePortraitItem(root);
      });
      root.addEventListener('click', function (event) {
        var button = event.target.closest('[data-action]');
        if (!button) return;
        var action = button.dataset.action;
        var scope = button.dataset.scope || '';
        if (action === 'portrait-refresh') loadPortrait();
        if (action === 'portrait-maintain') maintainPortrait(scope);
        if (action === 'portrait-show-add') role(root, 'recent-form').hidden = false;
        if (action === 'portrait-hide-add') role(root, 'recent-form').hidden = true;
        if (action === 'portrait-show-reset') role(root, 'reset-form').hidden = false;
        if (action === 'portrait-hide-reset') role(root, 'reset-form').hidden = true;
        if (action === 'portrait-reset') resetPortrait(root);
        if (action === 'portrait-save-stable') saveStable(root, scope);
        if (action === 'portrait-lock-stable') setStableLock(root, scope, button.dataset.locked === 'true');
        if (action === 'portrait-clear-stable') clearStable(root, scope);
        if (action === 'portrait-rollback-stable') rollbackStable(root, scope, Number(button.dataset.revision || 0));
        if (action === 'portrait-cancel-item') {
          state.portraitEdit = null;
          role(root, 'item-editor').hidden = true;
        }
        if (action === 'portrait-edit-item' || action === 'portrait-delete-item') {
          var spec = state.portraitActions[Number(button.dataset.actionIndex)];
          if (!spec) return;
          if (action === 'portrait-delete-item') deletePortraitItem(root, spec);
          else {
            state.portraitEdit = Object.assign({}, spec);
            role(root, 'item-editor-text').value = spec.text || '';
            role(root, 'item-editor').hidden = false;
            role(root, 'item-editor-text').focus();
          }
        }
      });
    }

    function profileStatus(fact) {
      if (fact.deprecated || fact.state === 'deprecated') return { label: 'deprecated', tone: 'deprecated' };
      if (fact.active || fact.state === 'active') return { label: 'active', tone: 'active' };
      return { label: fact.state || 'inactive', tone: 'muted' };
    }

    function renderProfileEvidence(evidence) {
      if (!rows(evidence).length) return '<div class="ob-evidence">未记录证据。</div>';
      return '<div class="ob-evidence"><strong>evidence</strong>' + rows(evidence).map(function (item) {
        var label = (item.name || item.bucket_id || '') + (item.moment_id ? ' · ' + item.moment_id : '');
        return '<span>' + escapeHtml(label) + '</span>';
      }).join('') + '</div>';
    }

    function renderProfileFacts() {
      var root = rootFor('facts');
      if (!root) return;
      var facts = state.profileFacts;
      var activeCount = facts.filter(function (fact) { return fact.active; }).length;
      var deprecatedCount = facts.filter(function (fact) { return fact.deprecated; }).length;
      role(root, 'summary').textContent = facts.length + ' 条画像事实 · ' + activeCount + ' active · ' + deprecatedCount + ' deprecated';
      if (!facts.length) {
        role(root, 'list').innerHTML = empty('还没有画像事实。');
        return;
      }
      role(root, 'list').innerHTML = facts.map(function (fact) {
        var id = String(fact.id || '');
        var status = profileStatus(fact);
        var sections = fact.sections || {};
        return '<article class="ob-card ob-profile-card ' + escapeAttribute(status.tone) + '" data-fact-id="' + escapeAttribute(id) + '">' +
          '<header><div><h3>' + escapeHtml(fact.fact || '') + '</h3><div class="ob-chip-row">' +
            statusChip(status.label, status.tone) + statusChip(fact.kind || 'unknown', '') +
            statusChip('confidence ' + (fact.confidence == null ? '—' : fixed(fact.confidence, 2)), '') +
            statusChip(fact.source || 'profile_fact', '') + '</div></div><div class="ob-row-actions">' +
            (status.tone === 'active' ? '' : '<button type="button" data-action="profile-confirm" data-id="' + escapeAttribute(id) + '">确认</button>') +
            '<button type="button" data-action="profile-edit-toggle" data-id="' + escapeAttribute(id) + '">编辑</button>' +
            '<button type="button" class="danger" data-action="profile-deprecate" data-id="' + escapeAttribute(id) + '">废弃</button>' +
            '<button type="button" class="danger" data-action="profile-delete" data-id="' + escapeAttribute(id) + '">删除</button></div></header>' +
          '<dl class="ob-profile-grid"><div><dt>subject</dt><dd>' + escapeHtml(fact.subject || '—') + '</dd></div>' +
            '<div><dt>predicate</dt><dd>' + escapeHtml(fact.predicate || '—') + '</dd></div>' +
            '<div><dt>object</dt><dd>' + escapeHtml(fact.object || '—') + '</dd></div>' +
            '<div><dt>updated</dt><dd>' + escapeHtml(fact.updated_at || fact.last_active || fact.created || '—') + '</dd></div>' +
            '<div><dt>bucket</dt><dd>' + escapeHtml(id) + '</dd></div>' +
            '<div><dt>tags</dt><dd>' + escapeHtml(rows(fact.tags).join(', ') || '—') + '</dd></div></dl>' +
          renderProfileEvidence(fact.evidence) +
          '<form class="ob-profile-edit" data-role="profile-edit" data-id="' + escapeAttribute(id) + '" hidden>' +
            '<label>Fact<textarea name="fact" rows="3" required>' + escapeHtml(fact.fact || '') + '</textarea></label>' +
            '<div class="ob-form-grid"><label>Kind<input name="profile_kind" value="' + escapeAttribute(fact.kind || 'preference') + '" /></label>' +
              '<label>Subject<input name="subject" value="' + escapeAttribute(fact.subject || 'user') + '" /></label>' +
              '<label>Predicate<input name="predicate" value="' + escapeAttribute(fact.predicate || '') + '" /></label>' +
              '<label>Object<input name="object" value="' + escapeAttribute(fact.object || '') + '" /></label>' +
              '<label>Confidence<input name="confidence" type="number" min="0" max="1" step="0.01" value="' + escapeAttribute(fact.confidence == null ? '0.9' : fact.confidence) + '" /></label></div>' +
            '<label>Evidence context<textarea name="evidence_context" rows="2">' + escapeHtml(sections.evidence_context || '') + '</textarea></label>' +
            '<label>Reflection<textarea name="reflection" rows="2">' + escapeHtml(sections.reflection || '') + '</textarea></label>' +
            '<label>Followup<textarea name="followup" rows="2">' + escapeHtml(sections.followup || '') + '</textarea></label>' +
            '<div class="ob-row-actions"><button type="submit" data-action="profile-save">保存</button>' +
              '<button type="button" data-action="profile-edit-toggle" data-id="' + escapeAttribute(id) + '">取消</button></div>' +
          '</form>' +
        '</article>';
      }).join('');
    }

    async function loadProfileFacts() {
      var root = rootFor('facts');
      if (!root) return;
      var request = beginRead('facts');
      if (!request) return;
      role(root, 'summary').textContent = '读取中…';
      role(root, 'list').innerHTML = loading('加载画像事实…');
      try {
        var data = await apiJson('get', '/api/profile-facts', {
          signal: request.signal,
          timeoutMs: READ_TIMEOUT_MS,
        });
        if (!isCurrentRead('facts', request)) return;
        state.profileFacts = rows(data.facts);
        renderProfileFacts();
      } catch (error) {
        if (!isCurrentRead('facts', request)) return;
        role(root, 'summary').textContent = '读取失败';
        role(root, 'list').innerHTML = errorBlock('加载失败: ' + error.message);
      }
    }

    function factById(id) {
      return state.profileFacts.find(function (fact) { return String(fact.id || '') === String(id || ''); });
    }

    async function mutateProfileFact(root, id, body, success) {
      try {
        await apiJson('patch', '/api/profile-facts/' + encodeURIComponent(id), { body: body });
        setStatus(root, 'status', success, 'success');
        await loadProfileFacts();
      } catch (error) {
        setStatus(root, 'status', '画像事实操作失败: ' + error.message, 'error');
      }
    }

    async function deleteProfileFact(root, id) {
      var fact = factById(id);
      if (!fact || !await confirmAction('彻底删除这条画像事实？\n\n' + (fact.fact || id))) return;
      try {
        await apiJson('delete', '/api/profile-facts/' + encodeURIComponent(id), { body: { confirm: 'DELETE' } });
        setStatus(root, 'status', '画像事实已删除。', 'success');
        await loadProfileFacts();
      } catch (error) {
        setStatus(root, 'status', '画像事实删除失败: ' + error.message, 'error');
      }
    }

    async function saveProfileFact(root, form) {
      var id = String(form.dataset.id || '');
      var value = function (name) {
        var field = form.elements.namedItem(name);
        return String(field && field.value || '').trim();
      };
      var fact = value('fact');
      if (!fact) {
        setStatus(root, 'status', 'Fact 不能为空。', 'error');
        return;
      }
      await mutateProfileFact(root, id, {
        action: 'edit',
        fact: fact,
        profile_kind: value('profile_kind'),
        subject: value('subject'),
        predicate: value('predicate'),
        object: value('object'),
        confidence: clamp(value('confidence'), 0, 1),
        evidence_context: value('evidence_context'),
        reflection: value('reflection'),
        followup: value('followup'),
      }, '画像事实已更新。');
    }

    function mountProfileFacts(root) {
      state.roots.facts = root;
      root.classList.add('ob-memory-profile');
      root.innerHTML = '<header class="ob-panel-header"><div><h2>Profile Facts</h2>' +
        '<p>画像事实卡保留作证据检查；不会把原文直接拼进每轮上下文。</p></div>' +
        '<button type="button" data-action="profile-refresh">刷新</button></header>' +
        '<div class="ob-status" data-role="status" aria-live="polite"></div>' +
        '<div data-role="summary">加载中…</div><div data-role="list">' + loading('加载画像事实…') + '</div>';
      root.addEventListener('submit', function (event) {
        var form = event.target.closest('[data-role="profile-edit"]');
        if (!form) return;
        event.preventDefault();
        saveProfileFact(root, form);
      });
      root.addEventListener('click', async function (event) {
        var button = event.target.closest('[data-action]');
        if (!button) return;
        var action = button.dataset.action;
        var id = button.dataset.id || '';
        if (action === 'profile-refresh') loadProfileFacts();
        if (action === 'profile-edit-toggle') {
          var card = button.closest('[data-fact-id]');
          var form = card && role(card, 'profile-edit');
          if (form) form.hidden = !form.hidden;
        }
        if (action === 'profile-confirm') mutateProfileFact(root, id, { action: 'confirm' }, '画像事实已确认。');
        if (action === 'profile-deprecate' && await confirmAction('废弃这条画像事实？它会保留作为历史证据。')) {
          mutateProfileFact(root, id, { action: 'deprecate' }, '画像事实已废弃。');
        }
        if (action === 'profile-delete') deleteProfileFact(root, id);
      });
    }

    function renderProfileProposals(data) {
      var root = rootFor('profileProposals');
      if (!root) return;
      var proposals = state.profileProposals;
      var rejected = rows(data && data.rejected);
      if (!proposals.length) {
        setStatus(root, 'status', rejected.length ? '没有可用候选，已拒绝 ' + rejected.length + ' 条。' : '没有生成候选。', rejected.length ? 'error' : '');
        role(root, 'list').innerHTML = empty('没有画像候选。');
        return;
      }
      setStatus(root, 'status', '生成 ' + proposals.length + ' 条候选。', 'success');
      role(root, 'list').innerHTML = proposals.map(function (item, index) {
        return '<article class="ob-card"><h3>' + escapeHtml(item.fact || '') + '</h3>' +
          '<div class="ob-chip-row">' + statusChip(item.profile_kind || 'other', '') + statusChip(item.subject || 'user', '') +
            statusChip(item.predicate || 'related_to', '') + statusChip('confidence ' + fixed(item.confidence, 2), '') + '</div>' +
          '<dl class="ob-profile-grid"><div><dt>object</dt><dd>' + escapeHtml(item.object || '—') + '</dd></div>' +
            '<div><dt>evidence</dt><dd>' + escapeHtml(item.evidence_bucket_id || '') +
              (item.evidence_moment_id ? ' · ' + escapeHtml(item.evidence_moment_id) : '') + '</dd></div>' +
            '<div><dt>reason</dt><dd>' + escapeHtml(item.reason || '—') + '</dd></div></dl>' +
          '<button type="button" data-action="profile-proposal-confirm" data-index="' + index + '">确认写入</button></article>';
      }).join('');
    }

    async function generateProfileProposals(root) {
      var bucketId = String(role(root, 'bucket-id').value || '').trim();
      var momentId = String(role(root, 'moment-id').value || '').trim();
      if (!bucketId) {
        setStatus(root, 'status', '先填证据 bucket id。', 'error');
        return;
      }
      setStatus(root, 'status', '生成中…', 'loading');
      role(root, 'list').innerHTML = loading('生成画像候选…');
      try {
        var data = await apiJson('post', '/api/profile-fact-proposals', { body: {
          bucket_id: bucketId,
          evidence_moment_id: momentId,
          max_proposals: 3,
        } });
        state.profileProposals = rows(data.proposals);
        renderProfileProposals(data);
      } catch (error) {
        state.profileProposals = [];
        setStatus(root, 'status', '生成失败: ' + error.message, 'error');
        role(root, 'list').innerHTML = errorBlock(error.message);
      }
    }

    async function confirmProfileProposal(root, index, button) {
      var proposal = state.profileProposals[index];
      if (!proposal || state.profileProposalWrites.has(proposal)) return;
      state.profileProposalWrites.add(proposal);
      if (button) button.disabled = true;
      try {
        if (!await confirmAction('确认写入画像事实？\n\n' + (proposal.fact || ''))) return;
        if (state.profileProposals.indexOf(proposal) < 0) return;
        setStatus(root, 'status', '写入中…', 'loading');
        var data = await apiJson('post', '/api/profile-fact-proposals/confirm', { body: proposal });
        var currentIndex = state.profileProposals.indexOf(proposal);
        if (currentIndex < 0) return;
        state.profileProposals.splice(currentIndex, 1);
        renderProfileProposals({ proposals: state.profileProposals, rejected: [] });
        setStatus(root, 'status', '已写入画像事实 ' + (data.id || ''), 'success');
        await loadProfileFacts();
      } catch (error) {
        setStatus(root, 'status', '写入失败: ' + error.message, 'error');
      } finally {
        state.profileProposalWrites.delete(proposal);
        if (button) button.disabled = false;
      }
    }

    function mountProfileProposals(root) {
      state.roots.profileProposals = root;
      root.classList.add('ob-memory-profile');
      root.innerHTML = '<header class="ob-panel-header"><div><h2>Profile-fact Proposals</h2>' +
        '<p>从指定记忆证据生成画像事实候选；候选必须再次确认才会写入。</p></div></header>' +
        '<form class="ob-form-grid" data-role="form"><label>证据 bucket id<input data-role="bucket-id" required /></label>' +
          '<label>Moment id（可选）<input data-role="moment-id" /></label><button type="submit">生成画像候选</button></form>' +
        '<div class="ob-status" data-role="status" aria-live="polite"></div><div data-role="list">' + empty('尚未生成候选。') + '</div>';
      role(root, 'form').addEventListener('submit', function (event) {
        event.preventDefault();
        generateProfileProposals(root);
      });
      root.addEventListener('click', function (event) {
        var button = event.target.closest('[data-action="profile-proposal-confirm"]');
        if (button) confirmProfileProposal(root, Number(button.dataset.index), button);
      });
    }

    function renderAnchorProposals(data) {
      var root = rootFor('anchorProposals');
      if (!root) return;
      var proposals = state.anchorProposals;
      var rejected = rows(data && data.rejected);
      if (data && data.bucket) state.anchorProposalBucket = data.bucket;
      var bucket = state.anchorProposalBucket || {};
      if (!proposals.length) {
        var reason = rejected.length ? rejected[0].reason || '已拒绝' : '';
        setStatus(root, 'status', rejected.length ? '没有可用候选：' + reason : '没有生成候选。', rejected.length ? 'error' : '');
        role(root, 'list').innerHTML = empty('没有 Anchor 候选。');
        return;
      }
      setStatus(root, 'status', '生成 ' + proposals.length + ' 条 Anchor 候选。', 'success');
      role(root, 'list').innerHTML = proposals.map(function (item, index) {
        return '<article class="ob-card"><h3>' + escapeHtml(bucket.name || item.bucket_id || '') + '</h3>' +
          '<div class="ob-chip-row">' + statusChip(item.anchor_kind || 'other', '') + statusChip('confidence ' + fixed(item.confidence, 2), '') + '</div>' +
          '<dl class="ob-profile-grid"><div><dt>bucket</dt><dd>' + escapeHtml(item.bucket_id || '') + '</dd></div>' +
            '<div><dt>reason</dt><dd>' + escapeHtml(item.reason || '—') + '</dd></div>' +
            '<div><dt>future</dt><dd>' + escapeHtml(item.future_use || '—') + '</dd></div></dl>' +
          '<button type="button" data-action="anchor-proposal-confirm" data-index="' + index + '">确认标为 Anchor</button></article>';
      }).join('');
    }

    async function generateAnchorProposals(root) {
      var bucketId = String(role(root, 'bucket-id').value || '').trim();
      if (!bucketId) {
        setStatus(root, 'status', '先填 bucket id。', 'error');
        return;
      }
      setStatus(root, 'status', '生成中…', 'loading');
      role(root, 'list').innerHTML = loading('生成 Anchor 候选…');
      try {
        var data = await apiJson('post', '/api/anchor-proposals', { body: { bucket_id: bucketId } });
        state.anchorProposals = rows(data.proposals);
        state.anchorProposalBucket = data.bucket || {};
        renderAnchorProposals(data);
      } catch (error) {
        state.anchorProposals = [];
        setStatus(root, 'status', '生成失败: ' + error.message, 'error');
        role(root, 'list').innerHTML = errorBlock(error.message);
      }
    }

    async function confirmAnchorProposal(root, index, button) {
      var proposal = state.anchorProposals[index];
      if (!proposal || state.anchorProposalWrites.has(proposal)) return;
      state.anchorProposalWrites.add(proposal);
      if (button) button.disabled = true;
      try {
        if (!await confirmAction('确认标为 Anchor？\n\n' + (proposal.bucket_id || ''))) return;
        if (state.anchorProposals.indexOf(proposal) < 0) return;
        setStatus(root, 'status', '写入中…', 'loading');
        var data = await apiJson('post', '/api/anchor-proposals/confirm', { body: proposal });
        var currentIndex = state.anchorProposals.indexOf(proposal);
        if (currentIndex < 0) return;
        state.anchorProposals.splice(currentIndex, 1);
        renderAnchorProposals({ proposals: state.anchorProposals, rejected: [] });
        setStatus(root, 'status', data.status === 'already_anchor' ? '已经是 Anchor。' : '已标为 Anchor ' + (data.id || ''), 'success');
      } catch (error) {
        setStatus(root, 'status', '写入失败: ' + error.message, 'error');
      } finally {
        state.anchorProposalWrites.delete(proposal);
        if (button) button.disabled = false;
      }
    }

    function mountAnchorProposals(root) {
      state.roots.anchorProposals = root;
      root.classList.add('ob-memory-profile');
      root.innerHTML = '<header class="ob-panel-header"><div><h2>Anchor Proposals</h2>' +
        '<p>评估一条已有记忆是否值得成为稀缺 Anchor；确认前不会更改坐标系。</p></div></header>' +
        '<form class="ob-form-grid" data-role="form"><label>候选 bucket id<input data-role="bucket-id" required /></label>' +
          '<button type="submit">生成 Anchor 候选</button></form>' +
        '<div class="ob-status" data-role="status" aria-live="polite"></div><div data-role="list">' + empty('尚未生成候选。') + '</div>';
      role(root, 'form').addEventListener('submit', function (event) {
        event.preventDefault();
        generateAnchorProposals(root);
      });
      root.addEventListener('click', function (event) {
        var button = event.target.closest('[data-action="anchor-proposal-confirm"]');
        if (button) confirmAnchorProposal(root, Number(button.dataset.index), button);
      });
    }

    app.registerPanel({
      id: 'memory-persona-state',
      workspace: 'memory',
      label: 'Persona',
      order: 50,
      mount: function (root) { mountPersona(root); },
      activate: function (context) {
        activateReads('persona', context);
        return loadPersona();
      },
      deactivate: function () { deactivateReads('persona'); },
    });
    app.registerPanel({
      id: 'memory-portrait',
      workspace: 'memory',
      label: 'Portrait',
      order: 60,
      mount: function (root) { mountPortrait(root); },
      activate: function (context) {
        activateReads('portrait', context);
        return loadPortrait();
      },
      deactivate: function () { deactivateReads('portrait'); },
    });
    app.registerPanel({
      id: 'memory-profile-facts',
      workspace: 'memory',
      label: 'Profile Facts',
      order: 70,
      mount: function (root) { mountProfileFacts(root); },
      activate: function (context) {
        activateReads('facts', context);
        return loadProfileFacts();
      },
      deactivate: function () { deactivateReads('facts'); },
    });
    app.registerPanel({
      id: 'memory-profile-proposals',
      workspace: 'memory',
      label: 'Profile Proposals',
      order: 71,
      mount: function (root) { mountProfileProposals(root); },
      activate: function () { return Promise.resolve(); },
    });
    app.registerPanel({
      id: 'memory-anchor-proposals',
      workspace: 'memory',
      label: 'Anchor Proposals',
      order: 72,
      mount: function (root) { mountAnchorProposals(root); },
      activate: function () { return Promise.resolve(); },
    });
  });
})();
