(function initSharedBucketStudio(global) {
  'use strict';

  var factories = global.OmbreDashboardFeatureFactories =
    global.OmbreDashboardFeatureFactories || [];

  var MAX_LIST_RESULTS = 200;
  var MAX_RAW_RESULTS = 50;
  var MAX_EDGE_RESULTS = 200;
  var MAX_CONTENT_LENGTH = 100000;
  var MAX_COMMENT_LENGTH = 4000;
  var MAX_QUERY_LENGTH = 512;
  var MAX_TOKEN_LENGTH = 4096;

  factories.push(function sharedBucketStudioFactory(app) {
    if (!app || typeof app.registerPanel !== 'function' || !app.api) {
      throw new Error('The unified Dashboard API and panel registry are required.');
    }

    ensureStyles(app);
    var state = null;

    app.registerPanel({
      id: 'shared-bucket-studio',
      workspace: 'shared',
      label: 'Bucket Studio',
      order: 15,
      mount: function mount(root) {
        if (!root) return;
        state = createState(app, root);
        root.classList.add('shared-bucket-studio');
        root.setAttribute('data-panel', 'shared-bucket-studio');
        root.innerHTML = panelMarkup();
        bindEvents(state);
        clearBucketDetailState(state);
      },
      activate: function activate(context) {
        if (!state) return undefined;
        state.active = true;
        var params = context && context.state && context.state.params || {};
        var bucketId = cleanText(params.bucket_id || params.bucketId, 160);
        var jobs = [
          loadBucketList(state, 'light'),
          loadDomainTaxonomy(state),
          loadEdges(state),
        ];
        if (bucketId) jobs.push(loadBucketDetail(state, bucketId));
        return Promise.all(jobs);
      },
      deactivate: function deactivate() {
        if (!state) return;
        state.active = false;
        invalidateRequests(state);
      },
    });
  });

  function ensureStyles(app) {
    if (typeof document === 'undefined' || !document.head) return;
    if (document.getElementById('ombre-shared-bucket-studio-styles')) return;
    var link = document.createElement('link');
    link.id = 'ombre-shared-bucket-studio-styles';
    link.rel = 'stylesheet';
    link.href = typeof app.assetUrl === 'function'
      ? app.assetUrl('shared-bucket-studio.css')
      : new URL('./dashboard-assets/shared-bucket-studio.css', document.baseURI).toString();
    document.head.appendChild(link);
  }

  function createState(app, root) {
    return {
      app: app,
      root: root,
      active: false,
      listMode: 'light',
      buckets: [],
      selectedIds: new Set(),
      selectedBucketId: '',
      detail: null,
      domains: [],
      requests: Object.create(null),
      writes: new Set(),
    };
  }

  function panelMarkup() {
    return '' +
      '<header class="shared-bucket-studio__hero">' +
        '<div><p class="shared-bucket-studio__eyebrow">Shared workspace · advanced operations</p>' +
        '<h1>Bucket Studio</h1>' +
        '<p>The canonical advanced surface for current/Ying bucket, raw-event, edge, and taxonomy operations. ' +
        'The original Buckets panel remains the fast everyday view.</p></div>' +
        '<button type="button" class="secondary" data-action="open-basic-buckets">Open basic Buckets</button>' +
      '</header>' +
      '<div class="shared-bucket-studio__status" data-role="global-status" aria-live="polite"></div>' +

      '<section class="shared-bucket-studio__section" aria-labelledby="bucket-browser-title">' +
        '<div class="shared-bucket-studio__section-head"><div><h2 id="bucket-browser-title">Bucket browser</h2>' +
        '<p>Light list is compact. Full list includes the richer P0 summary. Results are capped at 200.</p></div>' +
        '<label class="shared-bucket-studio__check"><input type="checkbox" data-role="include-archive" /> Include archive</label></div>' +
        '<div class="shared-bucket-studio__toolbar">' +
          '<button type="button" data-action="load-light">Light list</button>' +
          '<button type="button" class="secondary" data-action="load-full">Full list</button>' +
          '<form data-submit="bucket-search" class="shared-bucket-studio__inline-form">' +
            '<label><span>Search buckets</span><input name="q" maxlength="512" autocomplete="off" placeholder="name, content, or bucket id" required /></label>' +
            '<button type="submit">Search</button>' +
          '</form>' +
        '</div>' +
        '<div class="shared-bucket-studio__meta" data-role="list-status" aria-live="polite"></div>' +
        '<div class="shared-bucket-studio__list" data-role="bucket-list"></div>' +
      '</section>' +

      '<div class="shared-bucket-studio__grid">' +
        '<section class="shared-bucket-studio__section" aria-labelledby="create-memory-title">' +
          '<h2 id="create-memory-title">Create memory</h2>' +
          '<p class="shared-bucket-studio__hint">Creation uses the configured memory-write token. It is sent once and never stored by the Dashboard.</p>' +
          '<form data-submit="create-memory" class="shared-bucket-studio__form">' +
            '<label><span>Title</span><input name="title" maxlength="120" required /></label>' +
            '<label><span>Event date</span><input name="event_date" type="date" /></label>' +
            '<label><span>Domain</span><select name="domain" data-role="create-domain"><option value="general">general</option></select></label>' +
            '<label class="shared-bucket-studio__wide"><span>Raw Markdown</span><textarea name="content" maxlength="100000" rows="8" required></textarea></label>' +
            '<label class="shared-bucket-studio__wide"><span>Memory write token</span><input name="write_token" type="password" maxlength="4096" autocomplete="off" required /></label>' +
            '<button type="submit">Create memory</button>' +
          '</form>' +
          '<div class="shared-bucket-studio__meta" data-role="create-status" aria-live="polite"></div>' +
        '</section>' +

        '<section class="shared-bucket-studio__section" aria-labelledby="bulk-title">' +
          '<h2 id="bulk-title">Bulk operations</h2>' +
          '<p class="shared-bucket-studio__hint"><span data-role="selected-count">0 selected</span>. Every bulk mutation asks for confirmation.</p>' +
          '<form data-submit="bulk-update" class="shared-bucket-studio__form">' +
            '<label><span>Domain</span><select name="domain" data-role="bulk-domain"><option value="">Keep domain</option></select></label>' +
            '<label><span>Status</span><select name="status"><option value="">Keep status</option><option value="active">active</option><option value="archived">archived</option></select></label>' +
            '<label class="shared-bucket-studio__wide"><span>Add tags</span><input name="tags_add" maxlength="2000" placeholder="comma separated" /></label>' +
            '<label class="shared-bucket-studio__wide"><span>Remove tags</span><input name="tags_remove" maxlength="2000" placeholder="comma separated" /></label>' +
            '<div class="shared-bucket-studio__actions"><button type="submit">Apply bulk update</button>' +
            '<button type="button" class="danger" data-action="bulk-delete">Delete selected</button></div>' +
          '</form>' +
          '<div class="shared-bucket-studio__meta" data-role="bulk-status" aria-live="polite"></div>' +
        '</section>' +
      '</div>' +

      '<section class="shared-bucket-studio__section" data-role="detail-section" aria-labelledby="bucket-detail-title">' +
        '<div class="shared-bucket-studio__section-head"><div><h2 id="bucket-detail-title">Bucket detail &amp; Raw Markdown</h2>' +
        '<p data-role="detail-heading">Choose a bucket from the browser.</p></div>' +
        '<button type="button" class="secondary" data-action="refresh-moments">Refresh Integrated moments</button></div>' +
        '<div class="shared-bucket-studio__meta" data-role="detail-status" aria-live="polite"></div>' +
        '<div data-role="detail-summary" class="shared-bucket-studio__summary"></div>' +
        '<pre data-role="raw-content" class="shared-bucket-studio__raw">No bucket selected.</pre>' +
        '<form data-submit="edit-bucket" class="shared-bucket-studio__form shared-bucket-studio__edit-form">' +
          '<input name="bucket_id" type="hidden" />' +
          '<label><span>Title</span><input name="title" maxlength="120" required /></label>' +
          '<label><span>Event date</span><input name="event_date" type="date" /></label>' +
          '<label class="shared-bucket-studio__wide"><span>Raw Markdown</span><textarea name="content" maxlength="100000" rows="12" required></textarea></label>' +
          '<button type="submit">Save bucket</button>' +
        '</form>' +
        '<div class="shared-bucket-studio__detail-grid">' +
          '<div><h3>Year rings</h3><div data-role="comments" class="shared-bucket-studio__stack"></div>' +
            '<form data-submit="add-comment" class="shared-bucket-studio__form shared-bucket-studio__comment-form">' +
              '<input name="bucket_id" type="hidden" />' +
              '<label><span>Kind</span><select name="kind"><option value="comment">comment</option><option value="feel">feel</option></select></label>' +
              '<label class="shared-bucket-studio__wide"><span>Add a year ring</span><textarea name="content" maxlength="4000" rows="4" required></textarea></label>' +
              '<button type="submit">Add year ring</button>' +
            '</form>' +
          '</div>' +
          '<div><h3>Integrated moments</h3><div data-role="moments" class="shared-bucket-studio__stack"></div></div>' +
        '</div>' +
      '</section>' +

      '<div class="shared-bucket-studio__grid">' +
        '<section class="shared-bucket-studio__section" aria-labelledby="raw-ingest-title">' +
          '<h2 id="raw-ingest-title">Raw event ingest</h2>' +
          '<form data-submit="ingest-raw" class="shared-bucket-studio__form">' +
            '<label><span>Source</span><input name="source" maxlength="120" value="dashboard" /></label>' +
            '<label><span>Role</span><select name="role"><option value="user">user</option><option value="assistant">assistant</option><option value="system">system</option></select></label>' +
            '<label><span>Session ID</span><input name="session_id" maxlength="160" /></label>' +
            '<label><span>Conversation ID</span><input name="conversation_id" maxlength="160" /></label>' +
            '<label class="shared-bucket-studio__wide"><span>Raw text</span><textarea name="text" maxlength="100000" rows="7" required></textarea></label>' +
            '<button type="submit">Ingest raw event</button>' +
          '</form>' +
          '<div class="shared-bucket-studio__meta" data-role="ingest-status" aria-live="polite"></div>' +
        '</section>' +

        '<section class="shared-bucket-studio__section" aria-labelledby="raw-search-title">' +
          '<h2 id="raw-search-title">Raw event search</h2>' +
          '<form data-form="raw-search" class="shared-bucket-studio__form">' +
            '<label><span>Query</span><input name="q" maxlength="512" /></label>' +
            '<label><span>Limit</span><input name="limit" type="number" min="1" max="50" value="20" /></label>' +
            '<label><span>Source</span><input name="source" maxlength="120" /></label>' +
            '<label><span>Role</span><input name="role" maxlength="40" /></label>' +
            '<label><span>Session ID</span><input name="session_id" maxlength="160" /></label>' +
            '<div class="shared-bucket-studio__actions"><button type="button" data-action="raw-search-get">Search with GET</button>' +
            '<button type="button" class="secondary" data-action="raw-search-post">Search with POST</button></div>' +
          '</form>' +
          '<div class="shared-bucket-studio__meta" data-role="raw-search-status" aria-live="polite"></div>' +
          '<div data-role="raw-results" class="shared-bucket-studio__stack"></div>' +
        '</section>' +
      '</div>' +

      '<div class="shared-bucket-studio__grid">' +
        '<section class="shared-bucket-studio__section" aria-labelledby="edges-title">' +
          '<div class="shared-bucket-studio__section-head"><div><h2 id="edges-title">Memory edges</h2><p>Bounded to 200 rendered edges.</p></div>' +
          '<button type="button" class="secondary" data-action="refresh-edges">Refresh edges</button></div>' +
          '<div class="shared-bucket-studio__meta" data-role="edges-status" aria-live="polite"></div>' +
          '<div data-role="edges" class="shared-bucket-studio__stack"></div>' +
        '</section>' +
        '<section class="shared-bucket-studio__section" aria-labelledby="taxonomy-title">' +
          '<div class="shared-bucket-studio__section-head"><div><h2 id="taxonomy-title">Domain taxonomy</h2><p>Canonical domains used by create and bulk update.</p></div>' +
          '<button type="button" class="secondary" data-action="refresh-taxonomy">Refresh taxonomy</button></div>' +
          '<div class="shared-bucket-studio__meta" data-role="taxonomy-status" aria-live="polite"></div>' +
          '<div data-role="taxonomy" class="shared-bucket-studio__stack"></div>' +
        '</section>' +
      '</div>';
  }

  function bindEvents(state) {
    state.root.addEventListener('click', function handleClick(event) {
      var control = event.target && event.target.closest
        ? event.target.closest('[data-action]')
        : null;
      if (!control || !state.root.contains(control)) return;
      var action = control.dataset.action;
      if (action === 'open-basic-buckets') {
        state.app.router.go('shared', 'shared-buckets', {});
      } else if (action === 'load-light') {
        loadBucketList(state, 'light');
      } else if (action === 'load-full') {
        loadBucketList(state, 'full');
      } else if (action === 'open-bucket') {
        loadBucketDetail(state, cleanText(control.dataset.bucketId, 160));
      } else if (action === 'toggle-select') {
        toggleSelection(state, cleanText(control.dataset.bucketId, 160), Boolean(control.checked));
      } else if (action === 'bulk-delete') {
        bulkDelete(state);
      } else if (action === 'delete-comment') {
        deleteComment(
          state,
          cleanText(control.dataset.bucketId, 160),
          cleanText(control.dataset.commentId, 160)
        );
      } else if (action === 'refresh-moments') {
        loadMoments(state, state.selectedBucketId);
      } else if (action === 'raw-search-get') {
        searchRaw(state, 'get');
      } else if (action === 'raw-search-post') {
        searchRaw(state, 'post');
      } else if (action === 'refresh-edges') {
        loadEdges(state);
      } else if (action === 'refresh-taxonomy') {
        loadDomainTaxonomy(state);
      }
    });

    state.root.addEventListener('submit', function handleSubmit(event) {
      var form = event.target;
      if (!form || !form.matches || !form.matches('form[data-submit]')) return;
      event.preventDefault();
      var action = form.dataset.submit;
      if (action === 'bucket-search') searchBuckets(state, form);
      else if (action === 'create-memory') createMemory(state, form);
      else if (action === 'edit-bucket') editBucket(state, form);
      else if (action === 'bulk-update') bulkUpdate(state, form);
      else if (action === 'add-comment') addComment(state, form);
      else if (action === 'ingest-raw') ingestRaw(state, form);
    });
  }

  function cleanText(value, maxLength) {
    return String(value == null ? '' : value).trim().slice(0, maxLength);
  }

  function rawText(value, maxLength) {
    return String(value == null ? '' : value).slice(0, maxLength);
  }

  function formValue(form, name) {
    var control = form && form.elements && form.elements[name];
    return control ? control.value : '';
  }

  function findForm(state, selector) {
    var form = state.root.querySelector(selector);
    return form && form.elements ? form : null;
  }

  function escapeHtml(state, value) {
    if (state.app.ui && typeof state.app.ui.escape === 'function') {
      return state.app.ui.escape(value);
    }
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function setStatus(state, role, message, tone) {
    var element = state.root.querySelector('[data-role="' + role + '"]');
    if (!element) return;
    if (state.app.ui && typeof state.app.ui.setStatus === 'function') {
      state.app.ui.setStatus(element, message || '', tone || 'neutral');
    } else {
      element.textContent = message || '';
      element.dataset.tone = tone || 'neutral';
    }
  }

  function errorMessage(error) {
    return error && error.message ? error.message : String(error || 'Request failed');
  }

  async function requestJson(state, method, path, body, options) {
    var call = state.app.api[method];
    if (typeof call !== 'function') throw new Error('Dashboard API method unavailable: ' + method);
    var requestOptions = Object.assign({}, options || {});
    if (method !== 'get' && method !== 'head') requestOptions.retries = 0;
    var response = method === 'get' || method === 'head'
      ? await call.call(state.app.api, path, requestOptions)
      : await call.call(state.app.api, path, body, requestOptions);
    if (!response) throw new Error('The server returned no response.');
    if (typeof state.app.api.readJson === 'function') {
      return state.app.api.readJson(response);
    }
    var payload = await response.json();
    if (!response.ok) throw new Error(payload && payload.error || 'Request failed (' + response.status + ')');
    return payload;
  }

  function nextRequest(state, key) {
    state.requests[key] = (state.requests[key] || 0) + 1;
    return state.requests[key];
  }

  function requestIsCurrent(state, key, token) {
    return state.active && state.requests[key] === token;
  }

  function invalidateRequests(state) {
    Object.keys(state.requests).forEach(function invalidate(key) {
      state.requests[key] += 1;
    });
  }

  function beginWrite(state, key) {
    if (state.writes.has(key)) return false;
    state.writes.add(key);
    return true;
  }

  function finishWrite(state, key) {
    state.writes.delete(key);
  }

  async function confirmAction(state, message, detail) {
    var full = detail ? message + '\n\n' + detail : message;
    if (state.app.ui && typeof state.app.ui.confirm === 'function') {
      return Boolean(await state.app.ui.confirm(full));
    }
    return typeof global.confirm === 'function' && global.confirm(full);
  }

  function extractBuckets(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.buckets)) return payload.buckets;
    if (payload && Array.isArray(payload.results)) return payload.results;
    return [];
  }

  async function loadBucketList(state, mode) {
    var token = nextRequest(state, 'bucket-list');
    var includeArchive = state.root.querySelector('[data-role="include-archive"]');
    var archiveFlag = includeArchive && includeArchive.checked ? '1' : '0';
    var path = mode === 'full'
      ? '/api/buckets?sort=created_desc&include_archive=' + archiveFlag + '&limit=' + MAX_LIST_RESULTS
      : '/api/buckets/light?include_archive=' + archiveFlag + '&limit=' + MAX_LIST_RESULTS + '&offset=0';
    setStatus(state, 'list-status', 'Loading ' + mode + ' bucket list…', 'loading');
    try {
      var payload = await requestJson(state, 'get', path);
      if (!requestIsCurrent(state, 'bucket-list', token)) return;
      var buckets = extractBuckets(payload).slice(0, MAX_LIST_RESULTS);
      state.listMode = mode;
      state.buckets = buckets;
      state.selectedIds.clear();
      renderBucketList(state);
      var total = payload && Number.isFinite(Number(payload.count))
        ? Number(payload.count)
        : extractBuckets(payload).length;
      setStatus(
        state,
        'list-status',
        (mode === 'light' ? 'Light list' : 'Full list') + ': showing ' + buckets.length + ' of ' + total + '.',
        'ok'
      );
    } catch (error) {
      if (!requestIsCurrent(state, 'bucket-list', token)) return;
      state.buckets = [];
      renderBucketList(state);
      setStatus(state, 'list-status', 'Bucket list failed: ' + errorMessage(error), 'error');
    }
  }

  async function searchBuckets(state, form) {
    var query = cleanText(formValue(form, 'q'), MAX_QUERY_LENGTH);
    if (!query) {
      setStatus(state, 'list-status', 'Enter a bucket search query.', 'error');
      return;
    }
    var token = nextRequest(state, 'bucket-list');
    setStatus(state, 'list-status', 'Searching buckets…', 'loading');
    try {
      var payload = await requestJson(state, 'get', '/api/search?q=' + encodeURIComponent(query));
      if (!requestIsCurrent(state, 'bucket-list', token)) return;
      state.listMode = 'search';
      state.buckets = extractBuckets(payload).slice(0, MAX_LIST_RESULTS);
      state.selectedIds.clear();
      renderBucketList(state);
      setStatus(state, 'list-status', 'Search returned ' + state.buckets.length + ' bucket(s).', 'ok');
    } catch (error) {
      if (!requestIsCurrent(state, 'bucket-list', token)) return;
      setStatus(state, 'list-status', 'Bucket search failed: ' + errorMessage(error), 'error');
    }
  }

  function bucketName(bucket) {
    var metadata = bucket && bucket.metadata || {};
    return bucket && (bucket.name || metadata.name || bucket.id) || 'Unnamed bucket';
  }

  function bucketDomain(bucket) {
    var metadata = bucket && bucket.metadata || {};
    var domains = bucket && bucket.domain || metadata.domain || [];
    return Array.isArray(domains) ? domains.join(', ') : String(domains || '');
  }

  function renderBucketList(state) {
    var list = state.root.querySelector('[data-role="bucket-list"]');
    if (!list) return;
    if (!state.buckets.length) {
      list.innerHTML = '<div class="shared-bucket-studio__empty">No buckets in this result.</div>';
      updateSelectedCount(state);
      return;
    }
    list.innerHTML = state.buckets.map(function render(bucket) {
      var id = cleanText(bucket && bucket.id, 160);
      if (!id) return '';
      var checked = state.selectedIds.has(id) ? ' checked' : '';
      var preview = rawText(bucket.content_preview || bucket.content || '', 240);
      return '<article class="shared-bucket-studio__bucket">' +
        '<input type="checkbox" aria-label="Select ' + escapeHtml(state, bucketName(bucket)) + '" ' +
          'data-action="toggle-select" data-bucket-id="' + escapeHtml(state, id) + '"' + checked + ' />' +
        '<button type="button" class="shared-bucket-studio__bucket-open" data-action="open-bucket" data-bucket-id="' + escapeHtml(state, id) + '">' +
          '<strong>' + escapeHtml(state, bucketName(bucket)) + '</strong>' +
          '<span>' + escapeHtml(state, id) + '</span>' +
          '<small>' + escapeHtml(state, bucketDomain(bucket) || 'general') + '</small>' +
          (preview ? '<p>' + escapeHtml(state, preview) + '</p>' : '') +
        '</button></article>';
    }).join('');
    updateSelectedCount(state);
  }

  function toggleSelection(state, bucketId, checked) {
    if (!bucketId) return;
    if (checked) {
      if (state.selectedIds.size >= MAX_LIST_RESULTS) {
        setStatus(state, 'bulk-status', 'Selection is capped at ' + MAX_LIST_RESULTS + '.', 'error');
        renderBucketList(state);
        return;
      }
      state.selectedIds.add(bucketId);
    } else {
      state.selectedIds.delete(bucketId);
    }
    updateSelectedCount(state);
  }

  function updateSelectedCount(state) {
    var count = state.root.querySelector('[data-role="selected-count"]');
    if (count) count.textContent = state.selectedIds.size + ' selected';
  }

  function setDetailControlsDisabled(state, disabled) {
    [
      findForm(state, 'form[data-submit="edit-bucket"]'),
      findForm(state, 'form[data-submit="add-comment"]'),
    ].forEach(function update(form) {
      if (!form || typeof form.querySelectorAll !== 'function') return;
      Array.prototype.forEach.call(
        form.querySelectorAll('input, textarea, select, button'),
        function toggle(control) { control.disabled = disabled; }
      );
    });
    var refresh = state.root.querySelector('[data-action="refresh-moments"]');
    if (refresh) refresh.disabled = disabled;
  }

  function clearBucketDetailState(state) {
    state.selectedBucketId = '';
    state.detail = null;
    nextRequest(state, 'moments');

    var heading = state.root.querySelector('[data-role="detail-heading"]');
    var summary = state.root.querySelector('[data-role="detail-summary"]');
    var raw = state.root.querySelector('[data-role="raw-content"]');
    var comments = state.root.querySelector('[data-role="comments"]');
    var moments = state.root.querySelector('[data-role="moments"]');
    if (heading) heading.textContent = 'Choose a bucket from the browser.';
    if (summary) summary.innerHTML = '';
    if (raw) raw.textContent = 'No bucket selected.';
    if (comments) comments.innerHTML = '<div class="shared-bucket-studio__empty">No bucket selected.</div>';
    if (moments) moments.innerHTML = '<div class="shared-bucket-studio__empty">No bucket selected.</div>';

    var editForm = findForm(state, 'form[data-submit="edit-bucket"]');
    if (editForm) {
      editForm.elements.bucket_id.value = '';
      editForm.elements.title.value = '';
      editForm.elements.event_date.value = '';
      editForm.elements.content.value = '';
    }
    var commentForm = findForm(state, 'form[data-submit="add-comment"]');
    if (commentForm) {
      commentForm.elements.bucket_id.value = '';
      commentForm.elements.kind.value = 'comment';
      commentForm.elements.content.value = '';
    }
    setDetailControlsDisabled(state, true);
  }

  async function loadBucketDetail(state, bucketId) {
    if (!bucketId) {
      clearBucketDetailState(state);
      setStatus(state, 'detail-status', 'Choose a bucket first.', 'error');
      return;
    }
    var token = nextRequest(state, 'bucket-detail');
    clearBucketDetailState(state);
    setStatus(state, 'detail-status', 'Loading bucket detail…', 'loading');
    try {
      var payload = await requestJson(state, 'get', '/api/bucket/' + encodeURIComponent(bucketId));
      if (!requestIsCurrent(state, 'bucket-detail', token)) return;
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('Invalid bucket detail response.');
      }
      state.selectedBucketId = bucketId;
      state.detail = payload;
      renderBucketDetail(state, payload);
      setStatus(state, 'detail-status', 'Bucket detail loaded.', 'ok');
      loadMoments(state, bucketId);
    } catch (error) {
      if (!requestIsCurrent(state, 'bucket-detail', token)) return;
      setStatus(state, 'detail-status', 'Bucket detail failed: ' + errorMessage(error), 'error');
    }
  }

  function renderBucketDetail(state, bucket) {
    var metadata = bucket.metadata && typeof bucket.metadata === 'object' ? bucket.metadata : {};
    var id = cleanText(bucket.id || state.selectedBucketId, 160);
    var content = rawText(bucket.content, MAX_CONTENT_LENGTH);
    var heading = state.root.querySelector('[data-role="detail-heading"]');
    var summary = state.root.querySelector('[data-role="detail-summary"]');
    var raw = state.root.querySelector('[data-role="raw-content"]');
    if (heading) heading.textContent = (metadata.name || id) + ' · ' + id;
    if (summary) {
      var fields = [
        ['Type', metadata.type || 'dynamic'],
        ['Domain', Array.isArray(metadata.domain) ? metadata.domain.join(', ') : metadata.domain || 'general'],
        ['Tags', Array.isArray(metadata.tags) ? metadata.tags.join(', ') : metadata.tags || '—'],
        ['Event date', metadata.date || '—'],
        ['Created', metadata.created || '—'],
      ];
      summary.innerHTML = fields.map(function field(item) {
        return '<div><dt>' + escapeHtml(state, item[0]) + '</dt><dd>' + escapeHtml(state, item[1]) + '</dd></div>';
      }).join('');
    }
    if (raw) raw.textContent = content;

    var editForm = findForm(state, 'form[data-submit="edit-bucket"]');
    if (editForm) {
      editForm.elements.bucket_id.value = id;
      editForm.elements.title.value = metadata.name || id;
      editForm.elements.event_date.value = dateValue(metadata.date);
      editForm.elements.content.value = content;
    }
    var commentForm = findForm(state, 'form[data-submit="add-comment"]');
    if (commentForm) commentForm.elements.bucket_id.value = id;
    renderComments(state, id, metadata.comments);
    setDetailControlsDisabled(state, false);
  }

  function dateValue(value) {
    var match = String(value || '').match(/^\d{4}-\d{2}-\d{2}/);
    return match ? match[0] : '';
  }

  function renderComments(state, bucketId, rawComments) {
    var target = state.root.querySelector('[data-role="comments"]');
    if (!target) return;
    var comments = Array.isArray(rawComments) ? rawComments.slice(-100).reverse() : [];
    if (!comments.length) {
      target.innerHTML = '<div class="shared-bucket-studio__empty">No year rings yet.</div>';
      return;
    }
    target.innerHTML = comments.map(function render(comment) {
      var id = cleanText(comment && comment.id, 160);
      var canDelete = Boolean(id && comment && comment.source === 'dashboard');
      return '<article class="shared-bucket-studio__card"><div class="shared-bucket-studio__card-head">' +
        '<strong>' + escapeHtml(state, comment.author || comment.kind || 'comment') + '</strong>' +
        '<small>' + escapeHtml(state, comment.created || comment.original_feel_created || '') + '</small></div>' +
        '<p>' + escapeHtml(state, rawText(comment.content, MAX_COMMENT_LENGTH)) + '</p>' +
        (canDelete ? '<button type="button" class="danger secondary" data-action="delete-comment" ' +
          'data-bucket-id="' + escapeHtml(state, bucketId) + '" data-comment-id="' + escapeHtml(state, id) + '">Delete year ring</button>' : '') +
        '</article>';
    }).join('');
  }

  async function loadMoments(state, bucketId) {
    if (!bucketId) {
      setStatus(state, 'detail-status', 'Choose a bucket before loading moments.', 'error');
      return;
    }
    var token = nextRequest(state, 'moments');
    var target = state.root.querySelector('[data-role="moments"]');
    if (target) target.innerHTML = '<div class="shared-bucket-studio__empty">Loading moments…</div>';
    try {
      var payload = await requestJson(
        state,
        'get',
        '/api/moments?bucket_id=' + encodeURIComponent(bucketId) + '&limit=40'
      );
      if (!requestIsCurrent(state, 'moments', token) || bucketId !== state.selectedBucketId) return;
      renderMoments(state, payload);
    } catch (error) {
      if (!requestIsCurrent(state, 'moments', token)) return;
      if (target) target.textContent = 'Moments failed: ' + errorMessage(error);
    }
  }

  function renderMoments(state, payload) {
    var target = state.root.querySelector('[data-role="moments"]');
    if (!target) return;
    var moments = payload && Array.isArray(payload.moments) ? payload.moments.slice(0, 40) : [];
    var edges = payload && Array.isArray(payload.edges) ? payload.edges.slice(0, 40) : [];
    if (!moments.length && !edges.length) {
      target.innerHTML = '<div class="shared-bucket-studio__empty">No integrated moments.</div>';
      return;
    }
    var momentsHtml = moments.map(function render(moment) {
      return '<article class="shared-bucket-studio__card"><div class="shared-bucket-studio__card-head"><strong>' +
        escapeHtml(state, moment.moment_id || moment.section || 'moment') + '</strong><small>' +
        escapeHtml(state, moment.section || '') + '</small></div><p>' +
        escapeHtml(state, rawText(moment.text || moment.text_preview || '', 2000)) + '</p></article>';
    }).join('');
    var edgesHtml = edges.map(function render(edge) {
      return '<article class="shared-bucket-studio__card"><strong>' +
        escapeHtml(state, (edge.source || 'unknown') + ' → ' + (edge.target || 'unknown')) +
        '</strong><p>' + escapeHtml(state, edge.relation_type || edge.reason || 'related') + '</p></article>';
    }).join('');
    target.innerHTML = momentsHtml + edgesHtml;
  }

  async function createMemory(state, form) {
    var title = cleanText(formValue(form, 'title'), 120);
    var content = rawText(formValue(form, 'content'), MAX_CONTENT_LENGTH);
    var eventDate = dateValue(formValue(form, 'event_date'));
    var domain = cleanText(formValue(form, 'domain'), 80) || 'general';
    var tokenInput = form.elements && form.elements.write_token;
    var writeToken = cleanText(tokenInput && tokenInput.value, MAX_TOKEN_LENGTH);
    if (!title || !content.trim() || !writeToken) {
      setStatus(state, 'create-status', 'Title, Raw Markdown, and memory-write token are required.', 'error');
      return;
    }
    if (!beginWrite(state, 'create-memory')) return;
    try {
      setStatus(state, 'create-status', 'Creating memory…', 'loading');
      var body = { title: title, content: content, domain: [domain], source: 'dashboard' };
      if (eventDate) body.date = eventDate;
      var payload = await requestJson(state, 'post', '/api/memories', body, {
        headers: { Authorization: 'Bearer ' + writeToken },
        onUnauthorized: null,
      });
      setStatus(state, 'create-status', 'Memory created: ' + (payload.id || 'ok') + '.', 'ok');
      if (form.elements.title) form.elements.title.value = '';
      if (form.elements.content) form.elements.content.value = '';
      if (form.elements.event_date) form.elements.event_date.value = '';
      await refreshAfterWrite(state, payload.id || '');
    } catch (error) {
      setStatus(state, 'create-status', 'Create failed: ' + errorMessage(error), 'error');
    } finally {
      if (tokenInput) tokenInput.value = '';
      finishWrite(state, 'create-memory');
    }
  }

  async function editBucket(state, form) {
    var bucketId = cleanText(formValue(form, 'bucket_id'), 160);
    var title = cleanText(formValue(form, 'title'), 120);
    var content = rawText(formValue(form, 'content'), MAX_CONTENT_LENGTH);
    var eventDate = dateValue(formValue(form, 'event_date'));
    if (!bucketId || !title || !content.trim()) {
      setStatus(state, 'detail-status', 'Bucket, title, and Raw Markdown are required.', 'error');
      return;
    }
    var key = 'edit:' + bucketId;
    if (!beginWrite(state, key)) return;
    try {
      var confirmed = await confirmAction(state, 'Save bucket title, Event date, and Raw Markdown?', bucketId);
      if (!confirmed) return;
      setStatus(state, 'detail-status', 'Saving bucket…', 'loading');
      await requestJson(state, 'patch', '/api/bucket/' + encodeURIComponent(bucketId), {
        name: title,
        date: eventDate,
        content: content,
      });
      setStatus(state, 'detail-status', 'Bucket saved.', 'ok');
      await refreshAfterWrite(state, bucketId);
    } catch (error) {
      setStatus(state, 'detail-status', 'Save failed: ' + errorMessage(error), 'error');
    } finally {
      finishWrite(state, key);
    }
  }

  function parseList(value) {
    return String(value || '')
      .split(/[,，、\n]+/)
      .map(function trim(item) { return item.trim().slice(0, 80); })
      .filter(Boolean)
      .filter(function unique(item, index, values) { return values.indexOf(item) === index; })
      .slice(0, 50);
  }

  async function bulkUpdate(state, form) {
    var ids = Array.from(state.selectedIds).slice(0, MAX_LIST_RESULTS);
    if (!ids.length) {
      setStatus(state, 'bulk-status', 'Select at least one bucket.', 'error');
      return;
    }
    var body = { bucket_ids: ids };
    var domain = cleanText(formValue(form, 'domain'), 80);
    var status = cleanText(formValue(form, 'status'), 20);
    var tagsAdd = parseList(formValue(form, 'tags_add'));
    var tagsRemove = parseList(formValue(form, 'tags_remove'));
    if (domain) body.domain = domain;
    if (status) body.status = status;
    if (tagsAdd.length) body.tags_add = tagsAdd;
    if (tagsRemove.length) body.tags_remove = tagsRemove;
    if (Object.keys(body).length === 1) {
      setStatus(state, 'bulk-status', 'Choose at least one bulk change.', 'error');
      return;
    }
    if (!beginWrite(state, 'bulk-update')) return;
    try {
      var confirmed = await confirmAction(state, 'Apply bulk update to ' + ids.length + ' bucket(s)?', ids.slice(0, 12).join('\n'));
      if (!confirmed) return;
      setStatus(state, 'bulk-status', 'Applying bulk update…', 'loading');
      var payload = await requestJson(state, 'post', '/api/buckets/bulk-update', body);
      setStatus(state, 'bulk-status', 'Bulk update changed ' + (payload.changed_count || payload.changed || 0) + ' bucket(s).', 'ok');
      state.selectedIds.clear();
      await refreshAfterWrite(state, '');
    } catch (error) {
      setStatus(state, 'bulk-status', 'Bulk update failed: ' + errorMessage(error), 'error');
    } finally {
      finishWrite(state, 'bulk-update');
    }
  }

  async function bulkDelete(state) {
    var ids = Array.from(state.selectedIds).slice(0, MAX_LIST_RESULTS);
    if (!ids.length) {
      setStatus(state, 'bulk-status', 'Select at least one bucket.', 'error');
      return;
    }
    if (!beginWrite(state, 'bulk-delete')) return;
    try {
      var confirmed = await confirmAction(
        state,
        'Permanently delete ' + ids.length + ' selected bucket(s)? This cannot be undone from the Dashboard.',
        ids.slice(0, 12).join('\n')
      );
      if (!confirmed) return;
      setStatus(state, 'bulk-status', 'Deleting selected buckets…', 'loading');
      var payload = await requestJson(state, 'post', '/api/buckets/delete', {
        bucket_ids: ids,
        confirm: 'DELETE',
      });
      state.selectedIds.clear();
      setStatus(state, 'bulk-status', 'Deleted ' + (payload.deleted || 0) + ' bucket(s).', 'ok');
      await refreshAfterWrite(state, '');
    } catch (error) {
      setStatus(state, 'bulk-status', 'Delete failed: ' + errorMessage(error), 'error');
    } finally {
      finishWrite(state, 'bulk-delete');
    }
  }

  async function addComment(state, form) {
    var bucketId = cleanText(formValue(form, 'bucket_id'), 160);
    var content = rawText(formValue(form, 'content'), MAX_COMMENT_LENGTH);
    var kind = cleanText(formValue(form, 'kind'), 40) || 'comment';
    if (!bucketId || !content.trim()) {
      setStatus(state, 'detail-status', 'Choose a bucket and enter a year ring.', 'error');
      return;
    }
    var key = 'comment-add:' + bucketId;
    if (!beginWrite(state, key)) return;
    try {
      setStatus(state, 'detail-status', 'Adding year ring…', 'loading');
      await requestJson(
        state,
        'post',
        '/api/bucket/' + encodeURIComponent(bucketId) + '/comments',
        { content: content, kind: kind }
      );
      if (form.elements.content) form.elements.content.value = '';
      await refreshAfterWrite(state, bucketId);
      setStatus(state, 'detail-status', 'Year ring added.', 'ok');
    } catch (error) {
      setStatus(state, 'detail-status', 'Year ring failed: ' + errorMessage(error), 'error');
    } finally {
      finishWrite(state, key);
    }
  }

  async function deleteComment(state, bucketId, commentId) {
    if (!bucketId || !commentId) return;
    var key = 'comment-delete:' + bucketId + ':' + commentId;
    if (!beginWrite(state, key)) return;
    try {
      var confirmed = await confirmAction(state, 'Delete this Dashboard year ring?', commentId);
      if (!confirmed) return;
      await requestJson(
        state,
        'delete',
        '/api/bucket/' + encodeURIComponent(bucketId) + '/comments/' + encodeURIComponent(commentId)
      );
      await refreshAfterWrite(state, bucketId);
      setStatus(state, 'detail-status', 'Year ring deleted.', 'ok');
    } catch (error) {
      setStatus(state, 'detail-status', 'Year ring delete failed: ' + errorMessage(error), 'error');
    } finally {
      finishWrite(state, key);
    }
  }

  async function ingestRaw(state, form) {
    var text = rawText(formValue(form, 'text'), MAX_CONTENT_LENGTH);
    if (!text.trim()) {
      setStatus(state, 'ingest-status', 'Raw text is required.', 'error');
      return;
    }
    if (!beginWrite(state, 'ingest-raw')) return;
    try {
      setStatus(state, 'ingest-status', 'Ingesting raw event…', 'loading');
      var body = {
        source: cleanText(formValue(form, 'source'), 120) || 'dashboard',
        session_id: cleanText(formValue(form, 'session_id'), 160),
        conversation_id: cleanText(formValue(form, 'conversation_id'), 160),
        event: {
          role: cleanText(formValue(form, 'role'), 40) || 'user',
          text: text,
        },
      };
      var payload = await requestJson(state, 'post', '/api/ingest-raw', body);
      setStatus(state, 'ingest-status', 'Raw ingest complete: ' + (payload.ingested || payload.count || payload.status || 'ok') + '.', 'ok');
      if (form.elements.text) form.elements.text.value = '';
    } catch (error) {
      setStatus(state, 'ingest-status', 'Raw ingest failed: ' + errorMessage(error), 'error');
    } finally {
      finishWrite(state, 'ingest-raw');
    }
  }

  function rawSearchBody(state) {
    var form = findForm(state, 'form[data-form="raw-search"]');
    if (!form) return {};
    var limit = Number.parseInt(formValue(form, 'limit'), 10);
    if (!Number.isFinite(limit)) limit = 20;
    limit = Math.max(1, Math.min(MAX_RAW_RESULTS, limit));
    return {
      q: cleanText(formValue(form, 'q'), MAX_QUERY_LENGTH),
      limit: limit,
      source: cleanText(formValue(form, 'source'), 120),
      role: cleanText(formValue(form, 'role'), 40),
      session_id: cleanText(formValue(form, 'session_id'), 160),
    };
  }

  async function searchRaw(state, method) {
    var body = rawSearchBody(state);
    var token = nextRequest(state, 'raw-search');
    setStatus(state, 'raw-search-status', 'Searching raw events…', 'loading');
    try {
      var payload;
      if (method === 'get') {
        var query = new URLSearchParams();
        Object.keys(body).forEach(function add(key) {
          if (body[key] !== '') query.set(key, String(body[key]));
        });
        payload = await requestJson(state, 'get', '/api/search-raw?' + query.toString());
      } else {
        payload = await requestJson(state, 'post', '/api/search-raw', body);
      }
      if (!requestIsCurrent(state, 'raw-search', token)) return;
      renderRawResults(state, payload);
      setStatus(state, 'raw-search-status', 'Raw event search complete.', 'ok');
    } catch (error) {
      if (!requestIsCurrent(state, 'raw-search', token)) return;
      setStatus(state, 'raw-search-status', 'Raw search failed: ' + errorMessage(error), 'error');
    }
  }

  function renderRawResults(state, payload) {
    var target = state.root.querySelector('[data-role="raw-results"]');
    if (!target) return;
    var items = Array.isArray(payload)
      ? payload
      : payload && (payload.events || payload.results || payload.items) || [];
    if (!Array.isArray(items)) items = [];
    items = items.slice(0, MAX_RAW_RESULTS);
    if (!items.length) {
      target.innerHTML = '<div class="shared-bucket-studio__empty">No raw events.</div>';
      return;
    }
    target.innerHTML = items.map(function render(item) {
      var label = item.id || item.event_id || item.role || item.source || 'raw event';
      var text = item.text || item.content || item.content_preview || JSON.stringify(item);
      return '<article class="shared-bucket-studio__card"><div class="shared-bucket-studio__card-head"><strong>' +
        escapeHtml(state, label) + '</strong><small>' + escapeHtml(state, item.created_at || item.created || '') +
        '</small></div><pre>' + escapeHtml(state, rawText(text, 4000)) + '</pre></article>';
    }).join('');
  }

  async function loadEdges(state) {
    var token = nextRequest(state, 'edges');
    setStatus(state, 'edges-status', 'Loading memory edges…', 'loading');
    try {
      var payload = await requestJson(state, 'get', '/api/edges');
      if (!requestIsCurrent(state, 'edges', token)) return;
      var edges = payload && Array.isArray(payload.edges) ? payload.edges.slice(0, MAX_EDGE_RESULTS) : [];
      var target = state.root.querySelector('[data-role="edges"]');
      if (target) {
        target.innerHTML = edges.length ? edges.map(function render(edge) {
          var source = edge.source || edge.source_id || edge.from || 'unknown';
          var targetId = edge.target || edge.target_id || edge.to || 'unknown';
          return '<article class="shared-bucket-studio__card"><strong>' +
            escapeHtml(state, source + ' → ' + targetId) + '</strong><p>' +
            escapeHtml(state, edge.kind || edge.relation_type || edge.reason || 'related') + '</p></article>';
        }).join('') : '<div class="shared-bucket-studio__empty">No memory edges.</div>';
      }
      setStatus(state, 'edges-status', 'Showing ' + edges.length + ' edge(s).', 'ok');
    } catch (error) {
      if (!requestIsCurrent(state, 'edges', token)) return;
      setStatus(state, 'edges-status', 'Memory edges failed: ' + errorMessage(error), 'error');
    }
  }

  async function loadDomainTaxonomy(state) {
    var token = nextRequest(state, 'taxonomy');
    setStatus(state, 'taxonomy-status', 'Loading domain taxonomy…', 'loading');
    try {
      var payload = await requestJson(state, 'get', '/api/domain-taxonomy');
      if (!requestIsCurrent(state, 'taxonomy', token)) return;
      state.domains = payload && Array.isArray(payload.domains) ? payload.domains.slice(0, 100) : [];
      renderTaxonomy(state);
      setStatus(state, 'taxonomy-status', 'Loaded ' + state.domains.length + ' domain(s).', 'ok');
    } catch (error) {
      if (!requestIsCurrent(state, 'taxonomy', token)) return;
      setStatus(state, 'taxonomy-status', 'Domain taxonomy failed: ' + errorMessage(error), 'error');
    }
  }

  function renderTaxonomy(state) {
    var target = state.root.querySelector('[data-role="taxonomy"]');
    if (target) {
      target.innerHTML = state.domains.length ? state.domains.map(function render(domain) {
        return '<article class="shared-bucket-studio__card"><strong>' +
          escapeHtml(state, domain.label || domain.key || 'domain') + '</strong><p>' +
          escapeHtml(state, domain.key || '') +
          (domain.description ? ' · ' + escapeHtml(state, domain.description) : '') + '</p></article>';
      }).join('') : '<div class="shared-bucket-studio__empty">No taxonomy entries.</div>';
    }
    populateDomainSelect(state, '[data-role="create-domain"]', false);
    populateDomainSelect(state, '[data-role="bulk-domain"]', true);
  }

  function populateDomainSelect(state, selector, allowBlank) {
    var select = state.root.querySelector(selector);
    if (!select) return;
    var existing = cleanText(select.value, 80);
    var options = state.domains.length ? state.domains : [{ key: 'general', label: 'general' }];
    select.innerHTML = (allowBlank ? '<option value="">Keep domain</option>' : '') +
      options.map(function render(domain) {
        var key = cleanText(domain.key, 80) || 'general';
        return '<option value="' + escapeHtml(state, key) + '">' +
          escapeHtml(state, domain.label || key) + '</option>';
      }).join('');
    if (existing && options.some(function match(domain) { return domain.key === existing; })) {
      select.value = existing;
    }
  }

  async function refreshAfterWrite(state, bucketId) {
    if (state.app.store && typeof state.app.store.invalidate === 'function') {
      state.app.store.invalidate(['buckets', 'memory:buckets']);
    }
    if (state.app.commands && typeof state.app.commands.refreshBuckets === 'function') {
      state.app.commands.refreshBuckets();
    }
    await loadBucketList(state, state.listMode === 'full' ? 'full' : 'light');
    if (bucketId) await loadBucketDetail(state, bucketId);
  }
})(window);
