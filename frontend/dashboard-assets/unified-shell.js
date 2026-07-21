(function initUnifiedDashboard(global) {
  'use strict';

  var document = global.document;
  var currentApp = null;
  var syncingLegacyClick = false;
  var pendingHandledLegacyPanel = '';
  var bucketDetailHydration = null;
  var lastPanelByWorkspace = Object.create(null);

  var LEGACY_PANELS = Object.freeze({
    'shared-buckets': {
      workspace: 'shared', tab: 'list', label: '记忆桶', order: 10,
      selector: '#list-view',
    },
    'shared-search': {
      workspace: 'shared', tab: 'list', label: '搜索', order: 11,
      selector: '#list-view', hiddenFromNav: true, focus: '#search-input',
    },
    'shared-breath': {
      workspace: 'shared', tab: 'breath', label: 'Breath 模拟', order: 20,
      selector: '#breath-view',
    },
    'shared-network': {
      workspace: 'shared', tab: 'network', label: '记忆网络', order: 30,
      selector: '#network-view',
    },
    'shared-import': {
      workspace: 'shared', tab: 'import', label: '导入', order: 40,
      selector: '#import-view',
    },
    'system-plans': {
      workspace: 'system', tab: 'plan', label: '计划', order: 20,
      selector: '#plan-view',
    },
    'system-letters': {
      workspace: 'system', tab: 'letters', label: '信', order: 30,
      selector: '#letters-view',
    },
    'system-anchors': {
      workspace: 'system', tab: 'anchors', label: '锚点', order: 40,
      selector: '#anchors-view',
    },
    'system-logs': {
      workspace: 'system', tab: 'logs', label: '日志', order: 50,
      selector: '#logs-view',
    },
    'system-status': {
      workspace: 'system', tab: 'settings', label: '设置', order: 10,
      selector: '#settings-view',
    },
    'system-replay-debug': {
      workspace: 'system', tab: 'v3-debug', label: 'Replay', order: 120,
      selector: '#v3-debug-view', hiddenFromNav: true,
    },
    'system-about': {
      workspace: 'system', tab: 'about', label: '关于', order: 130,
      selector: '#about-view',
    },
  });

  // Removed top-level tabs remain valid as bookmark/history aliases. They are
  // registered without navigation or content and immediately replace their URL
  // with the single canonical owner.
  var PANEL_ALIASES = Object.freeze({
    'models-compat-export': {
      workspace: 'models-data', targetWorkspace: 'system', targetPanel: 'system-status', section: 'sec-backup',
    },
    'models-github-backup': {
      workspace: 'models-data', targetWorkspace: 'system', targetPanel: 'system-status', section: 'sec-github',
    },
    'models-migration-tools': {
      workspace: 'models-data', targetWorkspace: 'system', targetPanel: 'system-status', section: 'sec-backup',
    },
    'system-errors': {
      workspace: 'system', targetWorkspace: 'system', targetPanel: 'system-logs',
    },
    'system-identity-settings': {
      workspace: 'system', targetWorkspace: 'system', targetPanel: 'system-status', section: 'sec-me',
    },
    'system-auth-settings': {
      workspace: 'system', targetWorkspace: 'system', targetPanel: 'system-status', section: 'sec-me',
    },
    'system-mcp-settings': {
      workspace: 'system', targetWorkspace: 'system', targetPanel: 'system-status', section: 'sec-mcp',
    },
    'system-transport-settings': {
      workspace: 'system', targetWorkspace: 'system', targetPanel: 'system-status', section: 'sec-mcp',
    },
    'system-env-settings': {
      workspace: 'system', targetWorkspace: 'system', targetPanel: 'system-status', section: 'sec-env',
    },
    'system-tunnel-settings': {
      workspace: 'system', targetWorkspace: 'system', targetPanel: 'system-status', section: 'sec-me',
    },
    'system-diagnostics': {
      workspace: 'system', targetWorkspace: 'system', targetPanel: 'system-status', section: 'sec-service',
    },
    'system-version-update': {
      workspace: 'system', targetWorkspace: 'system', targetPanel: 'system-status', section: 'sec-version',
    },
    'system-restart-controls': {
      workspace: 'system', targetWorkspace: 'system', targetPanel: 'system-status', section: 'sec-service',
    },
    'system-developer': {
      workspace: 'system', targetWorkspace: 'system', targetPanel: 'system-status', section: 'sec-dev-mode',
    },
  });

  var SECTION_PANELS = Object.freeze({
    'sec-version': 'system-status',
    'sec-me': 'system-status',
    'sec-service': 'system-status',
    'sec-engine': 'system-status',
    'sec-bucket': 'system-status',
    'sec-github': 'system-status',
    'sec-backup': 'system-status',
    'sec-env': 'system-status',
    'sec-mcp': 'system-status',
    'sec-dev-mode': 'system-status',
  });

  function validatedLegacySection(panelId, sectionValue) {
    var section = String(sectionValue || '').trim();
    return section && SECTION_PANELS[section] === panelId ? section : '';
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function createUi() {
    return Object.freeze({
      escape: escapeHtml,
      escapeAttr: escapeHtml,
      setStatus: function setStatus(element, message, tone) {
        if (!element) return;
        element.textContent = message || '';
        element.dataset.tone = tone || 'neutral';
        element.setAttribute('aria-live', 'polite');
      },
      setRestartRequired: function setRestartRequired(required, reason) {
        if (typeof global.setRestartRequired === 'function') {
          global.setRestartRequired(Boolean(required), reason || '');
        }
      },
      confirm: function confirmAction(message, detail) {
        var text = String(message || '确认继续？');
        if (detail) text += '\n\n' + String(detail);
        return typeof global.confirm === 'function' && global.confirm(text);
      },
    });
  }

  function createEventBus() {
    var listeners = new Map();
    return Object.freeze({
      on: function on(name, handler) {
        if (typeof handler !== 'function') return function noop() {};
        if (!listeners.has(name)) listeners.set(name, new Set());
        listeners.get(name).add(handler);
        return function unsubscribe() { listeners.get(name).delete(handler); };
      },
      emit: function emit(name, detail) {
        var group = listeners.get(name);
        if (!group) return;
        Array.from(group).forEach(function notify(handler) { handler(detail || {}); });
      },
    });
  }

  function safeAssetUrl(app, name) {
    var value = String(name == null ? '' : name).replace(/\\/g, '/');
    if (!value || value.startsWith('/') || /(^|\/)\.\.($|\/)/.test(value) || /^[a-z][a-z\d+.-]*:/i.test(value)) {
      throw new TypeError('Invalid Dashboard asset path');
    }
    return app.env.asset('dashboard-assets/' + value);
  }

  function definitionRoot(definition) {
    if (!definition) return null;
    if (typeof definition.root === 'object' && definition.root) return definition.root;
    if (typeof definition.root === 'string') return document.querySelector(definition.root);
    if (definition.selector) return document.querySelector(definition.selector);
    return document.querySelector('[data-panel="' + definition.id + '"]');
  }

  function resolvePanelRoot(definition) {
    if (definition && definition.requiresRoot === false) return null;
    var existing = definitionRoot(definition);
    if (existing) return existing;

    var section = document.createElement('section');
    section.className = 'content unified-panel';
    section.id = 'unified-panel-' + definition.id;
    section.dataset.panel = definition.id;
    section.dataset.workspace = definition.workspace;
    section.setAttribute('role', 'tabpanel');
    section.setAttribute('tabindex', '-1');
    section.hidden = true;
    section.style.display = 'none';

    var boundary = document.getElementById('feedback-modal')
      || document.getElementById('error-alert-popup')
      || document.getElementById('self-overlay');
    document.body.insertBefore(section, boundary || null);
    return section;
  }

  function registerLegacyPanels(app) {
    Object.keys(LEGACY_PANELS).forEach(function register(panelId) {
      var legacy = LEGACY_PANELS[panelId];
      var definition = {
        id: panelId,
        workspace: legacy.workspace,
        label: legacy.label,
        order: legacy.order,
        selector: legacy.selector,
        hiddenFromNav: Boolean(legacy.hiddenFromNav),
        activate: function activate(context) {
          return activateLegacyPanel(app, panelId, context);
        },
      };
      app.registerPanel(definition);
    });
  }

  function registerPanelAliases(app) {
    Object.keys(PANEL_ALIASES).forEach(function register(aliasId) {
      var alias = PANEL_ALIASES[aliasId];
      app.registerPanel({
        id: aliasId,
        workspace: alias.workspace,
        label: aliasId,
        order: 10000,
        hiddenFromNav: true,
        requiresRoot: false,
        activate: function activateAlias() {
          var params = alias.section ? { section: alias.section } : {};
          return app.router.replace(alias.targetWorkspace, alias.targetPanel, params);
        },
      });
    });
  }

  function matchingPanelForTab(tabName) {
    var panelId = Object.keys(LEGACY_PANELS).find(function find(id) {
      return LEGACY_PANELS[id].tab === tabName && !LEGACY_PANELS[id].hiddenFromNav;
    });
    return panelId || 'system-status';
  }

  function revealSettingsSection(sectionId) {
    if (!sectionId) return;
    var section = document.getElementById(sectionId);
    if (!section) return;
    var group = section.getAttribute('data-sgroup');
    if (group && typeof global.showSettingsGroup === 'function') {
      global.showSettingsGroup(group);
    }
    global.requestAnimationFrame(function scrollToSection() {
      section.scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
  }

  function hydrateBucketDetail(context) {
    var bucketId = String(context && context.bucketId || '').trim();
    if (!bucketId || typeof global.showDetail !== 'function') return undefined;
    if (context && context.signal && context.signal.aborted) return undefined;
    if (bucketDetailHydration && bucketDetailHydration.id === bucketId) {
      return bucketDetailHydration.promise;
    }

    var hydration = { id: bucketId, promise: null };
    var detail;
    try {
      detail = global.showDetail(bucketId);
    } catch (error) {
      return Promise.reject(error);
    }
    hydration.promise = Promise.resolve(detail).then(
      function hydrated(value) {
        if (bucketDetailHydration === hydration) bucketDetailHydration = null;
        return value;
      },
      function hydrationFailed(error) {
        if (bucketDetailHydration === hydration) bucketDetailHydration = null;
        throw error;
      }
    );
    bucketDetailHydration = hydration;
    return hydration.promise;
  }

  function activateLegacyPanel(app, panelId, context) {
    var legacy = LEGACY_PANELS[panelId];
    if (!legacy) return;
    var sideEffectsAlreadyHandled = pendingHandledLegacyPanel === panelId;
    pendingHandledLegacyPanel = '';
    var tab = document.querySelector('.tab[data-tab="' + legacy.tab + '"]');
    if (tab && !sideEffectsAlreadyHandled) {
      syncingLegacyClick = true;
      try { tab.click(); } finally { syncingLegacyClick = false; }
    }
    applyPanelTabState(panelId);

    var section = validatedLegacySection(panelId, context && context.section) || legacy.section;
    if (section) revealSettingsSection(section);
    if (panelId === 'shared-search') {
      var query = String(context && context.q || '').trim();
      var searchInput = document.getElementById('search-input');
      if (searchInput) searchInput.value = query;
      if (query && typeof global.searchBuckets === 'function') {
        return global.searchBuckets(query, context && context.signal);
      }
      if (typeof global.cancelBucketSearch === 'function') global.cancelBucketSearch();
      if (typeof global.renderBuckets === 'function' && typeof global.filterBuckets === 'function') {
        global.renderBuckets(global.filterBuckets(global.allBuckets || []));
      }
      return undefined;
    }
    if (typeof global.cancelBucketSearch === 'function') global.cancelBucketSearch();
    if (panelId === 'shared-buckets') return hydrateBucketDetail(context);
    if (legacy.focus) {
      var focusTarget = document.querySelector(legacy.focus);
      if (focusTarget) focusTarget.focus();
    }
  }

  function configureApp(app) {
    app.ui = createUi();
    app.events = createEventBus();
    app.assetUrl = function assetUrl(name) { return safeAssetUrl(app, name); };

    app.commands.openLegacyPanel = function openLegacyPanel(tab, section) {
      var requestedSection = String(section || '').trim();
      var sectionPanel = SECTION_PANELS[requestedSection];
      var panelId = sectionPanel || matchingPanelForTab(tab);
      var definition = app.panels.get(panelId);
      if (!definition) throw new Error('Legacy Dashboard panel is unavailable: ' + panelId);
      return app.router.go(
        definition.workspace,
        panelId,
        sectionPanel ? { section: requestedSection } : {}
      );
    };
    app.commands.refreshBuckets = function refreshBuckets() {
      return typeof global.loadBuckets === 'function' ? global.loadBuckets() : undefined;
    };
    app.commands.openBucket = function openBucket(bucketId) {
      var id = String(bucketId || '').trim();
      if (!id) return undefined;
      return app.router.go('shared', 'shared-buckets', { bucketId: id });
    };
    app.commands.search = function search(queryValue) {
      var query = String(queryValue || '').trim();
      if (!query) return app.router.go('shared', 'shared-buckets', {});
      return app.router.go('shared', 'shared-search', { q: query });
    };
    app.events.on('bucket:open', function onBucketOpen(detail) {
      app.commands.openBucket(detail && (detail.id || detail.bucketId));
    });
  }

  function panelSort(left, right) {
    var leftOrder = Number(left.order || 1000);
    var rightOrder = Number(right.order || 1000);
    return leftOrder - rightOrder || String(left.label || left.id).localeCompare(String(right.label || right.id));
  }

  function tabForPanel(panelId) {
    return document.querySelector('.tab[data-panel-id="' + panelId + '"]');
  }

  function navigateToPanel(app, panelId) {
    var definition = app.panels.get(panelId);
    if (!definition) return;
    var current = app.router.current();
    if (current && current.panel === panelId) {
      syncChrome(app, current);
      return;
    }
    app.router.go(definition.workspace, panelId, {});
  }

  function bindRovingTablist(container, selector) {
    if (!container || container.dataset.unifiedRovingBound === 'true') return;
    container.dataset.unifiedRovingBound = 'true';
    container.setAttribute('role', 'tablist');
    container.addEventListener('keydown', function moveTabFocus(event) {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      var source = event.target && event.target.closest ? event.target.closest(selector) : null;
      if (!source || !container.contains(source)) return;
      var tabs = Array.from(container.querySelectorAll(selector)).filter(function visible(tab) {
        return !tab.hidden && tab.getAttribute('aria-disabled') !== 'true';
      });
      if (!tabs.length) return;
      var index = Math.max(0, tabs.indexOf(source));
      if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = tabs.length - 1;
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') index = (index - 1 + tabs.length) % tabs.length;
      else index = (index + 1) % tabs.length;
      event.preventDefault();
      tabs[index].focus();
      tabs[index].click();
    });
  }

  function bindPanelTab(app, tab) {
    if (tab.dataset.unifiedBound === 'true') return;
    tab.dataset.unifiedBound = 'true';
    tab.type = 'button';
    tab.setAttribute('role', 'tab');
    tab.id = tab.id || 'dashboard-tab-' + tab.dataset.panelId;
    tab.setAttribute('aria-selected', tab.classList.contains('active') ? 'true' : 'false');
    tab.setAttribute('tabindex', tab.classList.contains('active') ? '0' : '-1');
    var definition = app.panels.get(tab.dataset.panelId);
    var target = definitionRoot(definition);
    if (target) {
      target.id = target.id || 'unified-panel-' + definition.id;
      tab.setAttribute('aria-controls', target.id);
      target.setAttribute('role', 'tabpanel');
      target.setAttribute('aria-labelledby', tab.id);
    }
    tab.addEventListener('click', function routePanel() {
      if (syncingLegacyClick) return;
      var panelId = tab.dataset.panelId;
      var legacy = LEGACY_PANELS[panelId];
      var current = app.router.current();
      var handledByLegacyListener = Boolean(
        legacy
        && tab.dataset.tab === legacy.tab
        && (!current || current.panel !== panelId)
      );
      if (handledByLegacyListener) pendingHandledLegacyPanel = panelId;
      try {
        navigateToPanel(app, panelId);
      } catch (error) {
        if (pendingHandledLegacyPanel === panelId) pendingHandledLegacyPanel = '';
        throw error;
      }
    });
  }

  function ensurePanelTab(app, definition, tabContainer, spacer) {
    resolvePanelRoot(definition);
    var tab = tabForPanel(definition.id);
    if (!tab && !definition.hiddenFromNav) {
      tab = document.createElement('button');
      tab.type = 'button';
      tab.className = 'tab';
      tab.dataset.panelId = definition.id;
      tab.dataset.workspace = definition.workspace;
      tab.setAttribute('data-unified-panel-tab', 'true');
      tab.textContent = definition.label || definition.id;
      tabContainer.insertBefore(tab, spacer || null);
    }
    if (tab) bindPanelTab(app, tab);
    if (!lastPanelByWorkspace[definition.workspace] && !definition.hiddenFromNav) {
      lastPanelByWorkspace[definition.workspace] = definition.id;
    }
  }

  function buildPanelNavigation(app) {
    var tabContainer = document.getElementById('panel-tabs');
    if (!tabContainer) throw new Error('Unified Dashboard panel navigation is missing');
    var spacer = tabContainer.querySelector('.tab-spacer');
    var definitions = Array.from(app.panels.values()).sort(panelSort);

    definitions.forEach(function addPanel(definition) {
      ensurePanelTab(app, definition, tabContainer, spacer);
    });
    bindRovingTablist(tabContainer, '.tab[data-panel-id]');
  }

  function applyPanelTabState(panelId) {
    document.querySelectorAll('#panel-tabs .tab[data-panel-id]').forEach(function update(tab) {
      var selected = tab.dataset.panelId === panelId;
      tab.classList.toggle('active', selected);
      tab.setAttribute('aria-selected', selected ? 'true' : 'false');
      tab.setAttribute('tabindex', selected ? '0' : '-1');
    });
  }

  function revealPanelRoot(app, state) {
    document.querySelectorAll('.content').forEach(function hide(panel) {
      panel.style.display = 'none';
      panel.hidden = true;
      panel.setAttribute('aria-hidden', 'true');
      panel.classList.remove('active');
    });
    var definition = app.panels.get(state.panel);
    var target = definitionRoot(definition);
    if (!target) return;
    var activeTab = tabForPanel(state.panel);
    target.setAttribute('role', 'tabpanel');
    if (activeTab) target.setAttribute('aria-labelledby', activeTab.id);
    target.hidden = false;
    target.style.display = '';
    target.setAttribute('aria-hidden', 'false');
    target.classList.add('active');
  }

  function syncChrome(app, state) {
    if (!state || !app.panels.has(state.panel)) return;
    if (pendingHandledLegacyPanel && pendingHandledLegacyPanel !== state.panel) {
      pendingHandledLegacyPanel = '';
    }
    lastPanelByWorkspace[state.workspace] = state.panel;
    document.body.dataset.activeWorkspace = state.workspace;
    document.body.dataset.activePanel = state.panel;

    document.querySelectorAll('#workspace-tabs .workspace-tab[data-workspace]').forEach(function update(button) {
      var selected = button.dataset.workspace === state.workspace;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-selected', selected ? 'true' : 'false');
      button.setAttribute('tabindex', selected ? '0' : '-1');
    });
    document.querySelectorAll('#panel-tabs .tab[data-workspace]').forEach(function filter(tab) {
      tab.hidden = tab.dataset.workspace !== state.workspace;
    });
    applyPanelTabState(state.panel);
    revealPanelRoot(app, state);

    document.querySelectorAll('[data-unified-jump]').forEach(function mark(link) {
      link.setAttribute('aria-current', link.dataset.unifiedJump === state.panel ? 'page' : 'false');
    });
  }

  function bindWorkspaceNavigation(app) {
    var workspaceTabs = document.getElementById('workspace-tabs');
    document.querySelectorAll('#workspace-tabs .workspace-tab[data-workspace]').forEach(function bind(button) {
      button.type = 'button';
      button.setAttribute('role', 'tab');
      button.id = button.id || 'workspace-tab-' + button.dataset.workspace;
      button.setAttribute('aria-controls', 'panel-tabs');
      button.addEventListener('click', function selectWorkspace() {
        var workspace = button.dataset.workspace;
        var panelId = lastPanelByWorkspace[workspace];
        var definition = panelId && app.panels.get(panelId);
        if (!definition || definition.hiddenFromNav) {
          definition = Array.from(app.panels.values())
            .filter(function inWorkspace(item) { return item.workspace === workspace && !item.hiddenFromNav; })
            .sort(panelSort)[0];
        }
        if (definition) navigateToPanel(app, definition.id);
      });
    });
    bindRovingTablist(workspaceTabs, '.workspace-tab[data-workspace]');

    document.querySelectorAll('[data-unified-jump]').forEach(function bind(link) {
      link.addEventListener('click', function jump(event) {
        var panelId = link.dataset.unifiedJump;
        if (!app.panels.has(panelId)) return;
        event.preventDefault();
        navigateToPanel(app, panelId);
      });
    });
  }

  function reportFatal(error) {
    var panel = document.createElement('div');
    panel.className = 'unified-shell-fatal';
    panel.setAttribute('role', 'alert');
    panel.textContent = 'Dashboard failed to start: ' + (error && error.message ? error.message : String(error));
    var nav = document.getElementById('workspace-tabs');
    document.body.insertBefore(panel, nav ? nav.nextSibling : document.body.firstChild);
  }

  async function boot() {
    var authenticated = await global.OmbreDashboardAuthReady;
    if (authenticated === undefined && typeof global.checkAuth === 'function') {
      authenticated = await global.checkAuth();
    }
    if (!authenticated) {
      document.documentElement.dataset.unifiedDashboardAuth = 'required';
      return;
    }
    if (!global.OmbreDashboardApp || typeof global.OmbreDashboardApp.createDashboardApp !== 'function') {
      throw new Error('Unified Dashboard core did not load');
    }
    var app = global.OmbreDashboardApp.createDashboardApp({
      root: document,
      resolvePanelRoot: resolvePanelRoot,
      onUnauthorized: function onUnauthorized() {
        if (typeof global.handleDashboardUnauthorized === 'function') {
          return global.handleDashboardUnauthorized();
        }
        return undefined;
      },
      onError: function onError(error, context) {
        if (global.console && typeof global.console.error === 'function') {
          global.console.error('Unified Dashboard error', context, error);
        }
      },
    });
    currentApp = app;
    configureApp(app);
    registerLegacyPanels(app);
    registerPanelAliases(app);
    await app.loadQueuedFeatures();
    buildPanelNavigation(app);
    if (typeof app.onPanelRegistered === 'function') {
      app.onPanelRegistered(function panelRegistered(definition) {
        var tabContainer = document.getElementById('panel-tabs');
        var spacer = tabContainer && tabContainer.querySelector('.tab-spacer');
        if (tabContainer) ensurePanelTab(app, definition, tabContainer, spacer);
        syncChrome(app, app.router.current());
      });
    }
    app.router.onChange(function routeChanged(state) { syncChrome(app, state); });
    bindWorkspaceNavigation(app);
    await app.init();
    if (global.OmbreDashboardAuthenticated === false) {
      currentApp = null;
      await app.destroy();
      document.documentElement.dataset.unifiedDashboardAuth = 'required';
      return;
    }
    global.OmbreDashboard = app;
    document.documentElement.dataset.unifiedDashboardAuth = 'authenticated';
    document.documentElement.dataset.unifiedDashboardReady = 'true';
    if (typeof global.loadBuckets === 'function') global.loadBuckets();
    if (typeof global.loadStatusBanner === 'function') global.loadStatusBanner();
  }

  function start() {
    boot().catch(function onBootError(error) {
      if (global.console && typeof global.console.error === 'function') global.console.error(error);
      reportFatal(error);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})(window);
