(function initDashboardAppShell(global) {
  "use strict";

  const core = global.OmbreDashboardCore = global.OmbreDashboardCore || {};
  const featureFactories = global.OmbreDashboardFeatureFactories =
    global.OmbreDashboardFeatureFactories || [];
  const dashboardModules = global.OmbreDashboardModules =
    global.OmbreDashboardModules || [];
  const globalPanelDefinitions = [];
  let defaultApp = null;

  function validIdentifier(value) {
    return typeof value === "string" && /^[a-z0-9][a-z0-9-]*$/.test(value);
  }

  function reportError(app, error, context) {
    app.lastError = { error, context, at: new Date().toISOString() };
    if (typeof app.options.onError === "function") {
      app.options.onError(error, context, app);
      return;
    }
    if (global.document && typeof global.CustomEvent === "function") {
      global.document.dispatchEvent(new CustomEvent("ombre-dashboard-error", {
        detail: { error, context },
      }));
    }
  }

  async function runSafely(app, operation, context) {
    try {
      return await operation();
    } catch (error) {
      if (!(error && error.name === "AbortError")) reportError(app, error, context);
      return undefined;
    }
  }

  function abortError(message) {
    if (typeof global.DOMException === "function") {
      return new global.DOMException(message || "Dashboard transition superseded", "AbortError");
    }
    const error = new Error(message || "Dashboard transition superseded");
    error.name = "AbortError";
    return error;
  }

  function runUntilAborted(operation, signal) {
    if (!signal) return Promise.resolve().then(operation);
    if (signal.aborted) return Promise.reject(signal.reason || abortError());
    return new Promise((resolve, reject) => {
      const onAbort = () => {
        reject(signal.reason || abortError());
      };
      signal.addEventListener("abort", onAbort, { once: true });
      Promise.resolve()
        .then(operation)
        .then(resolve, reject)
        .finally(() => signal.removeEventListener("abort", onAbort));
    });
  }

  function escapeHtml(value) {
    const replacements = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;",
      "`": "&#96;",
    };
    return String(value === undefined || value === null ? "" : value)
      .replace(/[&<>"'`]/g, (character) => replacements[character]);
  }

  function createUiAdapter(settings) {
    if (settings.ui) return settings.ui;
    return Object.freeze({
      escape: escapeHtml,
      escapeAttr: escapeHtml,
      setStatus(element, message, tone) {
        if (!element) return null;
        element.textContent = String(message === undefined || message === null ? "" : message);
        if (element.dataset) {
          if (tone) element.dataset.tone = String(tone);
          else delete element.dataset.tone;
        }
        return element;
      },
      async confirm(message) {
        const confirmImpl = settings.confirm || global.confirm;
        if (typeof confirmImpl !== "function") return false;
        return Boolean(await confirmImpl(String(message || "")));
      },
    });
  }

  function createDashboardApp(options) {
    const settings = options || {};
    if (
      typeof core.createPathEnv !== "function"
      || typeof core.createApiClient !== "function"
      || typeof core.createRouter !== "function"
      || typeof core.createDashboardStore !== "function"
    ) {
      throw new Error("Dashboard core assets must load before app-shell.js");
    }

    const env = settings.env || core.createPathEnv({
      location: settings.location || global.location,
      pathname: settings.pathname,
      origin: settings.origin,
      mountPrefix: settings.mountPrefix,
      defaultBootMode: settings.bootMode,
    });
    const api = settings.api || core.createApiClient({
      pathEnv: env,
      fetchImpl: settings.fetchImpl,
      onUnauthorized: settings.onUnauthorized,
      defaultTimeoutMs: settings.defaultTimeoutMs,
      defaultRetries: settings.defaultRetries,
      retryDelayMs: settings.retryDelayMs,
    });
    const router = settings.router || core.createRouter({
      pathEnv: env,
      bootMode: settings.bootMode || env.bootMode,
      manifest: settings.manifest,
      location: settings.location || global.location,
      history: settings.history || global.history,
      eventTarget: settings.eventTarget || global,
    });
    const store = settings.store || core.createDashboardStore({ api });
    const ui = createUiAdapter(settings);
    const panels = new Map();
    const commands = Object.create(null);
    const mountedPanels = new Map();
    const processedFeatures = new Set();
    const panelListeners = new Set();
    const initHooks = { before: [], after: [] };
    let root = settings.root || global.document || null;
    let initialized = false;
    let activePanel = null;
    let transition = Promise.resolve();
    let transitionGeneration = 0;
    let activeTransitionController = null;
    let unsubscribeRouter = null;

    const app = {
      options: settings,
      env,
      api,
      auth: settings.auth || null,
      router,
      store,
      ui,
      apiUrl: (path) => env.api(path),
      assetUrl: (path) => env.asset(
        String(path || "").startsWith("dashboard-assets/")
          ? path
          : `dashboard-assets/${String(path || "").replace(/^\/+/, "")}`,
      ),
      commands,
      panels,
      lastError: null,

      registerPanel(definition) {
        if (!definition || !validIdentifier(definition.id)) {
          throw new TypeError("Panel id must be a lowercase Dashboard identifier");
        }
        if (!validIdentifier(definition.workspace)) {
          throw new TypeError("Panel workspace must be a lowercase Dashboard identifier");
        }
        if (panels.has(definition.id)) {
          if (panels.get(definition.id) === definition) return definition;
          throw new Error(`Dashboard panel already registered: ${definition.id}`);
        }
        panels.set(definition.id, definition);
        if (typeof router.registerPanel === "function") {
          router.registerPanel(definition.id, definition.workspace);
        }
        for (const listener of Array.from(panelListeners)) {
          runSafely(
            app,
            () => listener(definition, app),
            { phase: "panel-registered", panel: definition.id },
          );
        }
        if (app.events && typeof app.events.emit === "function") {
          app.events.emit("panel:registered", { panel: definition });
        }
        if (global.document && typeof global.CustomEvent === "function") {
          global.document.dispatchEvent(new CustomEvent("ombre-dashboard-panel-registered", {
            detail: { panel: definition, app },
          }));
        }
        return definition;
      },

      // Host chrome subscribes here to create navigation for features queued
      // after the first paint.  `{ replay: true }` also visits existing panels.
      onPanelRegistered(listener, listenerOptions) {
        if (typeof listener !== "function") {
          throw new TypeError("Panel registration listener must be a function");
        }
        panelListeners.add(listener);
        if (listenerOptions && listenerOptions.replay) {
          for (const definition of panels.values()) listener(definition, app);
        }
        return () => panelListeners.delete(listener);
      },

      registerCommand(name, handler) {
        if (!validIdentifier(name) || typeof handler !== "function") {
          throw new TypeError("Dashboard command requires a valid name and handler");
        }
        if (Object.prototype.hasOwnProperty.call(commands, name)) {
          throw new Error(`Dashboard command already registered: ${name}`);
        }
        commands[name] = handler;
        return () => {
          if (commands[name] === handler) delete commands[name];
        };
      },

      addInitHook(stage, hook) {
        let phase = stage;
        let callback = hook;
        if (typeof stage === "function") {
          phase = "before";
          callback = stage;
        }
        if (!Object.prototype.hasOwnProperty.call(initHooks, phase) || typeof callback !== "function") {
          throw new TypeError("Init hook stage must be before or after");
        }
        initHooks[phase].push(callback);
        return () => {
          const at = initHooks[phase].indexOf(callback);
          if (at !== -1) initHooks[phase].splice(at, 1);
        };
      },

      // A completed batch additionally emits `features:loaded` on an optional
      // app event bus and `ombre-dashboard-features-loaded` on `document`.
      async loadQueuedFeatures() {
        const before = processedFeatures.size;
        const queues = [globalPanelDefinitions, featureFactories, dashboardModules];
        for (const queue of queues) {
          for (let index = 0; index < queue.length; index += 1) {
            const feature = queue[index];
            if (!feature || processedFeatures.has(feature)) continue;
            processedFeatures.add(feature);
            await runSafely(app, async () => {
              if (typeof feature === "function") await feature(app);
              else app.registerPanel(feature);
            }, { phase: "feature-registration", feature });
          }
        }
        if (processedFeatures.size > before) {
          if (typeof settings.onFeaturesLoaded === "function") {
            await runSafely(app, () => settings.onFeaturesLoaded(app), {
              phase: "features-loaded",
            });
          }
          if (app.events && typeof app.events.emit === "function") {
            app.events.emit("features:loaded", { app });
          }
          if (global.document && typeof global.CustomEvent === "function") {
            global.document.dispatchEvent(new CustomEvent("ombre-dashboard-features-loaded", {
              detail: { app },
            }));
          }
        }
        return app;
      },

      async init(initOptions) {
        if (initialized) return app;
        initialized = true;
        if (initOptions && initOptions.root) root = initOptions.root;
        for (const hook of initHooks.before) {
          await runSafely(app, () => hook(app), { phase: "before-init" });
        }
        await app.loadQueuedFeatures();
        unsubscribeRouter = router.onChange((next, previous, cause) => {
          const generation = ++transitionGeneration;
          if (activeTransitionController) activeTransitionController.abort();
          const controller = new AbortController();
          activeTransitionController = controller;
          if (activePanel && activePanel.id !== next.panel) {
            store.abortScope(`panel:${activePanel.id}`);
          }
          transition = transition.then(async () => {
            if (generation !== transitionGeneration) return undefined;
            try {
              return await activatePanel(next, previous, cause, generation, controller);
            } finally {
              if (activeTransitionController === controller) {
                activeTransitionController = null;
              }
            }
          });
        });
        router.start();
        await transition;
        for (const hook of initHooks.after) {
          await runSafely(app, () => hook(app), { phase: "after-init" });
        }
        return app;
      },

      async destroy() {
        transitionGeneration += 1;
        if (unsubscribeRouter) unsubscribeRouter();
        unsubscribeRouter = null;
        router.stop();
        if (activeTransitionController) activeTransitionController.abort();
        activeTransitionController = null;
        await transition;
        store.abortScope(activePanel ? `panel:${activePanel.id}` : null);
        if (activePanel) {
          await callPanel(activePanel, "deactivate", { reason: "destroy" });
        }
        for (const [panelId, mounted] of mountedPanels.entries()) {
          const definition = panels.get(panelId);
          if (definition && typeof definition.unmount === "function") {
            await runSafely(
              app,
              () => definition.unmount(mounted.root, app),
              { phase: "panel-unmount", panel: definition.id },
            );
          }
        }
        mountedPanels.clear();
        activePanel = null;
        initialized = false;
      },
    };

    function panelRoot(definition) {
      if (typeof settings.resolvePanelRoot === "function") {
        return settings.resolvePanelRoot(definition, root, app);
      }
      if (definition.root && typeof definition.root !== "string") return definition.root;
      if (!root || typeof root.querySelector !== "function") return null;
      const selector = typeof definition.root === "string"
        ? definition.root
        : definition.selector || `[data-panel="${definition.id}"]`;
      return root.querySelector(selector);
    }

    async function callPanel(definition, method, context, signal) {
      if (typeof definition[method] !== "function") return undefined;
      return runSafely(
        app,
        () => runUntilAborted(() => definition[method](context, app), signal),
        { phase: `panel-${method}`, panel: definition.id },
      );
    }

    async function ensureMounted(definition, signal) {
      if (mountedPanels.has(definition.id)) return mountedPanels.get(definition.id);
      const target = panelRoot(definition);
      if (!target && definition.requiresRoot !== false) {
        return { root: null, pending: true };
      }
      if (typeof definition.mount === "function") {
        await runSafely(
          app,
          () => runUntilAborted(
            () => definition.mount(target, app, { signal }),
            signal,
          ),
          { phase: "panel-mount", panel: definition.id },
        );
        if (signal && signal.aborted) return { root: target, cancelled: true };
      }
      const mounted = { root: target };
      mountedPanels.set(definition.id, mounted);
      return mounted;
    }

    async function activatePanel(next, previous, cause, generation, controller) {
      const signal = controller.signal;
      if (generation !== transitionGeneration || signal.aborted) return;
      const definition = panels.get(next.panel);
      if (!definition) return;
      if (activePanel && activePanel.id !== definition.id) {
        store.abortScope(`panel:${activePanel.id}`);
        await callPanel(activePanel, "deactivate", {
          state: previous,
          next,
          cause,
          root: mountedPanels.get(activePanel.id)?.root || null,
          signal,
        }, signal);
        if (generation !== transitionGeneration || signal.aborted) return;
      }
      const mounted = await ensureMounted(definition, signal);
      if (mounted.pending || mounted.cancelled || generation !== transitionGeneration || signal.aborted) return;
      activePanel = definition;
      const activation = Object.assign(Object.create(null), next.params || {}, {
        state: next,
        previous,
        cause,
        root: mounted.root,
        scopeId: `panel:${definition.id}`,
        signal,
      });
      await callPanel(definition, "activate", activation, signal);
    }

    defaultApp = app;
    return app;
  }

  const publicApi = global.OmbreDashboardApp || {};
  publicApi.createDashboardApp = createDashboardApp;
  publicApi.registerPanel = function registerPanel(definition) {
    if (defaultApp) return defaultApp.registerPanel(definition);
    globalPanelDefinitions.push(definition);
    return definition;
  };
  publicApi.queueFeature = function queueFeature(factory) {
    if (typeof factory !== "function") throw new TypeError("Feature factory must be a function");
    featureFactories.push(factory);
    if (defaultApp) return defaultApp.loadQueuedFeatures();
    return factory;
  };
  publicApi.current = () => defaultApp;
  global.OmbreDashboardApp = publicApi;
})(window);
