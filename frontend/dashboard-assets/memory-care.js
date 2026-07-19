(function () {
  'use strict';

  var factories = window.OmbreDashboardFeatureFactories =
    window.OmbreDashboardFeatureFactories || [];

  factories.push(function memoryCareFeatureFactory(app) {
    if (!app || typeof app.registerPanel !== 'function') {
      throw new Error('The unified dashboard panel registry is unavailable.');
    }

    ensureStyles(app);
    var api = createApiClient(app);
    var ui = createUiHelpers(app);

    [
      createRemindersPanel(app, api, ui),
      createReflectionPanel(app, api, ui),
      createChatMemoryPanel(app, api, ui),
      createDreamsPanel(app, api, ui),
      createDarkroomPanel(app, api, ui),
    ].forEach(function (panel) {
      app.registerPanel(panel);
    });
  });

  function ensureStyles(app) {
    if (typeof document === 'undefined' || !document.head) return;
    if (document.getElementById('ombre-memory-care-styles')) return;
    var link = document.createElement('link');
    link.id = 'ombre-memory-care-styles';
    link.rel = 'stylesheet';
    link.href = typeof app.assetUrl === 'function'
      ? app.assetUrl('memory-care.css')
      : new URL('./dashboard-assets/memory-care.css', document.baseURI).toString();
    document.head.appendChild(link);
  }

  function createApiClient(app) {
    var source = app.api || {};

    async function request(method, path, body) {
      var call = source[method];
      if (typeof call !== 'function') {
        throw new Error('Dashboard API method is unavailable: ' + method.toUpperCase());
      }
      var response = body === undefined
        ? await call.call(source, path)
        : await call.call(source, path, body);
      if (!response) throw new Error('The server returned no response.');
      var data;
      if (typeof source.readJson === 'function') {
        data = await source.readJson(response);
      } else {
        try {
          data = await response.json();
        } catch (_error) {
          data = {};
        }
      }
      if (!response.ok) {
        var requestError = new Error(data && data.error ? data.error : 'HTTP ' + response.status);
        requestError.status = response.status;
        throw requestError;
      }
      return data;
    }

    return {
      get: function (path) { return request('get', path); },
      post: function (path, body) { return request('post', path, body); },
      patch: function (path, body) { return request('patch', path, body); },
    };
  }

  function createUiHelpers(app) {
    var source = app.ui || {};

    function escape(value) {
      if (typeof source.escape === 'function') return source.escape(value);
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function escapeAttr(value) {
      return typeof source.escapeAttr === 'function'
        ? source.escapeAttr(value)
        : escape(value);
    }

    function setStatus(element, message, tone) {
      if (!element) return;
      if (typeof source.setStatus === 'function') {
        try {
          source.setStatus(element, message, tone);
          return;
        } catch (_error) {}
      }
      element.textContent = message || '';
      element.dataset.tone = tone || 'neutral';
    }

    async function confirmAction(message, detail) {
      var fullMessage = detail ? message + '\n\n' + detail : message;
      if (typeof source.confirm === 'function') {
        return Boolean(await Promise.resolve(source.confirm(fullMessage)));
      }
      if (typeof window.confirm === 'function') {
        return Boolean(window.confirm(fullMessage));
      }
      return false;
    }

    function state(stateName, message, retryAction) {
      var action = retryAction
        ? '<button type="button" class="ob-memory-care__retry" data-action="retry" data-retry="' +
          escapeAttr(retryAction) + '">重试</button>'
        : '';
      var accessibility = stateName === 'error'
        ? ' role="alert" aria-live="assertive"'
        : ' role="status" aria-live="polite"';
      return '<div class="ob-memory-care__state" data-state="' + escapeAttr(stateName) +
        '"' + accessibility + '>' +
        '<span>' + escape(message) + '</span>' + action + '</div>';
    }

    return {
      escape: escape,
      escapeAttr: escapeAttr,
      setStatus: setStatus,
      confirmAction: confirmAction,
      loading: function (message) { return state('loading', message || '加载中', ''); },
      empty: function (message) { return state('empty', message || '暂无内容', ''); },
      error: function (message, retryAction) {
        return state('error', message || '加载失败', retryAction || 'reload');
      },
    };
  }

  function listen(root, eventName, selector, handler) {
    root.addEventListener(eventName, function (event) {
      var target = event.target && event.target.closest
        ? event.target.closest(selector)
        : null;
      if (!target || !root.contains(target)) return;
      return handler(event, target);
    });
  }

  function inputValue(root, name) {
    var element = root && root.querySelector('[name="' + name + '"]');
    return element ? String(element.value || '').trim() : '';
  }

  function optionalNumber(root, name, minimum, maximum) {
    var raw = inputValue(root, name);
    if (raw === '') return undefined;
    var value = Number(raw);
    if (!Number.isFinite(value) || value < minimum || value > maximum) {
      throw new Error(name + ' must be between ' + minimum + ' and ' + maximum);
    }
    return value;
  }

  function setBusy(button, busy, busyLabel) {
    if (!button) return;
    if (busy) {
      button.dataset.originalLabel = button.textContent;
      button.textContent = busyLabel || '处理中';
      button.disabled = true;
    } else {
      button.textContent = button.dataset.originalLabel || button.textContent;
      button.disabled = false;
      delete button.dataset.originalLabel;
    }
  }

  function lockControls(root, selector, locked) {
    if (!root || typeof root.querySelectorAll !== 'function') return;
    root.querySelectorAll(selector).forEach(function (control) {
      if (locked) {
        control.dataset.memoryCareWasDisabled = control.disabled ? 'true' : 'false';
        control.disabled = true;
      } else {
        if (control.dataset.originalLabel !== undefined) {
          control.textContent = control.dataset.originalLabel || control.textContent;
          delete control.dataset.originalLabel;
        }
        control.disabled = control.dataset.memoryCareWasDisabled === 'true';
        delete control.dataset.memoryCareWasDisabled;
      }
    });
  }

  function emit(app, eventName, detail) {
    if (eventName === 'buckets:invalidate' && app.store && typeof app.store.invalidate === 'function') {
      app.store.invalidate(['buckets', 'buckets-light']);
    }
    if (app.events && typeof app.events.emit === 'function') {
      app.events.emit(eventName, detail || {});
    }
  }

  function formatDateTime(value) {
    if (!value) return '';
    var parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return parsed.toLocaleString();
  }

  function toDateTimeLocal(value) {
    if (!value) return '';
    var parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 16);
    var local = new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
  }

  function todayLocal() {
    var now = new Date();
    var local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 10);
  }

  function summarizeResult(data) {
    if (!data || typeof data !== 'object') return '操作完成';
    var parts = [];
    ['status', 'date', 'period', 'created', 'updated', 'rejected', 'missing', 'count', 'reason']
      .forEach(function (key) {
        if (data[key] !== undefined && data[key] !== null && data[key] !== '') {
          parts.push(key + ': ' + String(data[key]));
        }
      });
    if (Array.isArray(data.candidates)) parts.push('candidates: ' + data.candidates.length);
    if (data.daily_activity_summary && data.daily_activity_summary.status) {
      parts.push('activity: ' + data.daily_activity_summary.status);
    }
    return parts.join(' · ') || '操作完成';
  }

  function friendlyError(error, fallback) {
    var status = Number(error && error.status || 0);
    if (status >= 500) return fallback + '（服务暂时不可用，HTTP ' + status + '）';
    var message = String(error && error.message || '').replace(/\s+/g, ' ').trim();
    return message ? message.slice(0, 240) : fallback;
  }

  function clipText(value, limit) {
    var text = String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
    var safeLimit = Math.max(1, Number(limit) || 1);
    return text.length > safeLimit ? text.slice(0, safeLimit) + '…' : text;
  }

  function limitedResponseArray(data, key, limit, label) {
    var items = data && Array.isArray(data[key]) ? data[key] : [];
    if (items.length > limit) {
      throw new Error((label || '列表') + '返回超过安全上限（' + limit + ' 条），请重试。');
    }
    return items;
  }

  function createRemindersPanel(app, api, ui) {
    var state = {
      root: null,
      active: false,
      status: 'active',
      items: [],
      editingId: '',
      requestId: 0,
      operationToken: 0,
      inFlight: new Map(),
    };

    function mount(root) {
      state.root = root;
      root.innerHTML = '<section class="ob-memory-care" data-memory-care="reminders">' +
        '<header class="ob-memory-care__header"><div><p class="ob-memory-care__eyebrow">Care</p>' +
        '<h2>照顾备忘</h2><p>按时间或对话轮次触发的独立提醒，不与普通记忆混在一起。</p></div>' +
        '<button type="button" class="ob-memory-care__primary" data-action="new-reminder">新建备忘</button></header>' +
        '<div class="ob-memory-care__toolbar" role="group" aria-label="提醒状态筛选">' +
          reminderFilterButton('active', '进行中', true) +
          reminderFilterButton('done', '已完成', false) +
          reminderFilterButton('archived', '已归档', false) +
          reminderFilterButton('all', '全部', false) +
          '<span class="ob-memory-care__toolbar-status" data-role="reminder-status" aria-live="polite"></span>' +
        '</div>' +
        '<div class="ob-memory-care__editor" data-role="reminder-editor" hidden>' + reminderForm() + '</div>' +
        '<div class="ob-memory-care__list" data-role="reminder-list">' + ui.loading('读取照顾备忘') + '</div>' +
      '</section>';

      listen(root, 'click', '[data-action]', onClick);
      listen(root, 'submit', '[data-role="reminder-form"]', saveReminder);
    }

    function reminderFilterButton(status, label, active) {
      return '<button type="button" class="ob-memory-care__chip' + (active ? ' is-active' : '') +
        '" data-action="filter-reminders" data-status="' + status + '">' + label + '</button>';
    }

    function reminderForm() {
      return '<form class="ob-memory-care__form" data-role="reminder-form">' +
        '<div class="ob-memory-care__form-heading"><h3 data-role="reminder-form-title">新建备忘</h3>' +
        '<button type="button" class="ob-memory-care__quiet" data-action="cancel-reminder">取消</button></div>' +
        '<div class="ob-memory-care__form-grid">' +
          field('标题', '<input name="title" maxlength="160" required>') +
          field('正文', '<textarea name="content" rows="3" maxlength="4000" required></textarea>', true) +
          field('开始', '<input name="start_at" type="datetime-local">') +
          field('结束', '<input name="end_at" type="datetime-local">') +
          field('下次触发', '<input name="next_due_at" type="datetime-local">') +
          field('重复', '<select name="repeat_rule"><option value="every_n_rounds">按轮次</option><option value="daily">每天</option><option value="morning_evening">早晚</option><option value="once">一次</option><option value="none">不重复</option></select>') +
          field('间隔轮次', '<input name="interval_rounds" type="number" min="0" max="100000" value="6">') +
          field('每日上限', '<input name="daily_limit" type="number" min="0" max="100">') +
          field('总触发上限', '<input name="max_injections" type="number" min="0" max="100000">') +
          field('冷却分钟', '<input name="cooldown_minutes" type="number" min="0" max="525600">') +
          field('频道', '<select name="channel"><option value="global">global</option><option value="session">session</option></select>') +
          field('会话 ID', '<input name="session_id" maxlength="240">') +
        '</div>' +
        '<div class="ob-memory-care__form-actions"><span data-role="reminder-form-status" aria-live="polite"></span>' +
        '<button type="submit" class="ob-memory-care__primary">保存</button></div>' +
      '</form>';
    }

    function field(label, control, wide) {
      return '<label class="ob-memory-care__field' + (wide ? ' is-wide' : '') + '"><span>' +
        label + '</span>' + control + '</label>';
    }

    function onClick(_event, button) {
      var action = button.dataset.action;
      if (action === 'filter-reminders') {
        state.status = button.dataset.status || 'active';
        state.root.querySelectorAll('[data-action="filter-reminders"]').forEach(function (item) {
          item.classList.toggle('is-active', item === button);
        });
        load();
      } else if (action === 'new-reminder') {
        openEditor(null);
      } else if (action === 'cancel-reminder') {
        closeEditor();
      } else if (action === 'edit-reminder') {
        openEditor(findReminder(button.dataset.id));
      } else if (action === 'complete-reminder') {
        return updateStatus(button.dataset.id, 'done', button);
      } else if (action === 'archive-reminder') {
        return archive(button.dataset.id, button);
      } else if (action === 'reopen-reminder') {
        return updateStatus(button.dataset.id, 'active', button);
      } else if (action === 'snooze-reminder') {
        return snooze(button.dataset.id, button);
      } else if (action === 'retry' && button.dataset.retry === 'reminders') {
        return load();
      }
    }

    function findReminder(id) {
      return state.items.find(function (item) { return String(item.id || '') === String(id || ''); });
    }

    function openEditor(item) {
      var editor = state.root.querySelector('[data-role="reminder-editor"]');
      var form = state.root.querySelector('[data-role="reminder-form"]');
      if (!editor || !form) return;
      form.reset();
      state.editingId = item ? String(item.id || '') : '';
      form.querySelector('[name="interval_rounds"]').value = item && item.interval_rounds != null
        ? item.interval_rounds : 6;
      ['title', 'content', 'repeat_rule', 'daily_limit', 'max_injections', 'cooldown_minutes', 'channel', 'session_id']
        .forEach(function (key) {
          if (!item || item[key] == null) return;
          var input = form.querySelector('[name="' + key + '"]');
          if (input) input.value = item[key];
        });
      ['start_at', 'end_at', 'next_due_at'].forEach(function (key) {
        var input = form.querySelector('[name="' + key + '"]');
        if (input) input.value = item ? toDateTimeLocal(item[key]) : '';
      });
      form.querySelector('[name="content"]').value = item ? (item.content || item.text || '') : '';
      var heading = form.querySelector('[data-role="reminder-form-title"]');
      if (heading) heading.textContent = item ? '编辑备忘' : '新建备忘';
      editor.hidden = false;
      form.querySelector('[name="title"]').focus();
    }

    function closeEditor() {
      state.editingId = '';
      var editor = state.root && state.root.querySelector('[data-role="reminder-editor"]');
      if (editor) editor.hidden = true;
    }

    function buildPayload(form) {
      var title = inputValue(form, 'title');
      var content = inputValue(form, 'content');
      if (!title) throw new Error('标题不能为空');
      if (!content) throw new Error('正文不能为空');
      var payload = {
        title: title,
        content: content,
        start_at: inputValue(form, 'start_at'),
        end_at: inputValue(form, 'end_at'),
        next_due_at: inputValue(form, 'next_due_at'),
        repeat_rule: inputValue(form, 'repeat_rule') || 'every_n_rounds',
        channel: inputValue(form, 'channel') || 'global',
        session_id: inputValue(form, 'session_id'),
      };
      [
        ['interval_rounds', 0, 100000],
        ['daily_limit', 0, 100],
        ['max_injections', 0, 100000],
        ['cooldown_minutes', 0, 525600],
      ].forEach(function (spec) {
        var value = optionalNumber(form, spec[0], spec[1], spec[2]);
        if (value !== undefined) payload[spec[0]] = value;
      });
      return payload;
    }

    async function saveReminder(event) {
      event.preventDefault();
      var form = event.target;
      var button = form.querySelector('[type="submit"]');
      var status = form.querySelector('[data-role="reminder-form-status"]');
      var operation;
      try {
        ui.setStatus(status, '', 'neutral');
        var payload = buildPayload(form);
        operation = reserveReminder(state.editingId || '__create__', button);
        if (!operation) {
          ui.setStatus(status, '这条备忘已有更新正在进行。', 'neutral');
          return;
        }
        setBusy(button, true, '保存中');
        if (!validReminderOperation(operation)) return;
        if (state.editingId) {
          await api.patch('/api/reminders/' + encodeURIComponent(state.editingId), payload);
        } else {
          await api.post('/api/reminders', payload);
        }
        if (!validReminderOperation(operation)) return;
        closeEditor();
        await load();
      } catch (error) {
        if (validReminderOperation(operation)) {
          ui.setStatus(status, friendlyError(error, '保存失败'), 'error');
        }
      } finally {
        if (operation && state.inFlight.get(operation.key) === operation.token) {
          setBusy(button, false);
          releaseReminder(operation);
        }
      }
    }

    async function load() {
      if (!state.root) return;
      var requestId = ++state.requestId;
      var list = state.root.querySelector('[data-role="reminder-list"]');
      list.innerHTML = ui.loading('读取照顾备忘');
      try {
        var data = await api.get('/api/reminders?status=' + encodeURIComponent(state.status) + '&limit=100');
        if (!state.active || requestId !== state.requestId) return;
        state.items = limitedResponseArray(data, 'reminders', 100, '照顾备忘');
        renderList();
      } catch (error) {
        if (!state.active || requestId !== state.requestId) return;
        list.innerHTML = ui.error('读取失败：' + friendlyError(error, '无法读取照顾备忘'), 'reminders');
      }
    }

    function renderList() {
      var list = state.root.querySelector('[data-role="reminder-list"]');
      var status = state.root.querySelector('[data-role="reminder-status"]');
      ui.setStatus(status, state.items.length + ' 条', 'neutral');
      if (!state.items.length) {
        list.innerHTML = ui.empty('这个状态下还没有照顾备忘。');
        return;
      }
      list.innerHTML = state.items.map(renderReminder).join('');
    }

    function renderReminder(item) {
      var id = String(item.id || '');
      var status = String(item.status || 'active');
      var repeat = reminderRepeatLabel(item.repeat_rule, item.interval_rounds);
      var timing = item.next_due_at ? '下次 ' + formatDateTime(item.next_due_at)
        : item.start_at ? '开始 ' + formatDateTime(item.start_at) : '未设时间';
      var meta = [timing, repeat, reminderStatusLabel(status)];
      if (item.daily_limit) meta.push('每天最多 ' + item.daily_limit + ' 次');
      if (item.max_injections) meta.push('总共最多 ' + item.max_injections + ' 次');
      var actions = '<button type="button" data-action="edit-reminder" data-id="' + ui.escapeAttr(id) + '">编辑</button>';
      if (status === 'active') {
        actions += '<button type="button" data-action="complete-reminder" data-id="' + ui.escapeAttr(id) + '">标完成</button>' +
          '<label class="ob-memory-care__snooze"><span class="sr-only">稍后时长</span><select data-role="snooze-minutes"><option value="30">30 分钟</option><option value="60">1 小时</option><option value="240">4 小时</option><option value="1440">明天</option></select>' +
          '<button type="button" data-action="snooze-reminder" data-id="' + ui.escapeAttr(id) + '">稍后</button></label>' +
          '<button type="button" class="is-danger" data-action="archive-reminder" data-id="' + ui.escapeAttr(id) + '">归档</button>';
      } else {
        actions += '<button type="button" data-action="reopen-reminder" data-id="' + ui.escapeAttr(id) + '">重新打开</button>';
      }
      return '<article class="ob-memory-care__card" data-reminder-id="' + ui.escapeAttr(id) + '">' +
        '<div class="ob-memory-care__card-heading"><div><h3>' + ui.escape(item.title || '照顾备忘') + '</h3>' +
        '<p>' + ui.escape(meta.filter(Boolean).join(' · ')) + '</p></div>' +
        '<span class="ob-memory-care__badge" data-status="' + ui.escapeAttr(status) + '">' + ui.escape(reminderStatusLabel(status)) + '</span></div>' +
        '<div class="ob-memory-care__body-text">' + ui.escape(item.content || item.text || '') + '</div>' +
        '<div class="ob-memory-care__actions">' + actions + '</div></article>';
    }

    function reminderRepeatLabel(rule, rounds) {
      if (rule === 'every_n_rounds') return rounds ? '每 ' + rounds + ' 轮' : '按轮次';
      if (rule === 'daily') return '每天';
      if (rule === 'morning_evening') return '早晚';
      if (rule === 'once') return '一次';
      if (rule === 'none') return '不重复';
      return rule || '';
    }

    function reminderStatusLabel(status) {
      return { active: '进行中', done: '已完成', archived: '已归档', all: '全部' }[status] || status;
    }

    function reminderCard(button, id) {
      var direct = button && typeof button.closest === 'function'
        ? button.closest('[data-reminder-id]') : null;
      if (direct) return direct;
      if (!state.root || typeof state.root.querySelectorAll !== 'function') return null;
      var cards = state.root.querySelectorAll('[data-reminder-id]');
      for (var index = 0; index < cards.length; index += 1) {
        if (String(cards[index].dataset.reminderId || '') === String(id || '')) return cards[index];
      }
      return null;
    }

    function reserveReminder(id, button) {
      var key = String(id || '');
      if (!key || state.inFlight.has(key)) return null;
      var operation = {
        key: key,
        token: ++state.operationToken,
        card: reminderCard(button, key),
      };
      state.inFlight.set(key, operation.token);
      lockControls(operation.card, 'button, input, select, textarea', true);
      return operation;
    }

    function validReminderOperation(operation) {
      return Boolean(operation && state.active &&
        state.inFlight.get(operation.key) === operation.token);
    }

    function releaseReminder(operation) {
      if (!operation || state.inFlight.get(operation.key) !== operation.token) return;
      state.inFlight.delete(operation.key);
      lockControls(operation.card, 'button, input, select, textarea', false);
    }

    async function mutateReminder(id, button, payload, options) {
      var settings = options || {};
      var operation = reserveReminder(id, button);
      if (!operation) return;
      try {
        if (settings.confirm && !await ui.confirmAction(settings.confirm.message, settings.confirm.detail)) return;
        if (!validReminderOperation(operation)) return;
        setBusy(button, true, settings.busyLabel || '更新中');
        await api.patch('/api/reminders/' + encodeURIComponent(id), payload);
        if (validReminderOperation(operation)) await load();
      } catch (error) {
        if (state.active) {
          ui.setStatus(state.root.querySelector('[data-role="reminder-status"]'),
            (settings.errorLabel || '更新失败') + '：' + friendlyError(error, '无法更新照顾备忘'), 'error');
        }
      } finally {
        if (state.inFlight.get(operation.key) === operation.token) {
          setBusy(button, false);
          releaseReminder(operation);
        }
      }
    }

    function updateStatus(id, nextStatus, button) {
      return mutateReminder(id, button, { status: nextStatus });
    }

    async function archive(id, button) {
      return mutateReminder(id, button, { status: 'archived' }, {
        confirm: {
          message: '归档这条照顾备忘？',
          detail: '归档后仍可在“已归档”中重新打开。',
        },
      });
    }

    function snooze(id, button) {
      var card = button.closest('[data-reminder-id]');
      var select = card && card.querySelector('[data-role="snooze-minutes"]');
      var minutes = Number(select && select.value || 30);
      return mutateReminder(id, button, { snooze_minutes: minutes }, {
        errorLabel: '稍后失败',
      });
    }

    return {
      id: 'memory-reminders',
      workspace: 'memory',
      label: '照顾备忘',
      order: 10,
      mount: mount,
      activate: function () { state.active = true; return load(); },
      deactivate: function () {
        state.active = false;
        state.requestId += 1;
        state.operationToken += 1;
        state.inFlight.clear();
        lockControls(state.root,
          '[data-reminder-id] button, [data-reminder-id] input, [data-reminder-id] select, [data-reminder-id] textarea, [data-role="reminder-form"] button, [data-role="reminder-form"] input, [data-role="reminder-form"] select, [data-role="reminder-form"] textarea',
          false);
      },
    };
  }

  function createReflectionPanel(app, api, ui) {
    var now = new Date();
    var state = {
      root: null,
      active: false,
      requestId: 0,
      month: new Date(now.getFullYear(), now.getMonth(), 1),
      selectedDate: todayLocal(),
      impressions: [],
      details: Object.create(null),
      sourceDetails: Object.create(null),
      rawEventsByDate: Object.create(null),
      dayPage: 0,
      dayPageSize: 20,
      detailConcurrency: 4,
      sourceDetailConcurrency: 4,
      sourceDetailLimit: 24,
      rawEventLimit: 40,
      dayViewToken: 0,
      operationToken: 0,
      activeOperation: 0,
    };

    function mount(root) {
      state.root = root;
      root.innerHTML = '<section class="ob-memory-care" data-memory-care="reflection">' +
        '<header class="ob-memory-care__header"><div><p class="ob-memory-care__eyebrow">Daily processing</p>' +
        '<h2>日印象与活动</h2><p>生成 relationship weather，并在同一处查看每天留下的印象。</p></div></header>' +
        '<div class="ob-memory-care__run-grid">' +
          '<section class="ob-memory-care__run-card"><h3>生成日印象</h3><p>整理近期记忆与对话，写入一条 daily impression。</p>' +
          '<label class="ob-memory-care__check"><input type="checkbox" name="reflection_force"> 强制重新运行</label>' +
          '<button type="button" class="ob-memory-care__primary" data-action="run-reflection">运行 Reflection</button></section>' +
          '<section class="ob-memory-care__run-card"><h3>每日活动摘要</h3><p>从当天原始事件和对话生成活动摘要。</p>' +
          '<label class="ob-memory-care__field"><span>日期</span><input type="date" name="activity_date" value="' + todayLocal() + '"></label>' +
          '<label class="ob-memory-care__check"><input type="checkbox" name="activity_force"> 强制重新运行</label>' +
          '<button type="button" class="ob-memory-care__primary" data-action="run-activity">生成活动摘要</button></section>' +
        '</div>' +
        '<div class="ob-memory-care__operation-status" data-role="reflection-operation-status" aria-live="polite"></div>' +
        '<section class="ob-memory-care__calendar-shell">' +
          '<div class="ob-memory-care__calendar-card"><div class="ob-memory-care__calendar-heading"><h3>日印象月历</h3>' +
          '<div class="ob-memory-care__calendar-nav"><button type="button" data-action="shift-month" data-delta="-1" aria-label="上个月">‹</button>' +
          '<button type="button" data-action="today">今天</button>' +
          '<button type="button" data-action="shift-month" data-delta="1" aria-label="下个月">›</button></div></div>' +
          '<p class="ob-memory-care__month-label" data-role="month-label"></p>' +
          '<div class="ob-memory-care__weekdays"><span>日</span><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span></div>' +
          '<div class="ob-memory-care__calendar" data-role="reflection-calendar">' + ui.loading('读取日印象') + '</div></div>' +
          '<div class="ob-memory-care__day-card" data-role="reflection-day">' + ui.loading('读取选中日期') + '</div>' +
        '</section>' +
      '</section>';

      listen(root, 'click', '[data-action]', onClick);
    }

    function onClick(_event, button) {
      var action = button.dataset.action;
      if (action === 'shift-month') {
        state.month = new Date(state.month.getFullYear(), state.month.getMonth() + Number(button.dataset.delta || 0), 1);
        state.selectedDate = localDate(state.month);
        state.dayPage = 0;
        renderCalendar();
      } else if (action === 'today') {
        var current = new Date();
        state.month = new Date(current.getFullYear(), current.getMonth(), 1);
        state.selectedDate = todayLocal();
        state.dayPage = 0;
        renderCalendar();
      } else if (action === 'select-reflection-date') {
        state.selectedDate = button.dataset.date || todayLocal();
        state.dayPage = 0;
        renderCalendar();
      } else if (action === 'shift-reflection-day-page') {
        state.dayPage = Math.max(0, state.dayPage + Number(button.dataset.delta || 0));
        var selectedItems = byDate()[state.selectedDate] || [];
        renderSelectedDay(selectedItems, state.requestId);
      } else if (action === 'run-reflection') {
        return runReflection(button);
      } else if (action === 'run-activity') {
        return runActivity(button);
      } else if (action === 'open-bucket') {
        openBucket(button.dataset.id);
      } else if (action === 'retry' && button.dataset.retry === 'reflection') {
        return loadHistory();
      } else if (action === 'retry' && button.dataset.retry === 'reflection-day-events') {
        delete state.rawEventsByDate[state.selectedDate];
        renderSelectedDay((byDate()[state.selectedDate] || []), state.requestId);
      } else if (action === 'retry' && button.dataset.retry === 'reflection-sources') {
        Object.keys(state.sourceDetails).forEach(function (id) {
          if (state.sourceDetails[id] && state.sourceDetails[id].__error) {
            delete state.sourceDetails[id];
          }
        });
        renderSelectedDay((byDate()[state.selectedDate] || []), state.requestId);
      }
    }

    function localDate(date) {
      var local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
      return local.toISOString().slice(0, 10);
    }

    function impressionDate(bucket) {
      var view = bucket && bucket.metadata_view && typeof bucket.metadata_view === 'object'
        ? bucket.metadata_view : {};
      var direct = bucket.date || bucket.event_date || view.event_date;
      if (direct) return String(direct).slice(0, 10);
      var match = String(bucket.id || '').match(/^reflection_daily_(\d{4}-\d{2}-\d{2})$/);
      return match ? match[1] : String(bucket.created || '').slice(0, 10);
    }

    function isDailyImpression(bucket) {
      if (!bucket || typeof bucket !== 'object') return false;
      var tags = Array.isArray(bucket.tags) ? bucket.tags.map(String) : [];
      return bucket.type === 'feel' && tags.includes('relationship_weather') && tags.includes('daily_impression');
    }

    async function loadHistory() {
      if (!state.root) return;
      var requestId = ++state.requestId;
      var calendar = state.root.querySelector('[data-role="reflection-calendar"]');
      var day = state.root.querySelector('[data-role="reflection-day"]');
      calendar.innerHTML = ui.loading('读取日印象');
      day.innerHTML = ui.loading('读取选中日期');
      state.details = Object.create(null);
      state.sourceDetails = Object.create(null);
      state.rawEventsByDate = Object.create(null);
      state.dayPage = 0;
      state.dayViewToken += 1;
      try {
        var buckets = await loadAllLightBuckets(requestId);
        if (!state.active || requestId !== state.requestId) return;
        state.impressions = buckets.filter(isDailyImpression).map(function (bucket) {
          var copy = Object.assign({}, bucket);
          copy.reflection_date = impressionDate(bucket);
          return copy;
        }).filter(function (bucket) {
          return /^\d{4}-\d{2}-\d{2}$/.test(bucket.reflection_date);
        });
        renderCalendar();
      } catch (error) {
        if (!state.active || requestId !== state.requestId) return;
        calendar.innerHTML = ui.error('日印象读取失败：' + friendlyError(error, '无法读取日印象'), 'reflection');
        day.innerHTML = ui.empty('无法显示当天详情。');
      }
    }

    async function loadAllLightBuckets(requestId) {
      var pageSize = 2000;
      var maxPages = 50;
      var maxItems = 100000;
      var dailyFilter = '&type=feel&tags=relationship_weather,daily_impression&sort=created_desc';
      var offset = 0;
      var buckets = [];
      var expectedTotal = null;
      var seenBucketIds = Object.create(null);
      for (var pageNumber = 0; pageNumber < maxPages; pageNumber += 1) {
        var data = await api.get('/api/buckets/light?include_archive=false&limit=' + pageSize + '&offset=' + offset + dailyFilter);
        if (!state.active || requestId !== state.requestId) return [];
        var page = Array.isArray(data) ? data : (Array.isArray(data.buckets) ? data.buckets : []);
        if (Array.isArray(data)) {
          if (page.length > maxItems) {
            throw new Error('日印象历史超过安全读取上限（100000 条），无法完整显示。');
          }
          return page;
        }
        var currentTotal = Number(data.count);
        if (!Number.isFinite(currentTotal) || currentTotal < 0) {
          throw new Error('日印象历史分页总数无效，请重试。');
        }
        if (currentTotal > maxItems || page.length > maxItems || buckets.length + page.length > maxItems) {
          throw new Error('日印象历史超过安全读取上限（100000 条），无法完整显示。');
        }
        if (page.length > pageSize) {
          throw new Error('日印象历史单页超过安全分页大小，请重试。');
        }
        if (expectedTotal === null) {
          expectedTotal = currentTotal;
        } else if (currentTotal !== expectedTotal) {
          throw new Error('日印象历史在分页读取期间发生变化，请重试。');
        }
        if (offset + page.length > expectedTotal) {
          throw new Error('日印象历史返回数量与分页总数不一致，请重试。');
        }
        for (var pageIndex = 0; pageIndex < page.length; pageIndex += 1) {
          var bucket = page[pageIndex];
          var bucketId = String(bucket && (bucket.id || bucket.bucket_id) || '').trim();
          if (!bucketId) continue;
          if (seenBucketIds[bucketId]) {
            throw new Error('日印象历史分页出现重复记录，请重试。');
          }
          seenBucketIds[bucketId] = true;
        }
        buckets = buckets.concat(page);
        if (buckets.length >= expectedTotal) return buckets;
        if (!page.length) {
          if (expectedTotal > buckets.length) {
            throw new Error('日印象历史在分页读取期间发生变化，请重试。');
          }
          return buckets;
        }
        if (page.length < pageSize) {
          if (expectedTotal > buckets.length) {
            throw new Error('日印象历史返回不完整，请重试。');
          }
          return buckets;
        }
        offset += page.length;
      }
      throw new Error('日印象历史超过安全读取上限（100000 条），无法完整显示。');
    }

    function byDate() {
      return state.impressions.reduce(function (result, item) {
        var key = item.reflection_date;
        if (!result[key]) result[key] = [];
        result[key].push(item);
        return result;
      }, Object.create(null));
    }

    function renderCalendar() {
      if (!state.root) return;
      var calendar = state.root.querySelector('[data-role="reflection-calendar"]');
      var label = state.root.querySelector('[data-role="month-label"]');
      var grouped = byDate();
      var year = state.month.getFullYear();
      var month = state.month.getMonth();
      label.textContent = year + ' 年 ' + (month + 1) + ' 月';
      var leading = new Date(year, month, 1).getDay();
      var count = new Date(year, month + 1, 0).getDate();
      var cells = [];
      for (var blank = 0; blank < leading; blank += 1) {
        cells.push('<span class="ob-memory-care__calendar-empty" aria-hidden="true"></span>');
      }
      for (var day = 1; day <= count; day += 1) {
        var date = localDate(new Date(year, month, day));
        var items = grouped[date] || [];
        cells.push('<button type="button" class="ob-memory-care__calendar-day' +
          (date === state.selectedDate ? ' is-selected' : '') + (items.length ? ' has-entry' : '') +
          '" data-action="select-reflection-date" data-date="' + ui.escapeAttr(date) +
          '" aria-label="' + ui.escapeAttr(date + (items.length ? '，有日印象' : '')) + '">' +
          '<span>' + day + '</span>' + (items.length ? '<i aria-hidden="true"></i>' : '') + '</button>');
      }
      calendar.innerHTML = cells.join('');
      renderSelectedDay(grouped[state.selectedDate] || [], state.requestId);
    }

    function renderSelectedDay(items, requestId, viewToken) {
      var day = state.root.querySelector('[data-role="reflection-day"]');
      var generation = requestId == null ? state.requestId : requestId;
      var currentView = viewToken == null ? ++state.dayViewToken : viewToken;
      var selected = state.selectedDate;
      var pageCount = Math.max(1, Math.ceil(items.length / state.dayPageSize));
      state.dayPage = Math.min(Math.max(0, state.dayPage), pageCount - 1);
      var pageStart = state.dayPage * state.dayPageSize;
      var visibleItems = items.slice(pageStart, pageStart + state.dayPageSize);
      var rawState = ensureRawEventState(selected);
      var heading = '<div class="ob-memory-care__day-heading"><div><p class="ob-memory-care__eyebrow">Selected day</p>' +
        '<h3>' + ui.escape(selected) + '</h3></div><span>' + items.length + ' 条日印象</span></div>';
      var pagination = pageCount > 1
        ? '<nav class="ob-memory-care__day-pagination" aria-label="日印象分页">' +
          '<button type="button" data-action="shift-reflection-day-page" data-delta="-1"' +
          (state.dayPage === 0 ? ' disabled' : '') + '>上一页</button>' +
          '<span>第 ' + (state.dayPage + 1) + ' / ' + pageCount + ' 页</span>' +
          '<button type="button" data-action="shift-reflection-day-page" data-delta="1"' +
          (state.dayPage >= pageCount - 1 ? ' disabled' : '') + '>下一页</button></nav>'
        : '';
      var impressions = items.length
        ? pagination + '<div class="ob-memory-care__day-list">' +
          visibleItems.map(renderImpression).join('') + '</div>'
        : ui.empty('这一天没有日印象。');
      day.innerHTML = heading +
        '<section class="ob-memory-care__day-section" aria-labelledby="reflection-impressions-heading">' +
          '<div class="ob-memory-care__section-heading"><h4 id="reflection-impressions-heading">日印象</h4>' +
          '<span>' + items.length + ' 条</span></div>' + impressions +
        '</section>' + renderDatedEvents(visibleItems, rawState);
      hydrateDetails(visibleItems, generation, currentView, items);
      hydrateSourceDetails(visibleItems, generation, currentView, items);
      loadRawEvents(selected, generation, currentView, items);
    }

    function renderImpression(item) {
      var id = String(item.id || item.bucket_id || '');
      var detail = state.details[id];
      var content = detail && detail.content || item.content_preview || '正在读取正文';
      var valence = item.valence == null ? '' : 'V' + Number(item.valence).toFixed(2);
      var arousal = item.arousal == null ? '' : 'A' + Number(item.arousal).toFixed(2);
      return '<article class="ob-memory-care__day-entry" data-impression-id="' + ui.escapeAttr(id) + '">' +
        '<div class="ob-memory-care__card-heading"><div><h4>' + ui.escape(item.name || id || '日印象') + '</h4>' +
        '<p>' + ui.escape([valence, arousal, item.confidence == null ? '' : '置信度 ' + item.confidence].filter(Boolean).join(' · ')) + '</p></div>' +
        '<button type="button" class="ob-memory-care__quiet" data-action="open-bucket" data-id="' + ui.escapeAttr(id) + '">在记忆桶中打开</button></div>' +
        '<div class="ob-memory-care__body-text" data-role="impression-body">' + ui.escape(content) + '</div>' +
        renderSourceEvidence(detail) +
      '</article>';
    }

    function detailValue(detail, key) {
      if (!detail || typeof detail !== 'object') return undefined;
      if (detail[key] !== undefined && detail[key] !== null) return detail[key];
      var metadata = detail.metadata && typeof detail.metadata === 'object' ? detail.metadata : {};
      if (metadata[key] !== undefined && metadata[key] !== null) return metadata[key];
      var view = detail.metadata_view && typeof detail.metadata_view === 'object'
        ? detail.metadata_view : {};
      return view[key];
    }

    function evidenceIds(detail, key) {
      var values = detailValue(detail, key);
      if (!Array.isArray(values)) return [];
      var seen = Object.create(null);
      return values.map(function (value) { return String(value == null ? '' : value).trim(); })
        .filter(function (value) {
          if (!value || seen[value]) return false;
          seen[value] = true;
          return true;
        });
    }

    function sourceIdsForImpressions(items) {
      var seen = Object.create(null);
      var ids = [];
      items.forEach(function (item) {
        var id = String(item.id || item.bucket_id || '');
        evidenceIds(state.details[id], 'source_bucket_ids').forEach(function (sourceId) {
          if (seen[sourceId]) return;
          seen[sourceId] = true;
          ids.push(sourceId);
        });
      });
      return ids;
    }

    function detailTags(detail) {
      var tags = detailValue(detail, 'tags');
      if (typeof tags === 'string') tags = tags.split(',');
      return Array.isArray(tags)
        ? tags.map(function (tag) { return String(tag || '').trim(); }).filter(Boolean)
        : [];
    }

    function detailDate(detail) {
      var value = detailValue(detail, 'date') || detailValue(detail, 'event_date') || '';
      return String(value).slice(0, 10);
    }

    function isDatedSourceEvent(detail, date) {
      if (!detail || detail.__loading || detail.__error) return false;
      var tags = detailTags(detail);
      var type = String(detailValue(detail, 'type') || '');
      if (type === 'feel' || tags.includes('daily_impression') ||
          tags.includes('relationship_weather') || tags.includes('profile_fact') ||
          tags.includes('self_anchor') || tags.includes('自我')) return false;
      if (Boolean(detailValue(detail, 'resolved')) || Boolean(detailValue(detail, 'digested'))) return false;
      return detailDate(detail) === date;
    }

    function rawEventByEvidenceId(rawId) {
      var rawState = state.rawEventsByDate[state.selectedDate];
      if (!rawState || rawState.status !== 'loaded') return null;
      return rawState.items.find(function (event) {
        return String(event.id == null ? '' : event.id) === rawId ||
          String(event.source_event_id == null ? '' : event.source_event_id) === rawId;
      }) || null;
    }

    function evidenceChip(label, title) {
      return '<span class="ob-memory-care__evidence-chip"' +
        (title ? ' title="' + ui.escapeAttr(title) + '"' : '') + '>' + ui.escape(label) + '</span>';
    }

    function renderSourceEvidence(detail) {
      var heading = '<div class="ob-memory-care__evidence-heading"><h5>来源证据</h5></div>';
      if (!detail || detail.__loading) {
        return '<section class="ob-memory-care__evidence" aria-label="来源证据">' + heading +
          ui.loading('读取来源证据') + '</section>';
      }
      if (detail.__error) {
        return '<section class="ob-memory-care__evidence" aria-label="来源证据">' + heading +
          ui.error('来源证据读取失败：' + detail.error, 'reflection') + '</section>';
      }
      var bucketIds = evidenceIds(detail, 'source_bucket_ids');
      var rawIds = evidenceIds(detail, 'source_raw_event_ids');
      var turnIds = evidenceIds(detail, 'source_conversation_turn_ids');
      if (!bucketIds.length && !rawIds.length && !turnIds.length) {
        return '<section class="ob-memory-care__evidence" aria-label="来源证据">' + heading +
          ui.empty('这条日印象没有记录来源证据。') + '</section>';
      }
      var bucketChips = bucketIds.slice(0, 8).map(function (id) {
        var source = state.sourceDetails[id];
        var label = source && !source.__loading && !source.__error
          ? String(detailValue(source, 'name') || source.id || id) : id;
        if (source && !source.__loading && !source.__error) {
          return '<button type="button" class="ob-memory-care__evidence-chip" data-action="open-bucket" data-id="' +
            ui.escapeAttr(id) + '">' + ui.escape(label) + '</button>';
        }
        return evidenceChip(label, source && source.__error ? '参考记忆桶暂时无法读取' : '参考记忆桶');
      });
      var rawChips = rawIds.slice(0, 12).map(function (id) {
        var event = rawEventByEvidenceId(id);
        var label = event
          ? '原始事件 ' + id + ' · ' + String(event.role || event.source || 'event')
          : '原始事件 ' + id;
        return evidenceChip(label, event ? clipText(event.text, 180) : '原始事件 ID');
      });
      var turnChips = turnIds.slice(0, 12).map(function (id) {
        return evidenceChip('对话轮次 ' + id, 'Conversation turn ID');
      });
      var groups = [];
      if (bucketChips.length) groups.push(evidenceGroup('参考记忆桶', bucketChips, bucketIds.length));
      if (rawChips.length) groups.push(evidenceGroup('原始事件', rawChips, rawIds.length));
      if (turnChips.length) groups.push(evidenceGroup('对话轮次', turnChips, turnIds.length));
      return '<section class="ob-memory-care__evidence" aria-label="来源证据">' + heading +
        groups.join('') + '</section>';
    }

    function evidenceGroup(label, chips, total) {
      var hiddenCount = Math.max(0, total - chips.length);
      return '<div class="ob-memory-care__evidence-group"><h6>' + ui.escape(label) + '</h6>' +
        '<div class="ob-memory-care__evidence-chips">' + chips.join('') + '</div>' +
        (hiddenCount ? '<p>另有 ' + hiddenCount + ' 条，可在记忆桶详情中查看。</p>' : '') + '</div>';
    }

    function ensureRawEventState(date) {
      if (!state.rawEventsByDate[date]) {
        state.rawEventsByDate[date] = { status: 'loading', items: [], error: '', started: false };
      }
      return state.rawEventsByDate[date];
    }

    function renderDatedEvents(items, rawState) {
      var sourceIds = sourceIdsForImpressions(items);
      var limitedSourceIds = sourceIds.slice(0, state.sourceDetailLimit);
      var sourceRecords = limitedSourceIds.map(function (id) { return state.sourceDetails[id]; })
        .filter(function (detail) { return isDatedSourceEvent(detail, state.selectedDate); });
      var sourceLoading = items.some(function (item) {
        var id = String(item.id || item.bucket_id || '');
        return id && (!state.details[id] || state.details[id].__loading);
      }) || limitedSourceIds.some(function (id) {
        return !state.sourceDetails[id] || state.sourceDetails[id].__loading;
      });
      var sourceFailed = limitedSourceIds.some(function (id) {
        return state.sourceDetails[id] && state.sourceDetails[id].__error;
      });
      var sourceList = sourceRecords.length
        ? '<div class="ob-memory-care__event-list">' + sourceRecords.map(renderDatedSourceEvent).join('') + '</div>'
        : '';
      if (sourceLoading) {
        sourceList += ui.loading('读取带日期的参考记忆');
      } else if (sourceFailed) {
        sourceList += ui.error('部分参考记忆读取失败。', 'reflection-sources');
      } else if (!sourceRecords.length) {
        sourceList = ui.empty('这一天没有日印象引用的带日期记忆事件。');
      }
      if (sourceIds.length > state.sourceDetailLimit) {
        sourceList += '<p class="ob-memory-care__bounded-note">为保持页面流畅，本页最多读取 ' +
          state.sourceDetailLimit + ' 个参考记忆桶。</p>';
      }

      var rawList;
      if (rawState.status === 'loading') {
        rawList = ui.loading('读取当天原始事件');
      } else if (rawState.status === 'error') {
        rawList = ui.error('原始事件读取失败：' + rawState.error, 'reflection-day-events');
      } else if (!rawState.items.length) {
        rawList = ui.empty('这一天没有保存的原始事件。');
      } else {
        rawList = '<div class="ob-memory-care__event-list">' +
          rawState.items.map(renderRawEvent).join('') + '</div>';
      }
      var rawCount = rawState.status === 'loaded' ? rawState.items.length : 0;
      var total = sourceRecords.length + rawCount;
      return '<section class="ob-memory-care__day-section ob-memory-care__events" aria-labelledby="reflection-events-heading">' +
        '<div class="ob-memory-care__section-heading"><h4 id="reflection-events-heading">当天发生了什么</h4>' +
        '<span>' + total + ' 条可见证据</span></div>' +
        '<section class="ob-memory-care__event-group" aria-labelledby="reflection-memory-events-heading">' +
          '<div class="ob-memory-care__subheading"><h5 id="reflection-memory-events-heading">带日期的记忆事件</h5>' +
          '<span>' + sourceRecords.length + ' 条</span></div>' + sourceList + '</section>' +
        '<section class="ob-memory-care__event-group" aria-labelledby="reflection-raw-events-heading">' +
          '<div class="ob-memory-care__subheading"><h5 id="reflection-raw-events-heading">当天原始事件</h5>' +
          '<span>' + rawCount + ' 条，最多 ' + state.rawEventLimit + ' 条</span></div>' + rawList + '</section>' +
      '</section>';
    }

    function renderDatedSourceEvent(detail) {
      var id = String(detail.id || detailValue(detail, 'id') || '');
      var meta = [detailValue(detail, 'source'), detailValue(detail, 'kind'),
        '重要度 ' + String(detailValue(detail, 'importance') || 0)].filter(Boolean).join(' · ');
      return '<article class="ob-memory-care__event" data-event-kind="bucket">' +
        '<div class="ob-memory-care__card-heading"><div><h5>' +
          ui.escape(detailValue(detail, 'name') || id || '记忆事件') + '</h5><p>' + ui.escape(meta) + '</p></div>' +
        '<button type="button" class="ob-memory-care__quiet" data-action="open-bucket" data-id="' +
          ui.escapeAttr(id) + '">在记忆桶中打开</button></div>' +
        '<p class="ob-memory-care__event-text">' + ui.escape(clipText(detail.content, 800)) + '</p></article>';
    }

    function renderRawEvent(event) {
      var eventId = String(event.source_event_id || event.id || '');
      var meta = [event.role, event.source, formatDateTime(event.created_at)].filter(Boolean).join(' · ');
      return '<article class="ob-memory-care__event" data-event-kind="raw">' +
        '<div class="ob-memory-care__card-heading"><div><h5>' + ui.escape(eventId || '原始事件') +
        '</h5><p>' + ui.escape(meta) + '</p></div><span class="ob-memory-care__badge">raw</span></div>' +
        '<p class="ob-memory-care__event-text">' + ui.escape(clipText(event.text, 800)) + '</p></article>';
    }

    async function loadRawEvents(date, requestId, viewToken, allItems) {
      var rawState = ensureRawEventState(date);
      if (rawState.started || rawState.status !== 'loading') return;
      rawState.started = true;
      try {
        var since = encodeURIComponent(date + 'T00:00:00');
        var until = encodeURIComponent(date + 'T23:59:59.999999');
        var data = await api.get('/api/search-raw?since=' + since + '&until=' + until +
          '&limit=' + state.rawEventLimit);
        if (!state.active || requestId !== state.requestId) return;
        rawState.items = limitedResponseArray(data, 'items', state.rawEventLimit, '当天原始事件');
        rawState.status = 'loaded';
      } catch (error) {
        if (!state.active || requestId !== state.requestId) return;
        rawState.items = [];
        rawState.status = 'error';
        rawState.error = friendlyError(error, '无法读取当天原始事件');
      }
      if (state.active && requestId === state.requestId && state.selectedDate === date &&
          viewToken === state.dayViewToken) {
        renderSelectedDay(allItems || [], requestId, viewToken);
      }
    }

    async function hydrateDetails(items, requestId, viewToken, allItems) {
      var selected = state.selectedDate;
      var generation = requestId == null ? state.requestId : requestId;
      var pending = items.filter(function (item) {
        var id = String(item.id || item.bucket_id || '');
        return id && !state.details[id];
      });
      if (!pending.length) return;
      pending.forEach(function (item) {
        var id = String(item.id || item.bucket_id || '');
        state.details[id] = { id: id, __loading: true };
      });
      var nextIndex = 0;

      function isCurrentView() {
        return state.active && generation === state.requestId &&
          state.selectedDate === selected && viewToken === state.dayViewToken;
      }

      async function hydrateWorker() {
        while (isCurrentView()) {
          var index = nextIndex;
          nextIndex += 1;
          if (index >= pending.length) return;
          var item = pending[index];
          var id = String(item.id || item.bucket_id || '');
          if (!id) continue;
          if (!isCurrentView()) return;
          var detail;
          try {
            detail = await api.get('/api/bucket/' + encodeURIComponent(id));
          } catch (error) {
            detail = {
              __error: true,
              error: friendlyError(error, '正文暂时无法读取'),
              content: item.content_preview || '正文暂时无法读取。',
            };
          }
          if (!isCurrentView()) {
            if (state.details[id] && state.details[id].__loading) delete state.details[id];
            return;
          }
          state.details[id] = detail;
        }
      }

      var workerCount = Math.min(state.detailConcurrency, pending.length);
      var workers = [];
      for (var worker = 0; worker < workerCount; worker += 1) workers.push(hydrateWorker());
      await Promise.all(workers);
      if (isCurrentView()) {
        renderSelectedDay(allItems || items, generation, viewToken);
      } else {
        pending.forEach(function (item) {
          var id = String(item.id || item.bucket_id || '');
          if (state.details[id] && state.details[id].__loading) delete state.details[id];
        });
      }
    }

    async function hydrateSourceDetails(items, requestId, viewToken, allItems) {
      var selected = state.selectedDate;
      var ids = sourceIdsForImpressions(items).slice(0, state.sourceDetailLimit);
      var pending = ids.filter(function (id) { return !state.sourceDetails[id]; });
      if (!pending.length) return;
      pending.forEach(function (id) { state.sourceDetails[id] = { id: id, __loading: true }; });
      var nextIndex = 0;

      function isCurrentView() {
        return state.active && requestId === state.requestId && state.selectedDate === selected &&
          viewToken === state.dayViewToken;
      }

      async function hydrateWorker() {
        while (nextIndex < pending.length) {
          var index = nextIndex;
          nextIndex += 1;
          var id = pending[index];
          try {
            var detail = await api.get('/api/bucket/' + encodeURIComponent(id));
            if (!isCurrentView()) {
              delete state.sourceDetails[id];
              return;
            }
            state.sourceDetails[id] = Object.assign({ id: id }, detail || {});
          } catch (error) {
            if (!isCurrentView()) {
              delete state.sourceDetails[id];
              return;
            }
            state.sourceDetails[id] = {
              id: id,
              __error: true,
              error: friendlyError(error, '参考记忆桶暂时无法读取'),
            };
          }
        }
      }

      var workerCount = Math.min(state.sourceDetailConcurrency, pending.length);
      var workers = [];
      for (var worker = 0; worker < workerCount; worker += 1) workers.push(hydrateWorker());
      await Promise.all(workers);
      if (isCurrentView()) {
        renderSelectedDay(allItems || items, requestId, viewToken);
      } else {
        pending.forEach(function (id) {
          if (state.sourceDetails[id] && state.sourceDetails[id].__loading) {
            delete state.sourceDetails[id];
          }
        });
      }
    }

    async function runReflection(button) {
      var operation = reserveReflectionOperation();
      if (!operation) return;
      var forceInput = state.root.querySelector('[name="reflection_force"]');
      var force = Boolean(forceInput && forceInput.checked);
      try {
        var confirmed = await ui.confirmAction(
          '运行每日 Reflection？',
          force ? '已选择强制运行，可能替换同周期的整理结果。' : '这会根据近期材料生成或更新日印象。'
        );
        if (!confirmed || !validReflectionOperation(operation)) return;
        await runOperation(button, '/api/reflection/run', { period: 'daily', force: force }, 'Reflection', true, operation);
      } catch (error) {
        if (validReflectionOperation(operation)) {
          ui.setStatus(state.root.querySelector('[data-role="reflection-operation-status"]'),
            'Reflection 未运行：' + friendlyError(error, '确认未完成'), 'error');
        }
      } finally {
        releaseReflectionOperation(operation);
      }
    }

    async function runActivity(button) {
      var date = inputValue(state.root, 'activity_date');
      var forceInput = state.root.querySelector('[name="activity_force"]');
      var force = Boolean(forceInput && forceInput.checked);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
        ui.setStatus(state.root.querySelector('[data-role="reflection-operation-status"]'), '请选择有效日期。', 'error');
        return;
      }
      var operation = reserveReflectionOperation();
      if (!operation) return;
      try {
        if (!await ui.confirmAction(
          force ? '强制重新生成活动摘要？' : '生成每日活动摘要？',
          date + (force ? ' · 已选择强制运行。' : '')
        ) || !validReflectionOperation(operation)) return;
        await runOperation(button, '/api/daily-activity-summary/run', { date: date, force: force }, '活动摘要', false, operation);
      } catch (error) {
        if (validReflectionOperation(operation)) {
          ui.setStatus(state.root.querySelector('[data-role="reflection-operation-status"]'),
            '活动摘要未运行：' + friendlyError(error, '确认未完成'), 'error');
        }
      } finally {
        releaseReflectionOperation(operation);
      }
    }

    function reserveReflectionOperation() {
      if (state.activeOperation) return 0;
      var token = ++state.operationToken;
      state.activeOperation = token;
      lockControls(state.root,
        '[data-action="run-reflection"], [data-action="run-activity"], [name="reflection_force"], [name="activity_date"], [name="activity_force"]',
        true);
      return token;
    }

    function validReflectionOperation(token) {
      return Boolean(token && state.active && state.activeOperation === token);
    }

    function releaseReflectionOperation(token) {
      if (!token || state.activeOperation !== token) return;
      state.activeOperation = 0;
      lockControls(state.root,
        '[data-action="run-reflection"], [data-action="run-activity"], [name="reflection_force"], [name="activity_date"], [name="activity_force"]',
        false);
    }

    async function runOperation(button, route, payload, label, refresh, operation) {
      var status = state.root.querySelector('[data-role="reflection-operation-status"]');
      try {
        setBusy(button, true, '运行中');
        ui.setStatus(status, label + ' 正在运行', 'neutral');
        var data = await api.post(route, payload);
        if (!validReflectionOperation(operation)) return;
        ui.setStatus(status, label + ' 完成 · ' + summarizeResult(data), 'success');
        emit(app, 'buckets:invalidate', { source: 'memory-reflection' });
        if (refresh) {
          await loadHistory();
        }
      } catch (error) {
        if (validReflectionOperation(operation)) {
          ui.setStatus(status, label + ' 失败：' + friendlyError(error, '操作未完成'), 'error');
        }
      } finally {
        if (state.activeOperation === operation) setBusy(button, false);
      }
    }

    function openBucket(id) {
      if (!id) return;
      if (app.commands && typeof app.commands.openBucket === 'function') {
        app.commands.openBucket(id);
      } else if (app.router && typeof app.router.go === 'function') {
        app.router.go('shared', 'shared-buckets', { bucketId: id });
      } else {
        emit(app, 'bucket:open', { id: id });
      }
    }

    return {
      id: 'memory-reflection',
      workspace: 'memory',
      label: '日印象',
      order: 20,
      mount: mount,
      activate: function () { state.active = true; return loadHistory(); },
      deactivate: function () {
        state.active = false;
        state.requestId += 1;
        state.operationToken += 1;
        state.activeOperation = 0;
        lockControls(state.root,
          '[data-action="run-reflection"], [data-action="run-activity"], [name="reflection_force"], [name="activity_date"], [name="activity_force"]',
          false);
      },
    };
  }

  function createChatMemoryPanel(app, api, ui) {
    var state = {
      root: null,
      active: false,
      requestId: 0,
      status: 'pending',
      items: [],
      decisionToken: 0,
      inFlight: new Map(),
      runToken: 0,
      activeRun: 0,
    };

    function mount(root) {
      state.root = root;
      root.innerHTML = '<section class="ob-memory-care" data-memory-care="chat-memory">' +
        '<header class="ob-memory-care__header"><div><p class="ob-memory-care__eyebrow">Automatic memory</p>' +
        '<h2>自动记忆</h2><p>从原始对话中提取长期候选；review 模式下可编辑后再写入。</p></div></header>' +
        '<section class="ob-memory-care__run-card ob-memory-care__run-card--wide"><div class="ob-memory-care__inline-form">' +
          '<label class="ob-memory-care__field"><span>日期</span><input type="date" name="chat_memory_date" value="' + todayLocal() + '"></label>' +
          '<label class="ob-memory-care__field"><span>模式</span><select name="chat_memory_mode"><option value="review">review · 先确认</option><option value="auto">auto · 自动写入</option></select></label>' +
          '<label class="ob-memory-care__check"><input type="checkbox" name="chat_memory_force"> 强制重新运行</label>' +
          '<button type="button" class="ob-memory-care__primary" data-action="run-chat-memory">运行自动记忆</button>' +
        '</div><div class="ob-memory-care__operation-status" data-role="chat-memory-operation-status" aria-live="polite"></div></section>' +
        '<div class="ob-memory-care__toolbar"><label class="ob-memory-care__field ob-memory-care__field--compact"><span>候选状态</span>' +
          '<select name="candidate_status" data-action="filter-candidates"><option value="pending">待确认</option><option value="confirmed">已写入</option><option value="rejected">已拒绝</option><option value="all">全部</option></select></label>' +
          '<button type="button" class="ob-memory-care__quiet" data-action="refresh-candidates">刷新</button>' +
          '<span class="ob-memory-care__toolbar-status" data-role="candidate-count" aria-live="polite"></span></div>' +
        '<div class="ob-memory-care__list" data-role="candidate-list">' + ui.loading('读取记忆候选') + '</div>' +
      '</section>';

      listen(root, 'click', '[data-action]', onClick);
      listen(root, 'change', '[data-action="filter-candidates"]', function (_event, select) {
        state.status = select.value || 'pending';
        loadCandidates();
      });
    }

    function onClick(_event, button) {
      var action = button.dataset.action;
      if (action === 'run-chat-memory') {
        return runChatMemory(button);
      } else if (action === 'refresh-candidates') {
        return loadCandidates();
      } else if (action === 'toggle-candidate-edit') {
        toggleEdit(button);
      } else if (action === 'confirm-candidate') {
        return decideCandidate(button, 'confirm');
      } else if (action === 'reject-candidate') {
        return decideCandidate(button, 'reject');
      } else if (action === 'retry' && button.dataset.retry === 'chat-memory') {
        return loadCandidates();
      }
    }

    async function loadCandidates() {
      if (!state.root) return;
      var requestId = ++state.requestId;
      var list = state.root.querySelector('[data-role="candidate-list"]');
      list.innerHTML = ui.loading('读取记忆候选');
      try {
        var data = await api.get('/api/daily-chat-memory/pending?status=' + encodeURIComponent(state.status) + '&limit=100');
        if (!state.active || requestId !== state.requestId) return;
        state.items = limitedResponseArray(data, 'items', 100, '记忆候选');
        renderCandidates();
      } catch (error) {
        if (!state.active || requestId !== state.requestId) return;
        list.innerHTML = ui.error('候选读取失败：' + friendlyError(error, '无法读取候选'), 'chat-memory');
      }
    }

    function renderCandidates() {
      var list = state.root.querySelector('[data-role="candidate-list"]');
      ui.setStatus(state.root.querySelector('[data-role="candidate-count"]'), state.items.length + ' 条', 'neutral');
      if (!state.items.length) {
        list.innerHTML = ui.empty(state.status === 'pending' ? '暂无待确认候选。' : '这个状态下暂无候选。');
        return;
      }
      list.innerHTML = state.items.map(renderCandidate).join('');
    }

    function listText(value) {
      return Array.isArray(value) ? value.join(', ') : String(value || '');
    }

    function renderCandidate(item) {
      var candidate = item && item.candidate && typeof item.candidate === 'object' ? item.candidate : {};
      var id = String(item.id || candidate.id || '');
      var status = String(item.status || 'pending');
      var editable = status === 'pending';
      var meta = [candidate.kind || 'memory', item.date || candidate.date || '', 'confidence ' + (candidate.confidence == null ? '—' : candidate.confidence)]
        .filter(Boolean).join(' · ');
      var editor = editable ? '<div class="ob-memory-care__candidate-editor" data-role="candidate-editor" hidden>' +
        '<div class="ob-memory-care__form-grid">' +
          field('标题', '<input name="candidate_title" maxlength="40" value="' + ui.escapeAttr(candidate.title || id) + '">') +
          field('类型', '<input name="candidate_kind" maxlength="40" value="' + ui.escapeAttr(candidate.kind || 'key_event') + '">') +
          field('正文', '<textarea name="candidate_content" maxlength="1200" rows="5">' + ui.escape(candidate.content || '') + '</textarea>', true) +
          field('域', '<input name="candidate_domain" maxlength="200" value="' + ui.escapeAttr(listText(candidate.domain)) + '" placeholder="逗号分隔">') +
          field('标签', '<input name="candidate_tags" maxlength="320" value="' + ui.escapeAttr(listText(candidate.tags)) + '" placeholder="逗号分隔">') +
          field('重要度', '<input name="candidate_importance" type="number" min="1" max="10" value="' + ui.escapeAttr(candidate.importance == null ? '' : candidate.importance) + '">') +
          field('置信度', '<input name="candidate_confidence" type="number" min="0" max="1" step="0.01" value="' + ui.escapeAttr(candidate.confidence == null ? '' : candidate.confidence) + '">') +
        '</div></div>' : '';
      var actions = editable
        ? '<button type="button" data-action="toggle-candidate-edit">编辑</button>' +
          '<button type="button" class="ob-memory-care__primary" data-action="confirm-candidate">写入</button>' +
          '<button type="button" class="is-danger" data-action="reject-candidate">拒绝</button>'
        : '<span class="ob-memory-care__muted">' + ui.escape(candidateDecisionLabel(status)) + '</span>';
      return '<article class="ob-memory-care__card" data-candidate-id="' + ui.escapeAttr(id) + '" data-editing="false">' +
        '<div class="ob-memory-care__card-heading"><div><h3>' + ui.escape(candidate.title || id || '记忆候选') + '</h3><p>' + ui.escape(meta) + '</p></div>' +
        '<span class="ob-memory-care__badge" data-status="' + ui.escapeAttr(status) + '">' + ui.escape(candidateDecisionLabel(status)) + '</span></div>' +
        '<div class="ob-memory-care__body-text">' + ui.escape(candidate.content || '') + '</div>' + editor +
        '<div class="ob-memory-care__actions">' + actions + '</div></article>';
    }

    function candidateDecisionLabel(status) {
      return { pending: '待确认', confirmed: '已写入', rejected: '已拒绝' }[status] || status;
    }

    function toggleEdit(button) {
      var card = button.closest('[data-candidate-id]');
      var editor = card && card.querySelector('[data-role="candidate-editor"]');
      if (!editor) return;
      editor.hidden = !editor.hidden;
      card.dataset.editing = editor.hidden ? 'false' : 'true';
      button.textContent = editor.hidden ? '编辑' : '收起编辑';
    }

    function readCandidateEdits(card) {
      var title = inputValue(card, 'candidate_title');
      var content = inputValue(card, 'candidate_content');
      if (!title) throw new Error('标题不能为空');
      if (!content) throw new Error('正文不能为空');
      var importanceRaw = inputValue(card, 'candidate_importance');
      var confidenceRaw = inputValue(card, 'candidate_confidence');
      var importance = importanceRaw === '' ? undefined : Number(importanceRaw);
      var confidence = confidenceRaw === '' ? undefined : Number(confidenceRaw);
      if (importance !== undefined && (!Number.isFinite(importance) || importance < 1 || importance > 10)) {
        throw new Error('importance must be between 1 and 10');
      }
      if (confidence !== undefined && (!Number.isFinite(confidence) || confidence < 0 || confidence > 1)) {
        throw new Error('confidence must be between 0 and 1');
      }
      var edits = {
        title: title,
        content: content,
        kind: inputValue(card, 'candidate_kind'),
        domain: inputValue(card, 'candidate_domain'),
        tags: inputValue(card, 'candidate_tags'),
      };
      if (importance !== undefined) edits.importance = importance;
      if (confidence !== undefined) edits.confidence = confidence;
      return edits;
    }

    async function decideCandidate(button, action) {
      var card = button.closest('[data-candidate-id]');
      var id = card ? String(card.dataset.candidateId || '') : '';
      if (!id) return;
      if (state.activeRun || state.inFlight.has(id)) return;
      var token = ++state.decisionToken;
      state.inFlight.set(id, token);
      lockCandidateCard(card, true);
      if (state.inFlight.size === 1) lockChatRunControls(true);
      var reject = action === 'reject';
      var status = state.root.querySelector('[data-role="chat-memory-operation-status"]');
      try {
        var confirmed = await ui.confirmAction(
          reject ? '拒绝这条记忆候选？' : '把这条候选写入长期记忆？',
          reject ? '拒绝后它不会写入记忆桶。' : '写入会创建一条长期记忆；编辑内容会一并保存。'
        );
        if (!confirmed || !state.active || state.inFlight.get(id) !== token) return;
        setBusy(button, true, reject ? '拒绝中' : '写入中');
        var payload = {
          candidate_ids: [id],
          action: reject ? 'reject' : 'confirm',
          confirm: reject ? 'REJECT' : 'WRITE',
        };
        if (!reject && card.dataset.editing === 'true') {
          payload.edits = Object.create(null);
          payload.edits[id] = readCandidateEdits(card);
        }
        var data = await api.post('/api/daily-chat-memory/confirm', payload);
        if (!state.active || state.inFlight.get(id) !== token) return;
        ui.setStatus(status, (reject ? '候选已拒绝' : '候选已写入') + ' · ' + summarizeResult(data), 'success');
        if (!reject) emit(app, 'buckets:invalidate', { source: 'memory-chat-memory' });
        await loadCandidates();
      } catch (error) {
        if (state.active) {
          ui.setStatus(status, '操作失败：' + friendlyError(error, '候选操作未完成'), 'error');
        }
      } finally {
        if (state.inFlight.get(id) === token) {
          state.inFlight.delete(id);
          lockCandidateCard(card, false);
          setBusy(button, false);
          if (!state.inFlight.size && !state.activeRun) lockChatRunControls(false);
        }
      }
    }

    function lockCandidateCard(card, locked) {
      if (!card) return;
      card.dataset.busy = locked ? 'true' : 'false';
      lockControls(card, 'button, input, select, textarea', locked);
    }

    function lockChatRunControls(locked) {
      lockControls(state.root,
        '[data-action="run-chat-memory"], [name="chat_memory_date"], [name="chat_memory_mode"], [name="chat_memory_force"]',
        locked);
    }

    function lockAllCandidateControls(locked) {
      lockControls(state.root,
        '[data-candidate-id] button, [data-candidate-id] input, [data-candidate-id] select, [data-candidate-id] textarea',
        locked);
    }

    async function runChatMemory(button) {
      var date = inputValue(state.root, 'chat_memory_date');
      var mode = inputValue(state.root, 'chat_memory_mode') || 'review';
      var forceInput = state.root.querySelector('[name="chat_memory_force"]');
      var force = Boolean(forceInput && forceInput.checked);
      var status = state.root.querySelector('[data-role="chat-memory-operation-status"]');
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
        ui.setStatus(status, '请选择有效日期。', 'error');
        return;
      }
      var detail = mode === 'auto'
        ? 'auto 模式会直接写入合格候选。'
        : 'review 模式只生成待确认候选。';
      if (force) detail += ' 已选择强制重新运行。';
      var token = reserveChatMemoryRun();
      if (!token) return;
      try {
        if (!await ui.confirmAction('运行 ' + date + ' 的自动记忆？', detail) ||
            !validChatMemoryRun(token)) return;
        setBusy(button, true, '运行中');
        ui.setStatus(status, '自动记忆正在运行', 'neutral');
        var data = await api.post('/api/daily-chat-memory/run', { date: date, mode: mode, force: force });
        if (!validChatMemoryRun(token)) return;
        ui.setStatus(status, '自动记忆完成 · ' + summarizeResult(data), 'success');
        emit(app, 'buckets:invalidate', { source: 'memory-chat-memory-run' });
        state.status = 'pending';
        var select = state.root.querySelector('[name="candidate_status"]');
        if (select) select.value = 'pending';
        await loadCandidates();
      } catch (error) {
        if (state.active) {
          ui.setStatus(status, '自动记忆失败：' + friendlyError(error, '自动记忆未完成'), 'error');
        }
      } finally {
        if (state.activeRun === token) setBusy(button, false);
        releaseChatMemoryRun(token);
      }
    }

    function reserveChatMemoryRun() {
      if (state.activeRun || state.inFlight.size) return 0;
      var token = ++state.runToken;
      state.activeRun = token;
      lockChatRunControls(true);
      lockAllCandidateControls(true);
      return token;
    }

    function validChatMemoryRun(token) {
      return Boolean(token && state.active && state.activeRun === token);
    }

    function releaseChatMemoryRun(token) {
      if (!token || state.activeRun !== token) return;
      state.activeRun = 0;
      lockChatRunControls(false);
      lockAllCandidateControls(false);
    }

    return {
      id: 'memory-chat-memory',
      workspace: 'memory',
      label: '自动记忆',
      order: 30,
      mount: mount,
      activate: function () { state.active = true; return loadCandidates(); },
      deactivate: function () {
        state.active = false;
        state.requestId += 1;
        state.decisionToken += 1;
        state.inFlight.clear();
        lockAllCandidateControls(false);
        state.runToken += 1;
        state.activeRun = 0;
        lockChatRunControls(false);
      },
    };
  }

  function createDreamsPanel(_app, api, ui) {
    var state = {
      root: null,
      active: false,
      requestId: 0,
      records: [],
      bodies: Object.create(null),
    };

    function mount(root) {
      state.root = root;
      root.innerHTML = '<section class="ob-memory-care" data-memory-care="dreams">' +
        '<header class="ob-memory-care__header"><div><p class="ob-memory-care__eyebrow">Night dream</p>' +
        '<h2>梦境时间线</h2><p>后台夜梦只展示已经生成的记录；正文按需打开，不会在列表里预加载。</p></div>' +
        '<button type="button" class="ob-memory-care__quiet" data-action="refresh-dreams">刷新</button></header>' +
        '<div class="ob-memory-care__timeline" data-role="dream-list">' + ui.loading('读取梦境记录') + '</div>' +
      '</section>';
      listen(root, 'click', '[data-action]', onClick);
    }

    function onClick(_event, button) {
      if (button.dataset.action === 'refresh-dreams') {
        return loadDreams();
      } else if (button.dataset.action === 'toggle-dream') {
        return toggleDream(button);
      } else if (button.dataset.action === 'retry' && button.dataset.retry === 'dreams') {
        return loadDreams();
      }
    }

    async function loadDreams() {
      if (!state.root) return;
      var requestId = ++state.requestId;
      state.bodies = Object.create(null);
      state.records = [];
      var list = state.root.querySelector('[data-role="dream-list"]');
      list.innerHTML = ui.loading('读取梦境记录');
      try {
        var data = await api.get('/api/dreams?limit=50');
        if (!state.active || requestId !== state.requestId) return;
        state.records = limitedResponseArray(data, 'records', 50, '梦境时间线');
        if (!state.records.length) {
          list.innerHTML = ui.empty('还没有梦。');
          return;
        }
        list.innerHTML = state.records.map(renderDream).join('');
      } catch (error) {
        if (!state.active || requestId !== state.requestId) return;
        list.innerHTML = ui.error('梦境读取失败：' + friendlyError(error, '无法读取梦境'), 'dreams');
      }
    }

    function dreamDate(record) {
      var local = String(record.local_date || '');
      if (/^\d{4}-\d{2}-\d{2}$/.test(local)) return local;
      return formatDateTime(record.generated_at) || '未知日期';
    }

    function dreamStatus(status) {
      return { surfaced: '已浮现', forgotten: '已遗忘', latent: '潜伏中' }[status] || '潜伏中';
    }

    function hasCurrentDream(id) {
      return state.records.some(function (record) {
        return String(record.dream_id || record.id || '') === id && Boolean(record.has_body);
      });
    }

    function renderDream(record) {
      var id = String(record.dream_id || record.id || '');
      var title = dreamDate(record) + ' · ' + (record.ai_name || 'AI') + ' 做了一个梦';
      if (!record.has_body || !id) {
        return '<article class="ob-memory-care__timeline-item is-static"><span class="ob-memory-care__timeline-dot"></span>' +
          '<div><h3>' + ui.escape(title) + '</h3><p>' + ui.escape(dreamStatus(record.status)) + '</p></div></article>';
      }
      return '<article class="ob-memory-care__timeline-item" data-dream-id="' + ui.escapeAttr(id) + '">' +
        '<span class="ob-memory-care__timeline-dot"></span><button type="button" class="ob-memory-care__dream-toggle" data-action="toggle-dream" aria-expanded="false">' +
        '<span><strong>' + ui.escape(title) + '</strong><small>' + ui.escape(dreamStatus(record.status)) + '</small></span><i aria-hidden="true">⌄</i></button>' +
        '<div class="ob-memory-care__dream-body" data-role="dream-body" hidden></div></article>';
    }

    async function toggleDream(button) {
      var row = button.closest('[data-dream-id]');
      var body = row && row.querySelector('[data-role="dream-body"]');
      if (!row || !body) return;
      var expanded = button.getAttribute('aria-expanded') === 'true';
      button.setAttribute('aria-expanded', expanded ? 'false' : 'true');
      body.hidden = expanded;
      row.classList.toggle('is-open', !expanded);
      if (expanded) return;
      var id = String(row.dataset.dreamId || '');
      var generation = state.requestId;
      if (!id || !hasCurrentDream(id)) return;
      if (state.bodies[id]) {
        body.textContent = state.bodies[id];
        return;
      }
      body.innerHTML = ui.loading('正在翻开梦境');
      try {
        var data = await api.get('/api/dreams/' + encodeURIComponent(id));
        var text = String(data.body || data.content || '梦境正文暂时不可用。');
        if (!state.active || generation !== state.requestId || !hasCurrentDream(id)) return;
        state.bodies[id] = text;
        body.textContent = text;
      } catch (error) {
        if (state.active && generation === state.requestId && hasCurrentDream(id)) {
          body.innerHTML = ui.error('梦境暂时打不开：' + friendlyError(error, '正文不可用'), 'dreams');
        }
      }
    }

    return {
      id: 'memory-dreams',
      workspace: 'memory',
      label: '梦境',
      order: 40,
      mount: mount,
      activate: function () { state.active = true; return loadDreams(); },
      deactivate: function () {
        state.active = false;
        state.requestId += 1;
        state.bodies = Object.create(null);
      },
    };
  }

  function createDarkroomPanel(_app, api, ui) {
    var state = { root: null, active: false, requestId: 0 };

    function mount(root) {
      state.root = root;
      root.innerHTML = '<section class="ob-memory-care" data-memory-care="darkroom">' +
        '<header class="ob-memory-care__header"><div><p class="ob-memory-care__eyebrow">Private reflection</p>' +
        '<h2>暗房门口</h2><p>这里只显示门口状态，不读取、回显或缓存暗房正文。</p></div>' +
        '<button type="button" class="ob-memory-care__quiet" data-action="refresh-darkroom">刷新</button></header>' +
        '<div data-role="darkroom-status">' + ui.loading('查看暗房门口') + '</div>' +
      '</section>';
      listen(root, 'click', '[data-action]', function (_event, button) {
        if (button.dataset.action === 'refresh-darkroom' ||
            (button.dataset.action === 'retry' && button.dataset.retry === 'darkroom')) {
          loadStatus();
        }
      });
    }

    async function loadStatus() {
      if (!state.root) return;
      var requestId = ++state.requestId;
      var target = state.root.querySelector('[data-role="darkroom-status"]');
      target.innerHTML = ui.loading('查看暗房门口');
      try {
        var data = await api.get('/api/darkroom/status');
        if (!state.active || requestId !== state.requestId) return;
        target.innerHTML = renderStatus(data || {});
      } catch (error) {
        if (!state.active || requestId !== state.requestId) return;
        target.innerHTML = ui.error('暗房状态读取失败：' + friendlyError(error, '无法读取暗房门口'), 'darkroom');
      }
    }

    function renderStatus(data) {
      var count = Number(data.count || 0);
      var tags = Array.isArray(data.last_tags) ? data.last_tags : [];
      return '<article class="ob-memory-care__darkroom-door">' +
        '<div class="ob-memory-care__door-mark" aria-hidden="true">◑</div><div class="ob-memory-care__door-copy">' +
        '<div class="ob-memory-care__card-heading"><div><h3>' + ui.escape(data.door || 'Darkroom Door') + '</h3>' +
        '<p>' + count + ' 个 active 房间</p></div><span class="ob-memory-care__badge" data-status="' + (count ? 'active' : 'empty') + '">' +
        (count ? '有未显影内容' : '门口安静') + '</span></div>' +
        '<dl class="ob-memory-care__status-grid">' +
          statusPair('最近进入', formatDateTime(data.last_entered_at) || '—') +
          statusPair('最近显影', formatDateTime(data.last_release_at) || '—') +
          statusPair('已显影次数', String(Number(data.released_count || 0))) +
          statusPair('最近 mood', data.last_mood || '—') +
          statusPair('最近 tags', tags.length ? tags.join(', ') : '—') +
          statusPair('状态更新时间', formatDateTime(data.updated_at) || '—') +
        '</dl></div></article>';
    }

    function statusPair(label, value) {
      return '<div><dt>' + ui.escape(label) + '</dt><dd>' + ui.escape(value) + '</dd></div>';
    }

    return {
      id: 'memory-darkroom',
      workspace: 'memory',
      label: '暗房门口',
      order: 50,
      mount: mount,
      activate: function () { state.active = true; return loadStatus(); },
      deactivate: function () { state.active = false; state.requestId += 1; },
    };
  }
})();
