(function () {
  'use strict';

  var factories = window.OmbreDashboardFeatureFactories =
    window.OmbreDashboardFeatureFactories || [];

  factories.push(function registerMemoryInsights(app) {
    if (!app || typeof app.registerPanel !== 'function') return;

    var api = app.api || {};
    var ui = app.ui || {};
    var identityCache = { value: null, pending: null, generation: 0 };
    var wordPanelState = null;

    function escapeFallback(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function esc(value) {
      return typeof ui.escape === 'function'
        ? ui.escape(String(value == null ? '' : value))
        : escapeFallback(value);
    }

    function escAttr(value) {
      return typeof ui.escapeAttr === 'function'
        ? ui.escapeAttr(String(value == null ? '' : value))
        : escapeFallback(value);
    }

    function errorText(error, fallback) {
      if (error && error.message) return String(error.message);
      return fallback || 'Request failed';
    }

    function responseError(data, response) {
      if (data && (data.error || data.reason || data.message)) {
        return String(data.error || data.reason || data.message);
      }
      return response && response.status
        ? 'Request failed (' + response.status + ')'
        : 'Request failed';
    }

    async function readJson(response) {
      if (!response) throw new Error('No response received');
      var data;
      if (typeof api.readJson === 'function') {
        data = await api.readJson(response);
      } else {
        try {
          data = await response.json();
        } catch (_error) {
          data = {};
        }
      }
      if (!response.ok) throw new Error(responseError(data, response));
      return data || {};
    }

    async function getJson(path, options) {
      if (typeof api.get !== 'function') throw new Error('Dashboard API is unavailable');
      return readJson(await api.get(path, options));
    }

    async function postJson(path, body) {
      if (typeof api.post !== 'function') throw new Error('Dashboard API is unavailable');
      return readJson(await api.post(path, body));
    }

    function abortControllerType() {
      if (typeof window.AbortController === 'function') return window.AbortController;
      return typeof AbortController === 'function' ? AbortController : null;
    }

    function abortPanelRequest(state, key) {
      var records = state && state.requestRecords;
      var record = records && records[key];
      if (!record) return;
      if (records[key] === record) delete records[key];
      record.cleanup();
      if (record.controller && !record.signal.aborted) record.controller.abort();
    }

    function beginPanelRequest(state, key) {
      state.requestRecords = state.requestRecords || {};
      abortPanelRequest(state, key);
      var Controller = abortControllerType();
      var controller = Controller ? new Controller() : null;
      var routeSignal = state.routeSignal || null;
      var forwardAbort = controller && routeSignal ? function () {
        controller.abort(routeSignal.reason);
      } : null;
      if (forwardAbort) {
        if (routeSignal.aborted) forwardAbort();
        else routeSignal.addEventListener('abort', forwardAbort, { once: true });
      }
      var record = {
        controller: controller,
        signal: controller ? controller.signal : routeSignal,
        cleanup: function () {
          if (forwardAbort) routeSignal.removeEventListener('abort', forwardAbort);
        },
      };
      state.requestRecords[key] = record;
      return record;
    }

    function finishPanelRequest(state, key, record) {
      if (state.requestRecords && state.requestRecords[key] === record) {
        delete state.requestRecords[key];
      }
      record.cleanup();
    }

    function abortPanelRequests(state) {
      if (!state || !state.requestRecords) return;
      Object.keys(state.requestRecords).forEach(function (key) {
        abortPanelRequest(state, key);
      });
    }

    function activatePanelState(state, context) {
      abortPanelRequests(state);
      state.routeSignal = context && context.signal ? context.signal : null;
    }

    function deactivatePanelState(state) {
      abortPanelRequests(state);
      state.routeSignal = null;
    }

    function requestWasAborted(error, request) {
      return Boolean(request && request.signal && request.signal.aborted) ||
        Boolean(error && error.name === 'AbortError');
    }

    function normalizedAbortReason(signal) {
      var reason = signal && signal.reason;
      if (reason instanceof Error) return reason;
      var error = new Error(reason == null ? 'Aborted' : String(reason));
      error.name = 'AbortError';
      return error;
    }

    function waitForSignal(promise, signal) {
      if (!signal) return promise;
      if (signal.aborted) return Promise.reject(normalizedAbortReason(signal));
      return new Promise(function (resolve, reject) {
        var settled = false;
        function cleanup() {
          signal.removeEventListener('abort', onAbort);
        }
        function finish(callback, value) {
          if (settled) return;
          settled = true;
          cleanup();
          callback(value);
        }
        function onAbort() {
          finish(reject, normalizedAbortReason(signal));
        }
        signal.addEventListener('abort', onAbort, { once: true });
        Promise.resolve(promise).then(
          function (value) { finish(resolve, value); },
          function (error) { finish(reject, error); }
        );
      });
    }

    function setStatus(element, message, tone) {
      if (!element) return;
      if (typeof ui.setStatus === 'function') {
        ui.setStatus(element, message || '', tone || '');
        return;
      }
      element.textContent = message || '';
      element.setAttribute('data-tone', tone || '');
    }

    async function confirmAction(message) {
      if (typeof ui.confirm === 'function') {
        return Boolean(await Promise.resolve(ui.confirm(message)));
      }
      if (typeof window.confirm === 'function') {
        return Boolean(await Promise.resolve(window.confirm(message)));
      }
      return false;
    }

    function stateBlock(kind, message, retry) {
      var stateAttr = kind === 'loading'
        ? 'data-state="loading"'
        : kind === 'error'
          ? 'data-state="error"'
          : 'data-state="empty"';
      return '<div class="memory-insights-state" ' + stateAttr + '>' +
        '<span>' + esc(message) + '</span>' +
        (retry ? '<button type="button" data-action="retry">Retry</button>' : '') +
      '</div>';
    }

    function numberText(value, digits) {
      var number = Number(value);
      if (!Number.isFinite(number)) return '0';
      return typeof digits === 'number' ? number.toFixed(digits) : String(number);
    }

    function rankText(value) {
      return value == null ? '—' : String(Number(value) + 1);
    }

    function chip(text, tone) {
      return '<span class="memory-insights-chip" data-tone="' +
        escAttr(tone || '') + '">' + esc(text) + '</span>';
    }

    function chips(items) {
      return '<div class="memory-insights-chips">' + items.map(function (item) {
        return chip(item.text, item.tone);
      }).join('') + '</div>';
    }

    function panelHeader(title, description, actions) {
      return '<header class="memory-insights-header">' +
        '<div><h2>' + esc(title) + '</h2><p>' + esc(description) + '</p></div>' +
        '<div class="memory-insights-actions">' + (actions || '') + '</div>' +
      '</header>';
    }

    function prepareRoot(root, panelId, html) {
      root.setAttribute('data-panel-id', panelId);
      root.innerHTML = '<section class="memory-insights-panel">' + html + '</section>';
    }

    function closestAction(root, event) {
      var element = event.target && event.target.closest
        ? event.target.closest('[data-action]')
        : null;
      return element && root.contains(element) ? element : null;
    }

    function bindPanelEvents(root, state, clickHandlers, submitHandlers) {
      root.addEventListener('click', function (event) {
        var actionElement = closestAction(root, event);
        if (!actionElement) return;
        var handler = clickHandlers[actionElement.getAttribute('data-action')];
        if (typeof handler === 'function') handler(actionElement, event, state);
      });
      root.addEventListener('submit', function (event) {
        var form = event.target;
        var action = form && form.getAttribute ? form.getAttribute('data-submit') : '';
        var handler = submitHandlers && submitHandlers[action];
        if (typeof handler !== 'function') return;
        event.preventDefault();
        handler(form, event, state);
      });
    }

    function paramsValue(params, key) {
      if (!params) return '';
      var values = params.state && params.state.params
        ? params.state.params
        : (params.params || params);
      if (typeof values.get === 'function') return String(values.get(key) || '');
      return String(values[key] || '');
    }

    function formatWordMapStats(stats) {
      stats = stats || {};
      return (stats.nodes || 0) + ' nodes · ' +
        (stats.card_nodes || 0) + ' card links · ' +
        (stats.edge_evidence || 0) + ' edge evidence';
    }

    function formatIdentityStats(stats) {
      stats = stats || {};
      return (stats.canonical || 0) + ' canonical · ' +
        (stats.aliases || 0) + ' aliases · ' +
        (stats.evidence || 0) + ' evidence';
    }

    function createIdentityRequest() {
      var Controller = abortControllerType();
      var controller = Controller ? new Controller() : null;
      var generation = identityCache.generation;
      var record = {
        controller: controller,
        signal: controller ? controller.signal : null,
        subscribers: 0,
        settled: false,
        promise: null,
      };
      record.promise = getJson('/api/identity-semantics?limit=50', {
        signal: record.signal,
      }).then(function (data) {
        if (generation !== identityCache.generation) {
          return identityCache.value || data;
        }
        identityCache.value = data;
        return data;
      }).finally(function () {
        record.settled = true;
        if (identityCache.pending === record) identityCache.pending = null;
      });
      identityCache.pending = record;
      return record;
    }

    async function identityPayload(force, options) {
      if (!force && identityCache.value) return identityCache.value;
      if (force) identityCache.generation += 1;
      var record = !force && identityCache.pending && !identityCache.pending.settled
        ? identityCache.pending
        : createIdentityRequest();
      record.subscribers += 1;
      try {
        return await waitForSignal(record.promise, options && options.signal);
      } finally {
        record.subscribers -= 1;
        if (!record.settled && record.subscribers === 0 && record.controller && !record.signal.aborted) {
          record.controller.abort();
        }
      }
    }

    function wordMapMarkup() {
      return panelHeader(
        'Word Map Lite',
        'Explore the derived word graph, supporting cards, and the boundary around private identity aliases.',
        '<button type="button" data-action="refresh">Refresh</button>' +
        '<button type="button" data-action="rebuild-word-map">Rebuild Word Map</button>'
      ) +
      '<div class="memory-insights-summary" data-role="summary"></div>' +
      '<div class="memory-insights-message" data-role="message" aria-live="polite"></div>' +
      '<div class="memory-insights-grid memory-insights-grid--word-map">' +
        '<section><h3>Top Nodes</h3><div data-role="nodes"></div></section>' +
        '<section><h3>Co-occurrence Edges</h3><div data-role="edges"></div></section>' +
        '<section><h3>Selected Term Cards</h3><div data-role="cards" aria-live="polite"></div></section>' +
        '<section><h3>Private Alias Boundary</h3><div data-role="boundary"></div></section>' +
      '</div>';
    }

    function renderWordNodes(state, nodes) {
      var target = state.root.querySelector('[data-role="nodes"]');
      if (!nodes.length) {
        target.innerHTML = stateBlock('empty', 'No word nodes yet. Rebuild the Word Map to derive them.', false);
        return;
      }
      target.innerHTML = '<div class="memory-insights-list">' + nodes.map(function (node) {
        var term = String(node.term || '');
        return '<button type="button" class="memory-insights-card memory-insights-node" ' +
          'data-action="select-word" data-term="' + escAttr(term) + '" ' +
          'aria-pressed="' + (state.selectedTerm === term ? 'true' : 'false') + '">' +
            '<strong>' + esc(term) + '</strong>' +
            '<span>' + esc(node.kind || 'keyword') + ' · buckets ' +
              esc(node.bucket_count || 0) + ' · weight ' +
              esc(numberText(node.weight, 2)) + '</span>' +
          '</button>';
      }).join('') + '</div>';
    }

    function renderWordEdges(state, edges) {
      var target = state.root.querySelector('[data-role="edges"]');
      if (!edges.length) {
        target.innerHTML = stateBlock('empty', 'No co-occurrence edges yet.', false);
        return;
      }
      target.innerHTML = '<div class="memory-insights-list">' + edges.map(function (edge) {
        return '<article class="memory-insights-card">' +
          '<strong>' + esc(edge.term_a || '') + ' ↔ ' + esc(edge.term_b || '') + '</strong>' +
          '<span>buckets ' + esc(edge.bucket_count || 0) + ' · weight ' +
            esc(numberText(edge.weight, 2)) + '</span>' +
        '</article>';
      }).join('') + '</div>';
    }

    function renderWordBoundary(state, mapData, identity) {
      var target = state.root.querySelector('[data-role="boundary"]');
      var privateTerms = Array.isArray(mapData.private_terms_excluded)
        ? mapData.private_terms_excluded
        : [];
      var aliases = Array.isArray(identity.aliases) ? identity.aliases : [];
      target.innerHTML = '<div class="memory-insights-list">' +
        '<article class="memory-insights-card"><strong>Generic Word Map</strong>' +
          '<span>Derived from subjects, keywords, tags, and TF-IDF co-occurrence evidence.</span></article>' +
        '<article class="memory-insights-card"><strong>Private Scope</strong>' +
          '<span>Private aliases stay out of the generic graph. Excluded terms: ' +
            esc(privateTerms.length ? privateTerms.join(', ') : 'none') + '</span></article>' +
        '<article class="memory-insights-card"><strong>Private Alias</strong>' +
          '<span>' + esc(aliases.length) + ' aliases · ' +
            esc(identity.enabled ? 'private canonical enabled' : 'private canonical disabled') +
            '. The full evidence list lives in Identity Semantics.</span></article>' +
        '<article class="memory-insights-card"><strong>Gateway Boundary</strong>' +
          '<span>This derived index is diagnostic only and does not automatically change Gateway injection.</span></article>' +
      '</div>';
    }

    function renderWordMap(state) {
      if (!state.map || !state.identity) return;
      var nodes = Array.isArray(state.map.nodes) ? state.map.nodes : [];
      var edges = Array.isArray(state.map.edges) ? state.map.edges : [];
      state.root.querySelector('[data-role="summary"]').innerHTML = chips([
        { text: 'Word Map ' + formatWordMapStats(state.map.stats) },
        { text: 'Private Alias ' + formatIdentityStats(state.identity.stats) },
        { text: state.identity.enabled ? 'private config enabled' : 'private config disabled', tone: state.identity.enabled ? 'ok' : '' },
      ]);
      renderWordNodes(state, nodes);
      renderWordEdges(state, edges);
      renderWordBoundary(state, state.map, state.identity);
    }

    function renderWordCards(state, data) {
      var target = state.root.querySelector('[data-role="cards"]');
      var cards = data && Array.isArray(data.cards) ? data.cards : [];
      if (!cards.length) {
        target.innerHTML = stateBlock('empty', 'No bucket cards support “' + state.selectedTerm + '”.', false);
        return;
      }
      target.innerHTML = '<div class="memory-insights-list">' + cards.map(function (card) {
        return '<article class="memory-insights-card">' +
          '<strong>' + esc(card.bucket_id || 'unknown bucket') + '</strong>' +
          '<span>' + esc(card.kind || 'keyword') + ' · ' + esc(card.source || 'derived') +
            ' · weight ' + esc(numberText(card.weight, 2)) + '</span>' +
          (card.updated_at ? '<small>' + esc(card.updated_at) + '</small>' : '') +
        '</article>';
      }).join('') + '</div>';
    }

    async function loadWordCards(state, term) {
      state.selectedTerm = String(term || '').trim();
      renderWordNodes(state, Array.isArray(state.map && state.map.nodes) ? state.map.nodes : []);
      var target = state.root.querySelector('[data-role="cards"]');
      if (!state.selectedTerm) {
        target.innerHTML = stateBlock('empty', 'Choose a word node to inspect its bucket cards.', false);
        return;
      }
      var token = ++state.cardRequest;
      var request = beginPanelRequest(state, 'cards');
      target.innerHTML = stateBlock('loading', 'Loading cards for “' + state.selectedTerm + '”…', false);
      try {
        var data = await getJson('/api/word-map/cards?term=' +
          encodeURIComponent(state.selectedTerm) + '&limit=20', { signal: request.signal });
        if (token !== state.cardRequest) return;
        state.cards = data;
        renderWordCards(state, data);
      } catch (error) {
        if (token !== state.cardRequest || requestWasAborted(error, request)) return;
        target.innerHTML = stateBlock('error', 'Cards failed to load: ' + errorText(error), true);
      } finally {
        finishPanelRequest(state, 'cards', request);
      }
    }

    async function loadWordMap(state, force) {
      var token = ++state.request;
      state.cardRequest += 1;
      abortPanelRequest(state, 'cards');
      var request = beginPanelRequest(state, 'main');
      var summary = state.root.querySelector('[data-role="summary"]');
      summary.innerHTML = stateBlock('loading', 'Loading Word Map and private boundary…', false);
      state.root.querySelector('[data-role="nodes"]').innerHTML = stateBlock('loading', 'Loading nodes…', false);
      state.root.querySelector('[data-role="edges"]').innerHTML = stateBlock('loading', 'Loading edges…', false);
      state.root.querySelector('[data-role="cards"]').innerHTML = stateBlock('loading', 'Waiting for a node…', false);
      state.root.querySelector('[data-role="boundary"]').innerHTML = stateBlock('loading', 'Loading private boundary…', false);
      setStatus(state.root.querySelector('[data-role="message"]'), '', '');
      try {
        var results = await Promise.all([
          getJson('/api/word-map?nodes=20&edges=20', { signal: request.signal }),
          identityPayload(Boolean(force), { signal: request.signal }),
        ]);
        if (token !== state.request) return;
        state.map = results[0];
        state.identity = results[1];
        renderWordMap(state);
        var nodes = Array.isArray(state.map.nodes) ? state.map.nodes : [];
        var preferred = state.selectedTerm && nodes.some(function (node) {
          return String(node.term || '') === state.selectedTerm;
        }) ? state.selectedTerm : (nodes[0] && nodes[0].term);
        await loadWordCards(state, preferred || '');
      } catch (error) {
        if (token !== state.request || requestWasAborted(error, request)) return;
        summary.innerHTML = stateBlock('error', 'Word Map failed to load: ' + errorText(error), true);
        state.root.querySelector('[data-role="nodes"]').innerHTML = '';
        state.root.querySelector('[data-role="edges"]').innerHTML = '';
        state.root.querySelector('[data-role="cards"]').innerHTML = '';
        state.root.querySelector('[data-role="boundary"]').innerHTML = '';
        setStatus(state.root.querySelector('[data-role="message"]'), errorText(error), 'error');
      } finally {
        finishPanelRequest(state, 'main', request);
      }
    }

    async function rebuildWordMap(state, button) {
      if (state.rebuilding) return;
      var status = state.root.querySelector('[data-role="message"]');
      state.rebuilding = true;
      if (button) button.disabled = true;
      try {
        if (!await confirmAction('Rebuild the derived Word Map index now?')) return;
        setStatus(status, 'Rebuilding Word Map…', 'loading');
        var data = await postJson('/api/word-map/rebuild', {
          include_archive: false,
          nodes: 20,
          edges: 20
        });
        await loadWordMap(state, false);
        setStatus(status, 'Word Map rebuilt: ' + formatWordMapStats(data.stats), 'ok');
      } catch (error) {
        setStatus(status, 'Rebuild failed: ' + errorText(error), 'error');
      } finally {
        state.rebuilding = false;
        if (button) button.disabled = false;
      }
    }

    function mountWordMap(root) {
      var state = wordPanelState = {
        root: root,
        request: 0,
        cardRequest: 0,
        map: null,
        identity: null,
        cards: null,
        selectedTerm: '',
        rebuilding: false,
      };
      prepareRoot(root, 'memory-word-map', wordMapMarkup());
      bindPanelEvents(root, state, {
        refresh: function () { loadWordMap(state, true); },
        retry: function () { loadWordMap(state, true); },
        'select-word': function (element) { loadWordCards(state, element.getAttribute('data-term')); },
        'rebuild-word-map': function (element) { rebuildWordMap(state, element); },
      });
      return state;
    }

    function identityMarkup() {
      return panelHeader(
        'Identity Semantics',
        'Inspect the private alias-to-canonical index and its evidence without mixing it into display-name settings.',
        '<button type="button" data-action="refresh">Refresh</button>' +
        '<button type="button" data-action="rebuild-identity">Rebuild Private Alias</button>'
      ) +
      '<div class="memory-insights-summary" data-role="summary"></div>' +
      '<div class="memory-insights-message" data-role="message" aria-live="polite"></div>' +
      '<div data-role="aliases" aria-live="polite"></div>';
    }

    function renderIdentity(state, data) {
      var aliases = Array.isArray(data.aliases) ? data.aliases : [];
      state.root.querySelector('[data-role="summary"]').innerHTML = chips([
        { text: formatIdentityStats(data.stats) },
        { text: data.enabled ? 'Private Alias enabled' : 'Private Alias disabled', tone: data.enabled ? 'ok' : '' },
        { text: data.private_configured ? 'private config present' : 'private config not configured' },
      ]);
      var target = state.root.querySelector('[data-role="aliases"]');
      if (!aliases.length) {
        target.innerHTML = stateBlock('empty', 'No private aliases. The index stays empty until private canonical identity is configured.', false);
        return;
      }
      target.innerHTML = '<div class="memory-insights-list memory-insights-list--wide">' + aliases.map(function (item) {
        var evidence = Array.isArray(item.evidence_bucket_ids) ? item.evidence_bucket_ids : [];
        return '<article class="memory-insights-card">' +
          '<div class="memory-insights-card-title"><strong>' + esc(item.alias || '') + '</strong>' +
            '<span>→ ' + esc(item.canonical || '') + '</span></div>' +
          chips([
            { text: item.scope || 'private' },
            { text: 'confidence ' + numberText(item.confidence, 2) },
            { text: evidence.length + ' evidence buckets' },
          ]) +
          '<small>evidence: ' + esc(evidence.length ? evidence.join(', ') : '—') + '</small>' +
        '</article>';
      }).join('') + '</div>';
    }

    async function loadIdentity(state, force) {
      var token = ++state.request;
      var request = beginPanelRequest(state, 'main');
      state.root.querySelector('[data-role="summary"]').innerHTML = stateBlock('loading', 'Loading identity semantics…', false);
      state.root.querySelector('[data-role="aliases"]').innerHTML = stateBlock('loading', 'Loading private aliases…', false);
      setStatus(state.root.querySelector('[data-role="message"]'), '', '');
      try {
        var data = await identityPayload(Boolean(force), { signal: request.signal });
        if (token !== state.request) return;
        state.data = data;
        renderIdentity(state, data);
        if (wordPanelState && wordPanelState.root && wordPanelState.map) {
          wordPanelState.identity = data;
          renderWordMap(wordPanelState);
        }
      } catch (error) {
        if (token !== state.request || requestWasAborted(error, request)) return;
        state.root.querySelector('[data-role="summary"]').innerHTML = '';
        state.root.querySelector('[data-role="aliases"]').innerHTML =
          stateBlock('error', 'Identity semantics failed to load: ' + errorText(error), true);
      } finally {
        finishPanelRequest(state, 'main', request);
      }
    }

    async function rebuildIdentity(state, button) {
      if (state.rebuilding) return;
      var status = state.root.querySelector('[data-role="message"]');
      state.rebuilding = true;
      if (button) button.disabled = true;
      try {
        if (!await confirmAction('Rebuild the private identity alias index now?')) return;
        state.request += 1;
        setStatus(status, 'Rebuilding private aliases…', 'loading');
        var data = await postJson('/api/identity-semantics/rebuild', {
          include_archive: false,
          limit: 50
        });
        identityCache.generation += 1;
        identityCache.value = data;
        state.data = data;
        renderIdentity(state, data);
        setStatus(status, 'Private aliases rebuilt: ' + formatIdentityStats(data.stats), 'ok');
        if (wordPanelState && wordPanelState.root && wordPanelState.map) {
          wordPanelState.identity = data;
          renderWordMap(wordPanelState);
        }
      } catch (error) {
        setStatus(status, 'Rebuild failed: ' + errorText(error), 'error');
      } finally {
        state.rebuilding = false;
        if (button) button.disabled = false;
      }
    }

    function mountIdentity(root) {
      var state = { root: root, request: 0, data: null, rebuilding: false };
      prepareRoot(root, 'memory-identity-semantics', identityMarkup());
      bindPanelEvents(root, state, {
        refresh: function () { loadIdentity(state, true); },
        retry: function () { loadIdentity(state, true); },
        'rebuild-identity': function (element) { rebuildIdentity(state, element); },
      });
      return state;
    }

    function momentMarkup() {
      return panelHeader(
        'Moment Diagnostics',
        'Inspect the moment decomposition, runtime gates, source windows, and edges for one bucket.',
        ''
      ) +
      '<form class="memory-insights-query" data-submit="moments">' +
        '<label><span>Bucket ID</span><input name="bucket_id" type="text" autocomplete="off" placeholder="memory-…" required /></label>' +
        '<button type="submit">Inspect Moments</button>' +
      '</form>' +
      '<div class="memory-insights-message" data-role="message" aria-live="polite"></div>' +
      '<div data-role="results" aria-live="polite">' + stateBlock('empty', 'Enter a bucket ID to inspect its moments.', false) + '</div>';
    }

    function momentGateChips(moment) {
      var layer = moment.layer_debug || {};
      var runtimeGate = moment.runtime_gate || {};
      var direct = runtimeGate.direct_seed || {};
      var related = runtimeGate.related_target || {};
      var recall = runtimeGate.recall_context || {};
      var values = [
        { text: 'layer ' + (layer.layer || runtimeGate.layer || 'unknown') },
        { text: 'section ' + (moment.section || 'moment') },
        { text: 'direct ' + (direct.allowed ? 'yes' : 'no') + (direct.reason ? ' · ' + direct.reason : ''), tone: direct.allowed ? 'ok' : 'blocked' },
        { text: 'related ' + (related.allowed ? 'yes' : 'no') + (related.reason ? ' · ' + related.reason : ''), tone: related.allowed ? 'ok' : 'blocked' },
        { text: 'context ' + (recall.allowed ? 'yes' : 'no'), tone: recall.allowed ? 'ok' : 'blocked' },
      ];
      if (layer.context_only) values.push({ text: 'context-only', tone: 'blocked' });
      return chips(values);
    }

    function formatMomentSource(sourceRef) {
      if (!sourceRef || typeof sourceRef !== 'object') return '';
      var path = sourceRef.path ? String(sourceRef.path).split(/[\\/]/).pop() : '';
      var source = sourceRef.source || 'bucket_content';
      var start = sourceRef.start_line || sourceRef.content_start_line || '';
      var end = sourceRef.end_line || '';
      var line = start ? ':' + start + (end && end !== start ? '-' + end : '') : '';
      return (path ? path + ' · ' : '') + source + line;
    }

    function renderMomentItem(moment, index) {
      var item = moment || {};
      var source = formatMomentSource(item.metadata && item.metadata.source_ref);
      var sourceWindow = item.source_window
        ? '<details class="memory-insights-source"><summary>Source window</summary><pre>' +
          esc(item.source_window) + '</pre></details>'
        : '';
      return '<article class="memory-insights-card memory-insights-card--detail">' +
        '<div class="memory-insights-card-title"><strong>#' + (index + 1) + ' ' +
          esc(item.moment_id || '') + '</strong><small>' + esc(source) + '</small></div>' +
        momentGateChips(item) +
        '<div class="memory-insights-body">' + esc(item.text || item.text_preview || '') + '</div>' +
        sourceWindow +
      '</article>';
    }

    function renderMomentEdges(edges) {
      if (!edges.length) {
        return '<section class="memory-insights-subsection"><h3>Moment Edges</h3>' +
          stateBlock('empty', 'No moment edges.', false) + '</section>';
      }
      return '<section class="memory-insights-subsection"><h3>Moment Edges</h3>' +
        '<div class="memory-insights-list">' + edges.map(function (edge) {
          var edgeChips = [{ text: edge.relation_type || 'relates_to' }];
          if (edge.confidence != null) edgeChips.push({ text: 'confidence ' + numberText(edge.confidence, 2) });
          if (edge.reason) edgeChips.push({ text: String(edge.reason).slice(0, 80) });
          return '<article class="memory-insights-card">' +
            '<div class="memory-insights-card-title"><strong>' + esc(edge.source || 'unknown') +
              ' → ' + esc(edge.target || 'unknown') + '</strong><small>' +
              esc(edge.created_at || '') + '</small></div>' +
            chips(edgeChips) +
          '</article>';
        }).join('') + '</div></section>';
    }

    function renderMoments(state, data) {
      if (!data || data.status !== 'ok') {
        state.root.querySelector('[data-role="results"]').innerHTML =
          stateBlock('empty', 'No moment diagnostics are available for this bucket.', false);
        return;
      }
      var moments = Array.isArray(data.moments) ? data.moments : [];
      var edges = Array.isArray(data.edges) ? data.edges : [];
      var layer = data.bucket_layer_debug || {};
      var summary = chips([
        { text: data.name || data.bucket_id || 'bucket' },
        { text: 'bucket layer ' + (layer.layer || 'unknown') },
        { text: 'direct ' + (layer.can_direct_seed ? 'yes' : 'no'), tone: layer.can_direct_seed ? 'ok' : 'blocked' },
        { text: 'diffuse ' + (layer.can_diffuse ? 'yes' : 'no'), tone: layer.can_diffuse ? 'ok' : 'blocked' },
        { text: 'related ' + (layer.can_related_target ? 'yes' : 'no'), tone: layer.can_related_target ? 'ok' : 'blocked' },
        { text: moments.length + ' moments' },
        { text: (data.edge_count == null ? edges.length : data.edge_count) + ' edges' },
      ]);
      var body = moments.length
        ? '<div class="memory-insights-list memory-insights-list--wide">' +
          moments.map(renderMomentItem).join('') + '</div>'
        : stateBlock('empty', 'This bucket has not produced any moments.', false);
      state.root.querySelector('[data-role="results"]').innerHTML =
        '<div class="memory-insights-summary">' + summary + '</div>' + body + renderMomentEdges(edges);
    }

    async function loadMoments(state, bucketId) {
      bucketId = String(bucketId || '').trim();
      var target = state.root.querySelector('[data-role="results"]');
      if (!bucketId) {
        target.innerHTML = stateBlock('error', 'Bucket ID is required.', false);
        return;
      }
      state.bucketId = bucketId;
      var token = ++state.request;
      var request = beginPanelRequest(state, 'main');
      target.innerHTML = stateBlock('loading', 'Parsing moments for ' + bucketId + '…', false);
      setStatus(state.root.querySelector('[data-role="message"]'), '', '');
      try {
        var data = await getJson('/api/moments?bucket_id=' + encodeURIComponent(bucketId) + '&limit=40', {
          signal: request.signal,
        });
        if (token !== state.request) return;
        renderMoments(state, data);
      } catch (error) {
        if (token !== state.request || requestWasAborted(error, request)) return;
        target.innerHTML = stateBlock('error', 'Moment diagnostics failed: ' + errorText(error), true);
      } finally {
        finishPanelRequest(state, 'main', request);
      }
    }

    function mountMoments(root) {
      var state = { root: root, request: 0, bucketId: '' };
      prepareRoot(root, 'memory-moment-diagnostics', momentMarkup());
      bindPanelEvents(root, state, {
        retry: function () { loadMoments(state, state.bucketId); },
      }, {
        moments: function (form) {
          loadMoments(state, form.elements.bucket_id.value);
        },
      });
      return state;
    }

    function recallMarkup() {
      return panelHeader(
        'Recall Diagnostics',
        'Trace seed buckets, moment candidates, admission gates, ranks, and render decisions for a query.',
        ''
      ) +
      '<form class="memory-insights-query" data-submit="recall">' +
        '<label><span>Recall query</span><input name="query" type="search" autocomplete="off" placeholder="What should memory recall?" required /></label>' +
        '<button type="submit">Run Recall Debug</button>' +
      '</form>' +
      '<div data-role="results" aria-live="polite">' + stateBlock('empty', 'Enter a query to inspect recall candidates.', false) + '</div>';
    }

    function renderRecallCandidate(candidate) {
      var row = candidate || {};
      var admitted = row.admission === 'admitted';
      var direct = Boolean(row.selected_direct);
      var secondary = Boolean(row.selected_secondary);
      var selected = direct || secondary || Boolean(row.selected_returned);
      var role = direct ? 'direct' : secondary ? 'secondary' : selected ? 'returned' : admitted ? 'admitted' : 'suppressed';
      var values = [
        { text: role, tone: selected || admitted ? 'ok' : 'blocked' },
        { text: 'pre #' + rankText(row.pre_rank) },
        { text: 'gate ' + (row.gate_rank == null ? 'filtered' : '#' + rankText(row.gate_rank)), tone: row.gate === 'filtered' ? 'blocked' : 'ok' },
        { text: 'final ' + (row.final_rank == null ? '—' : '#' + rankText(row.final_rank)) },
        { text: 'section ' + (row.section || 'moment') },
      ];
      if (row.admission_reason) values.push({ text: row.admission_reason, tone: admitted ? 'ok' : 'blocked' });
      if (row.rerank_score != null) values.push({ text: 'rerank ' + numberText(row.rerank_score, 2) });
      if (row.embedding_score != null) values.push({ text: 'semantic ' + numberText(row.embedding_score, 2) });
      if (row.direct_render && row.direct_render.shape) {
        values.push({
          text: 'render ' + row.direct_render.shape + ' · ' + (row.direct_render.reason || ''),
          tone: row.direct_render.shape === 'bucket_capsule' ? 'ok' : '',
        });
      }
      if (Array.isArray(row.gate_reasons) && row.gate_reasons.length) {
        values.push({ text: row.gate_reasons.join(' · '), tone: row.gate === 'filtered' ? 'blocked' : '' });
      }
      return '<article class="memory-insights-card memory-insights-card--detail">' +
        '<div class="memory-insights-card-title"><strong>' +
          esc(row.bucket_name || row.bucket_id || 'unknown') + '</strong><small>' +
          esc(row.moment_id || '') + '</small></div>' +
        chips(values) +
        (row.annotation_summary ? '<div class="memory-insights-path">summary: ' + esc(row.annotation_summary) + '</div>' : '') +
        '<div class="memory-insights-body">' + esc(row.text_preview || '') + '</div>' +
      '</article>';
    }

    function renderRecall(state, data) {
      var target = state.root.querySelector('[data-role="results"]');
      if (!data || data.status !== 'ok') {
        target.innerHTML = stateBlock('empty', data && data.error ? data.error : 'No recall diagnostics.', false);
        return;
      }
      var candidates = Array.isArray(data.candidates) ? data.candidates : [];
      var seeds = Array.isArray(data.seed_buckets) ? data.seed_buckets : [];
      var thresholds = data.recall_thresholds || {};
      var warnings = Array.isArray(data.warnings) ? data.warnings : [];
      var summary = chips([
        { text: seeds.length + ' seed buckets' },
        { text: candidates.length + ' candidates' },
        { text: (data.admitted_count || 0) + ' admitted', tone: data.admitted_count ? 'ok' : '' },
        { text: (data.suppressed_count || 0) + ' suppressed', tone: data.suppressed_count ? 'blocked' : '' },
        { text: 'profile ' + (thresholds.profile || 'default') },
        { text: 'direct ' + (thresholds.direct_render_mode || 'auto') },
      ]);
      var body = candidates.length
        ? '<div class="memory-insights-list memory-insights-list--wide">' +
          candidates.map(renderRecallCandidate).join('') + '</div>'
        : stateBlock('empty', 'No moment candidates.', false);
      var warning = warnings.length
        ? '<div class="memory-insights-warning"><strong>Warnings</strong> ' + esc(warnings.join(' · ')) + '</div>'
        : '';
      target.innerHTML = '<div class="memory-insights-summary">' + summary + '</div>' + body + warning;
    }

    async function loadRecall(state, query) {
      query = String(query || '').trim();
      var target = state.root.querySelector('[data-role="results"]');
      if (!query) {
        target.innerHTML = stateBlock('error', 'A recall query is required.', false);
        return;
      }
      state.query = query;
      var token = ++state.request;
      var request = beginPanelRequest(state, 'main');
      target.innerHTML = stateBlock('loading', 'Running recall diagnostics…', false);
      try {
        var data = await getJson('/api/recall-debug?q=' + encodeURIComponent(query) +
          '&max_candidates=12&max_results=3', { signal: request.signal });
        if (token !== state.request) return;
        renderRecall(state, data);
      } catch (error) {
        if (token !== state.request || requestWasAborted(error, request)) return;
        target.innerHTML = stateBlock('error', 'Recall diagnostics failed: ' + errorText(error), true);
      } finally {
        finishPanelRequest(state, 'main', request);
      }
    }

    function mountRecall(root) {
      var state = { root: root, request: 0, query: '' };
      prepareRoot(root, 'memory-recall-diagnostics', recallMarkup());
      bindPanelEvents(root, state, {
        retry: function () { loadRecall(state, state.query); },
      }, {
        recall: function (form) { loadRecall(state, form.elements.query.value); },
      });
      return state;
    }

    function diffusionMarkup() {
      return panelHeader(
        'Diffusion Diagnostics',
        'Follow diffusion seeds, activated hits, alternate paths, runtime gates, and warnings for a query.',
        ''
      ) +
      '<form class="memory-insights-query" data-submit="diffusion">' +
        '<label><span>Diffusion query</span><input name="query" type="search" autocomplete="off" placeholder="What should activate nearby memories?" required /></label>' +
        '<button type="submit">Run Diffusion Debug</button>' +
      '</form>' +
      '<div data-role="results" aria-live="polite">' + stateBlock('empty', 'Enter a query to inspect diffusion paths.', false) + '</div>';
    }

    function renderDiffusionPaths(row) {
      var pathRows = Array.isArray(row.paths) ? row.paths : [];
      var best = row.path
        ? '<div class="memory-insights-path"><strong>Best path</strong> ' + esc(row.path) + '</div>'
        : Array.isArray(row.path_ids) && row.path_ids.length
          ? '<div class="memory-insights-path"><strong>Path IDs</strong> ' + esc(row.path_ids.join(' → ')) + '</div>'
          : '';
      if (!pathRows.length) return best;
      return best + '<details class="memory-insights-paths"><summary>' +
        pathRows.length + ' activation path' + (pathRows.length === 1 ? '' : 's') + '</summary>' +
        pathRows.map(function (path, index) {
          var steps = Array.isArray(path.steps) ? path.steps : [];
          return '<div class="memory-insights-path">#' + (index + 1) + ' · score ' +
            esc(numberText(path.score, 2)) + ' · ' + esc(path.trace || '') +
            (steps.length ? '<small>' + esc(steps.map(function (step) {
              return (step.source || '') + ' ' + (step.relation_type || 'relates_to') + ' ' + (step.target || '');
            }).join(' / ')) + '</small>' : '') + '</div>';
        }).join('') + '</details>';
    }

    function renderDiffusionRow(row, index, kind) {
      row = row || {};
      var runtimeGate = row.runtime_gate || {};
      var related = runtimeGate.related_injection || runtimeGate.related_target || {};
      var values = [
        { text: (kind === 'seed' ? 'seed ' : 'hit ') + '#' + (index + 1) },
        { text: 'layer ' + ((row.layer_debug && row.layer_debug.layer) || runtimeGate.layer || 'unknown') },
      ];
      if (row.seed_score != null) values.push({ text: 'seed score ' + numberText(row.seed_score, 2) });
      if (row.score != null) values.push({ text: 'activation ' + numberText(row.score, 2) });
      if (row.salience != null) values.push({ text: 'salience ' + numberText(row.salience, 2) });
      if (row.resonance != null) values.push({ text: 'resonance ' + numberText(row.resonance, 2) });
      if (related.reason) values.push({
        text: 'related ' + (related.allowed ? 'yes' : 'no') + ' · ' + related.reason,
        tone: related.allowed ? 'ok' : 'blocked',
      });
      if (row.caution) values.push({ text: 'caution', tone: 'blocked' });
      return '<article class="memory-insights-card memory-insights-card--detail">' +
        '<strong>' + esc(row.name || row.bucket_id || 'unknown') + '</strong>' +
        chips(values) + renderDiffusionPaths(row) +
      '</article>';
    }

    function renderDiffusionSection(title, items, kind) {
      return '<section class="memory-insights-subsection"><h3>' + esc(title) + '</h3>' +
        (items.length
          ? '<div class="memory-insights-list">' + items.map(function (row, index) {
            return renderDiffusionRow(row, index, kind);
          }).join('') + '</div>'
          : stateBlock('empty', 'No ' + title.toLowerCase() + '.', false)) +
      '</section>';
    }

    function renderDiffusion(state, data) {
      var target = state.root.querySelector('[data-role="results"]');
      if (!data || data.status !== 'ok') {
        target.innerHTML = stateBlock('empty', data && data.error ? data.error : 'No diffusion diagnostics.', false);
        return;
      }
      var seeds = Array.isArray(data.seeds) ? data.seeds : [];
      var hits = Array.isArray(data.hits) ? data.hits : [];
      var warnings = Array.isArray(data.warnings) ? data.warnings : [];
      var options = data.options || {};
      var summary = chips([
        { text: seeds.length + ' seeds' },
        { text: hits.length + ' hits' },
        { text: 'edge ≥ ' + (options.edge_min_confidence == null ? '—' : options.edge_min_confidence) },
        { text: 'max hops ' + (options.max_hops == null ? '—' : options.max_hops) },
      ]);
      var warning = warnings.length
        ? '<div class="memory-insights-warning"><strong>Warnings</strong> ' + esc(warnings.join(' · ')) + '</div>'
        : '';
      target.innerHTML = '<div class="memory-insights-summary">' + summary + '</div>' +
        '<div class="memory-insights-columns">' +
          renderDiffusionSection('Seeds', seeds, 'seed') +
          renderDiffusionSection('Hits', hits, 'hit') +
        '</div>' + warning;
    }

    async function loadDiffusion(state, query) {
      query = String(query || '').trim();
      var target = state.root.querySelector('[data-role="results"]');
      if (!query) {
        target.innerHTML = stateBlock('error', 'A diffusion query is required.', false);
        return;
      }
      state.query = query;
      var token = ++state.request;
      var request = beginPanelRequest(state, 'main');
      target.innerHTML = stateBlock('loading', 'Tracing diffusion paths…', false);
      try {
        var data = await getJson('/api/diffusion-debug?q=' + encodeURIComponent(query), {
          signal: request.signal,
        });
        if (token !== state.request) return;
        renderDiffusion(state, data);
      } catch (error) {
        if (token !== state.request || requestWasAborted(error, request)) return;
        target.innerHTML = stateBlock('error', 'Diffusion diagnostics failed: ' + errorText(error), true);
      } finally {
        finishPanelRequest(state, 'main', request);
      }
    }

    function mountDiffusion(root) {
      var state = { root: root, request: 0, query: '' };
      prepareRoot(root, 'memory-diffusion-diagnostics', diffusionMarkup());
      bindPanelEvents(root, state, {
        retry: function () { loadDiffusion(state, state.query); },
      }, {
        diffusion: function (form) { loadDiffusion(state, form.elements.query.value); },
      });
      return state;
    }

    function clipText(value, limit) {
      var text = String(value || '');
      return text.length > limit ? text.slice(0, limit) + '…' : text;
    }

    function gatewayDirectRenderSummaries(payload) {
      var rows = Array.isArray(payload.recalled_moment_debug) ? payload.recalled_moment_debug : [];
      return rows.map(function (row) {
        var render = row.direct_render || {};
        if (!render.shape) return '';
        var label = row.bucket_name || row.bucket_id || row.moment_id || 'moment';
        return label + ' → ' + render.shape + (render.reason ? ' · ' + render.reason : '');
      }).filter(Boolean).slice(0, 5).join(' / ');
    }

    function gatewayChainDebugSummaries(payload) {
      var rows = Array.isArray(payload.diffused_moment_debug) ? payload.diffused_moment_debug : [];
      return rows.map(function (row) {
        var label = row.bucket_name || row.bucket_id || row.moment_id || 'moment';
        var parts = [];
        if (row.chain_bundle) parts.push('chain_bundle');
        if (row.note) parts.push(row.note);
        if (row.path && row.path.trace) parts.push(row.path.trace);
        var temperature = Array.isArray(row.temperature_context)
          ? row.temperature_context.map(function (item) {
            return item.label || item.section || '';
          }).filter(Boolean).join('+')
          : '';
        if (temperature) parts.push('temp ' + temperature);
        return parts.length ? label + ' → ' + parts.join(' · ') : '';
      }).filter(Boolean).slice(0, 5).join(' / ');
    }

    function gatewayStructuralActivationSummary(payload) {
      var planner = payload.query_planner_debug || {};
      var trace = payload.structural_activation_debug || planner.structural_activation_debug || {};
      var paths = Array.isArray(trace.paths) ? trace.paths : [];
      if ((!trace.enabled || trace.status === 'skipped') && !paths.length) return '';
      var summary = paths.map(function (path) {
        var seed = path.seed_term || 'term';
        if (path.seed_kind === 'category_seed') seed += ' [category]';
        var matched = path.matched_term && path.matched_term !== path.seed_term
          ? ' → ' + path.matched_term
          : '';
        var bucket = path.bucket_name || path.bucket_id || 'bucket';
        var decision = path.status || 'candidate';
        if (path.blocked_reason) decision += ': ' + path.blocked_reason;
        return seed + matched + ' → ' + bucket + ' [' + decision + ']';
      }).filter(Boolean).slice(0, 5).join(' / ');
      var finalReason = trace.final && trace.final.reason ? trace.final.reason : '';
      if (!summary) summary = 'no structural path';
      return 'shadow · ' + summary + (finalReason ? ' · ' + finalReason : '');
    }

    function gatewayMemoryEdgeActivationSummary(payload) {
      var planner = payload.query_planner_debug || {};
      var trace = payload.structural_activation_debug || planner.structural_activation_debug || {};
      var edges = trace.memory_edges || {};
      var paths = Array.isArray(edges.paths) ? edges.paths : [];
      if (!edges.status || edges.status === 'pending') return '';
      if (!paths.length) return edges.status + (edges.reason ? ' · ' + edges.reason : '');
      return edges.status + ' · ' + paths.map(function (path) {
        var seed = path.seed || {};
        var target = path.target || {};
        var relations = (path.steps || []).map(function (step) {
          return step.relation_type || 'relates_to';
        }).filter(Boolean).join(' → ');
        var final = path.final || {};
        var decision = final.status || ((path.gate || {}).allowed ? 'eligible' : 'blocked');
        if (final.suppression_reason) decision += ': ' + final.suppression_reason;
        return (seed.bucket_name || seed.bucket_id || 'seed') + ' → ' +
          (relations || 'relates_to') + ' → ' +
          (target.bucket_name || target.bucket_id || 'target') + ' [' + decision + ']';
      }).slice(0, 4).join(' / ');
    }

    function gatewayMomentChunkShadowSummary(payload) {
      var shadow = payload.moment_chunk_shadow_debug || {};
      var buckets = Array.isArray(shadow.buckets) ? shadow.buckets : [];
      if (!shadow.enabled || shadow.status === 'no_candidates') return '';
      var changed = buckets.filter(function (bucket) { return Boolean(bucket.changed); });
      if (!changed.length) {
        return buckets.length + ' candidate bucket' + (buckets.length === 1 ? '' : 's') + ' · unchanged';
      }
      return changed.map(function (bucket) {
        var shadowMoments = Array.isArray(bucket.shadow_moments)
          ? bucket.shadow_moments.filter(function (item) { return Boolean(item.split); })
          : [];
        var sizes = shadowMoments.map(function (item) { return item.chars || 0; }).join('/');
        return (bucket.bucket_name || bucket.bucket_id || 'bucket') + ' ' +
          (bucket.current_content_moment_count || 0) + ' → ' +
          (bucket.shadow_content_moment_count || 0) + (sizes ? ' [' + sizes + ']' : '');
      }).slice(0, 4).join(' / ');
    }

    function gatewayDynamicAnchorSummary(payload) {
      var planner = payload.query_planner_debug || {};
      var anchor = planner.dynamic_anchor || {};
      var discriminative = Array.isArray(anchor.discriminative_terms) ? anchor.discriminative_terms : [];
      var required = Array.isArray(anchor.required_terms) ? anchor.required_terms : [];
      var categories = Array.isArray(anchor.category_terms) ? anchor.category_terms : [];
      var aliases = Array.isArray(anchor.retrieval_alias_hits) ? anchor.retrieval_alias_hits : [];
      if (!discriminative.length && !categories.length && !aliases.length) return '';
      var parts = [];
      if (required.length) parts.push('required ' + required.join('/'));
      else if (discriminative.length) parts.push('anchors ' + discriminative.join('/'));
      if (categories.length) {
        parts.push((anchor.category_overview ? 'category overview ' : 'category ') + categories.join('/'));
      }
      if (aliases.length) {
        parts.push('alias ' + aliases.slice(0, 3).map(function (hit) {
          var name = hit.bucket_name || hit.bucket_id || 'bucket';
          var terms = Array.isArray(hit.terms) ? hit.terms.join('/') : '';
          return name + (terms ? ' [' + terms + ']' : '');
        }).join(' / '));
      }
      return parts.join(' · ');
    }

    function gatewaySemanticRescueSummary(payload) {
      var planner = payload.query_planner_debug || {};
      var rescue = planner.semantic_rescue || {};
      if (!rescue.enabled && !rescue.called && !rescue.error) return '';
      var parts = [];
      if (rescue.called) {
        var candidates = Array.isArray(rescue.candidate_bucket_ids) ? rescue.candidate_bucket_ids : [];
        parts.push('called' + (candidates.length ? ' [' + candidates.slice(0, 3).join('/') + ']' : ''));
      }
      if (rescue.selected_bucket_id) parts.push('selected ' + rescue.selected_bucket_id);
      if (rescue.matched_axis) parts.push('axis ' + rescue.matched_axis);
      if (rescue.direct_evidence_span) parts.push('evidence ' + String(rescue.direct_evidence_span).slice(0, 180));
      if (rescue.error) parts.push('error ' + rescue.error);
      if (!rescue.selected_bucket_id && rescue.skip_reason) parts.push('blocked ' + rescue.skip_reason);
      return parts.join(' · ');
    }

    function gatewayContextPreview(payload) {
      var parts = [];
      if (payload.recalled_memory) parts.push('Recalled Memory:\n' + clipText(payload.recalled_memory, 360));
      if (payload.diffused_memory) parts.push('Diffused Memory:\n' + clipText(payload.diffused_memory, 360));
      if (payload.dream_context) parts.push('Dream Context:\n' + clipText(payload.dream_context, 360));
      if (payload.dynamic_context) parts.push('Dynamic Context:\n' + clipText(payload.dynamic_context, 520));
      if (payload.stable_context) parts.push('Stable Context:\n' + clipText(payload.stable_context, 520));
      return parts.join('\n\n');
    }

    function gatewayMarkup() {
      return panelHeader(
        'Gateway Recent Injections',
        'Inspect recent memory-injection decisions. Raw context remains hidden unless you explicitly include it.',
        ''
      ) +
      '<form class="memory-insights-query memory-insights-query--gateway" data-submit="gateway">' +
        '<label><span>Session ID (optional)</span><input name="session_id" type="text" autocomplete="off" placeholder="Filter one session" /></label>' +
        '<label class="memory-insights-check"><input name="include_context" type="checkbox" />' +
          '<span>Include private context previews</span></label>' +
        '<button type="submit">Refresh Injections</button>' +
      '</form>' +
      '<div class="memory-insights-privacy">Metadata-only is the safe default. Context previews are clipped and only requested when checked.</div>' +
      '<div data-role="results" aria-live="polite">' + stateBlock('empty', 'Open this panel to read recent Gateway injections.', false) + '</div>';
    }

    function gatewayLine(label, value) {
      if (!value) return '';
      return '<div class="memory-insights-line"><strong>' + esc(label) + ':</strong> ' + esc(value) + '</div>';
    }

    function renderGatewayItem(item, includeContext) {
      var row = item || {};
      var payload = row.payload || {};
      var recalled = Array.isArray(payload.recalled_bucket_ids) ? payload.recalled_bucket_ids : [];
      var diffused = Array.isArray(payload.diffused_bucket_ids) ? payload.diffused_bucket_ids : [];
      var injected = Array.isArray(payload.injected_bucket_ids) ? payload.injected_bucket_ids : [];
      var recalledMoments = Array.isArray(payload.recalled_moment_ids) ? payload.recalled_moment_ids : [];
      var diffusedMoments = Array.isArray(payload.diffused_moment_ids) ? payload.diffused_moment_ids : [];
      var dreamStatus = payload.dream_context_status || {};
      var values = [
        { text: 'round ' + (row.round_id || '—') },
        { text: 'recalled ' + recalled.length, tone: recalled.length ? 'ok' : '' },
        { text: 'diffused ' + diffused.length, tone: diffused.length ? 'ok' : '' },
        { text: 'injected ' + injected.length, tone: injected.length ? 'ok' : '' },
        { text: payload.recent_context_injected ? 'recent yes' : 'recent no', tone: payload.recent_context_injected ? 'ok' : '' },
        { text: payload.date_persona_trace_injected ? 'date trace yes' : 'date trace no', tone: payload.date_persona_trace_injected ? 'ok' : '' },
      ];
      if (dreamStatus.status) {
        values.push({
          text: payload.dream_context_injected
            ? 'dream injected' + (dreamStatus.retained ? ' · retained' : '')
            : 'dream skipped' + (dreamStatus.reason ? ' · ' + dreamStatus.reason : ''),
          tone: payload.dream_context_injected ? 'ok' : '',
        });
      }
      if (payload.context_mode) values.push({ text: 'mode ' + payload.context_mode });

      var contextPreview = includeContext ? gatewayContextPreview(payload) : '';
      return '<article class="memory-insights-card memory-insights-card--gateway">' +
        '<div class="memory-insights-card-title"><strong>' + esc(row.session_id || 'session') +
          '</strong><small>' + esc(row.created_at || '') + '</small></div>' +
        chips(values) +
        '<div class="memory-insights-lines">' +
          gatewayLine('query', payload.query_preview || '—') +
          gatewayLine('buckets', injected.slice(0, 8).join(', ') || '—') +
          gatewayLine('moments', recalledMoments.concat(diffusedMoments).slice(0, 8).join(', ') || '—') +
          gatewayLine('direct render', gatewayDirectRenderSummaries(payload)) +
          gatewayLine('diffused chain', gatewayChainDebugSummaries(payload)) +
          gatewayLine('activation shadow', gatewayStructuralActivationSummary(payload)) +
          gatewayLine('memory edge shadow', gatewayMemoryEdgeActivationSummary(payload)) +
          gatewayLine('moment chunk shadow', gatewayMomentChunkShadowSummary(payload)) +
          gatewayLine('anchor gate', gatewayDynamicAnchorSummary(payload)) +
          gatewayLine('semantic rescue', gatewaySemanticRescueSummary(payload)) +
        '</div>' +
        (contextPreview
          ? '<details class="memory-insights-context"><summary>Private context preview</summary><pre>' +
            esc(contextPreview) + '</pre></details>'
          : '') +
      '</article>';
    }

    function renderGateway(state, data) {
      var target = state.root.querySelector('[data-role="results"]');
      if (!data || data.status !== 'ok') {
        target.innerHTML = stateBlock('error', data && data.error ? data.error : 'Gateway injections are unavailable.', true);
        return;
      }
      var items = Array.isArray(data.items) ? data.items : [];
      if (!items.length) {
        target.innerHTML = stateBlock('empty', 'No recent Gateway injection records.', false);
        return;
      }
      target.innerHTML = '<div class="memory-insights-list memory-insights-list--wide">' +
        items.map(function (item) { return renderGatewayItem(item, state.includeContext); }).join('') + '</div>';
    }

    async function loadGateway(state, values) {
      values = values || {};
      state.sessionId = String(values.sessionId == null ? state.sessionId : values.sessionId).trim();
      state.includeContext = values.includeContext == null
        ? state.includeContext
        : Boolean(values.includeContext);
      var token = ++state.request;
      var request = beginPanelRequest(state, 'main');
      var target = state.root.querySelector('[data-role="results"]');
      target.innerHTML = stateBlock('loading', 'Loading recent Gateway injections…', false);
      var path = '/api/gateway-injections?limit=10&include_context=' + (state.includeContext ? '1' : '0');
      if (state.sessionId) path += '&session_id=' + encodeURIComponent(state.sessionId);
      try {
        var data = await getJson(path, { signal: request.signal });
        if (token !== state.request) return;
        renderGateway(state, data);
      } catch (error) {
        if (token !== state.request || requestWasAborted(error, request)) return;
        target.innerHTML = stateBlock('error', 'Gateway injections failed to load: ' + errorText(error), true);
      } finally {
        finishPanelRequest(state, 'main', request);
      }
    }

    function mountGateway(root) {
      var state = { root: root, request: 0, sessionId: '', includeContext: false };
      prepareRoot(root, 'memory-gateway-injections', gatewayMarkup());
      bindPanelEvents(root, state, {
        retry: function () { loadGateway(state); },
      }, {
        gateway: function (form) {
          loadGateway(state, {
            sessionId: form.elements.session_id.value,
            includeContext: form.elements.include_context.checked,
          });
        },
      });
      return state;
    }

    var panelStates = {};

    app.registerPanel({
      id: 'memory-word-map',
      workspace: 'memory',
      label: 'Word Map Lite',
      order: 420,
      mount: function (root) { panelStates.wordMap = mountWordMap(root); },
      activate: function (context) {
        if (!panelStates.wordMap) return;
        activatePanelState(panelStates.wordMap, context);
        if (!panelStates.wordMap.map) return loadWordMap(panelStates.wordMap, false);
      },
      deactivate: function () {
        if (panelStates.wordMap) {
          panelStates.wordMap.request += 1;
          panelStates.wordMap.cardRequest += 1;
          deactivatePanelState(panelStates.wordMap);
        }
      },
    });

    app.registerPanel({
      id: 'memory-identity-semantics',
      workspace: 'memory',
      label: 'Identity Semantics',
      order: 430,
      mount: function (root) { panelStates.identity = mountIdentity(root); },
      activate: function (context) {
        if (!panelStates.identity) return;
        activatePanelState(panelStates.identity, context);
        if (!panelStates.identity.data) return loadIdentity(panelStates.identity, false);
      },
      deactivate: function () {
        if (panelStates.identity) {
          panelStates.identity.request += 1;
          deactivatePanelState(panelStates.identity);
        }
      },
    });

    app.registerPanel({
      id: 'memory-moment-diagnostics',
      workspace: 'memory',
      label: 'Moment Diagnostics',
      order: 440,
      mount: function (root) { panelStates.moments = mountMoments(root); },
      activate: function (params) {
        if (!panelStates.moments) return;
        activatePanelState(panelStates.moments, params);
        var bucketId = paramsValue(params, 'bucket_id') || paramsValue(params, 'bucketId');
        if (!bucketId) return;
        panelStates.moments.root.querySelector('input[name="bucket_id"]').value = bucketId;
        return loadMoments(panelStates.moments, bucketId);
      },
      deactivate: function () {
        if (panelStates.moments) {
          panelStates.moments.request += 1;
          deactivatePanelState(panelStates.moments);
        }
      },
    });

    app.registerPanel({
      id: 'memory-recall-diagnostics',
      workspace: 'memory',
      label: 'Recall Diagnostics',
      order: 450,
      mount: function (root) { panelStates.recall = mountRecall(root); },
      activate: function (params) {
        if (!panelStates.recall) return;
        activatePanelState(panelStates.recall, params);
        var query = paramsValue(params, 'q') || paramsValue(params, 'query');
        if (!query) return;
        panelStates.recall.root.querySelector('input[name="query"]').value = query;
        return loadRecall(panelStates.recall, query);
      },
      deactivate: function () {
        if (panelStates.recall) {
          panelStates.recall.request += 1;
          deactivatePanelState(panelStates.recall);
        }
      },
    });

    app.registerPanel({
      id: 'memory-diffusion-diagnostics',
      workspace: 'memory',
      label: 'Diffusion Diagnostics',
      order: 460,
      mount: function (root) { panelStates.diffusion = mountDiffusion(root); },
      activate: function (params) {
        if (!panelStates.diffusion) return;
        activatePanelState(panelStates.diffusion, params);
        var query = paramsValue(params, 'q') || paramsValue(params, 'query');
        if (!query) return;
        panelStates.diffusion.root.querySelector('input[name="query"]').value = query;
        return loadDiffusion(panelStates.diffusion, query);
      },
      deactivate: function () {
        if (panelStates.diffusion) {
          panelStates.diffusion.request += 1;
          deactivatePanelState(panelStates.diffusion);
        }
      },
    });

    app.registerPanel({
      id: 'memory-gateway-injections',
      workspace: 'memory',
      label: 'Gateway Injections',
      order: 470,
      mount: function (root) { panelStates.gateway = mountGateway(root); },
      activate: function (params) {
        if (!panelStates.gateway) return;
        activatePanelState(panelStates.gateway, params);
        var sessionId = paramsValue(params, 'session_id') || paramsValue(params, 'sessionId');
        var input = panelStates.gateway.root.querySelector('input[name="session_id"]');
        var includeInput = panelStates.gateway.root.querySelector('input[name="include_context"]');
        if (sessionId && input) input.value = sessionId;
        if (includeInput) includeInput.checked = false;
        return loadGateway(panelStates.gateway, {
          sessionId: sessionId || panelStates.gateway.sessionId,
          includeContext: false,
        });
      },
      deactivate: function () {
        if (panelStates.gateway) {
          panelStates.gateway.request += 1;
          deactivatePanelState(panelStates.gateway);
          panelStates.gateway.includeContext = false;
          var includeInput = panelStates.gateway.root.querySelector('input[name="include_context"]');
          if (includeInput) includeInput.checked = false;
        }
      },
    });
  });
}());
