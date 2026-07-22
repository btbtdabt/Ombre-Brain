(function initDashboardRouter(global) {
  "use strict";

  const core = global.OmbreDashboardCore = global.OmbreDashboardCore || {};
  const DEFAULT_PANELS = Object.freeze({
    shared: "shared-buckets",
    memory: "memory-reminders",
    "models-data": "models-effective-config",
    system: "system-status",
  });
  const DEFAULT_TAB_ALIASES = Object.freeze({
    list: "shared-buckets",
    breath: "shared-breath",
    network: "shared-network",
    import: "shared-import",
    plan: "system-plans",
    letters: "system-letters",
    anchors: "system-anchors",
    logs: "system-logs",
    settings: "system-status",
    "v3-debug": "system-replay-debug",
    about: "system-about",
    faq: "system-about",
    todos: "memory-reminders",
    reflection: "memory-reflection",
    "chat-memory": "memory-chat-memory",
    dreams: "memory-dreams",
    persona: "memory-persona-state",
    profile: "memory-profile-facts",
    "word-map": "memory-word-map",
    config: "models-effective-config",
    "upstream-config": "models-upstream",
    "model-config": "models-dehydration",
    "memory-config": "models-surfacing",
  });
  const ROUTING_QUERY_KEYS = new Set(["workspace", "panel", "tab"]);
  const UNSAFE_OBJECT_KEYS = new Set(["__proto__", "constructor", "prototype"]);

  function inferredWorkspace(panelId) {
    if (panelId.startsWith("shared-")) return "shared";
    if (panelId.startsWith("memory-")) return "memory";
    if (panelId.startsWith("models-")) return "models-data";
    if (panelId.startsWith("system-")) return "system";
    return null;
  }

  function createRouter(options) {
    const settings = options || {};
    const pathEnv = settings.pathEnv || settings.env;
    if (!pathEnv || typeof pathEnv.route !== "function") {
      throw new TypeError("createRouter requires a path environment");
    }
    const locationLike = settings.location || global.location;
    const historyLike = settings.history || global.history;
    const eventTarget = settings.eventTarget || global;
    const manifest = settings.manifest || {};
    const panelWorkspaces = new Map();
    const tabAliases = Object.assign(Object.create(null), DEFAULT_TAB_ALIASES);
    const hashAliases = Object.assign(Object.create(null), { "#letters": "system-letters" });
    const routeAliases = Object.assign(Object.create(null), { "/letters": "system-letters" });
    const defaults = Object.assign({}, DEFAULT_PANELS, settings.defaults || {});

    for (const panel of manifest.panels || []) {
      if (panel && panel.id && panel.workspace) {
        panelWorkspaces.set(String(panel.id), String(panel.workspace));
      }
    }
    for (const alias of manifest.legacy_state_aliases || []) {
      if (!alias || !alias.state || !alias.panel) continue;
      if (alias.kind === "hash") hashAliases[alias.state] = alias.panel;
      if (alias.kind === "route") routeAliases[alias.state] = alias.panel;
      if (alias.kind === "tab") tabAliases[alias.state] = alias.panel;
    }
    for (const panel of [
      ...Object.values(defaults),
      ...Object.values(tabAliases),
      ...Object.values(hashAliases),
      ...Object.values(routeAliases),
    ]) {
      const workspace = inferredWorkspace(panel);
      if (workspace && !panelWorkspaces.has(panel)) panelWorkspaces.set(panel, workspace);
    }

    const listeners = new Set();
    let state = null;
    let started = false;

    function fallbackState() {
      const mode = settings.bootMode || pathEnv.bootMode || "shared";
      const workspace = Object.prototype.hasOwnProperty.call(defaults, mode)
        ? mode
        : "shared";
      return { workspace, panel: defaults[workspace], params: {} };
    }

    function validState(panel, requestedWorkspace) {
      if (!panel || typeof panel !== "string") return null;
      const workspace = panelWorkspaces.get(panel);
      if (!workspace) return null;
      if (requestedWorkspace && requestedWorkspace !== workspace) return null;
      return { workspace, panel, params: {} };
    }

    function paramsObject(searchParams) {
      const params = Object.create(null);
      for (const [key, value] of searchParams.entries()) {
        if (!ROUTING_QUERY_KEYS.has(key) && !UNSAFE_OBJECT_KEYS.has(key)) params[key] = value;
      }
      return params;
    }

    function canonicalizeLegacyState(input) {
      const source = input || locationLike || {};
      const pathname = core.normalizePathname
        ? core.normalizePathname(source.pathname || "/")
        : String(source.pathname || "/");
      const search = new URLSearchParams(source.search || "");
      const hash = String(source.hash || "");
      const requested = validState(search.get("panel"), search.get("workspace"));
      if (requested) {
        requested.params = paramsObject(search);
        return requested;
      }

      const tabName = search.get("tab");
      const tabPanel = tabAliases[tabName];
      const fromTab = validState(tabPanel);
      if (fromTab) {
        fromTab.params = paramsObject(search);
        if (tabName === "faq" && !fromTab.params.section) {
          fromTab.params.section = "faq-section";
        }
        return fromTab;
      }

      const directHashPanel = hashAliases[hash];
      const fromHash = validState(directHashPanel);
      if (fromHash) return fromHash;

      if (hash.startsWith("#")) {
        const hashParams = new URLSearchParams(hash.slice(1));
        const hashTabName = hashParams.get("tab");
        const fromHashParams = validState(
          hashParams.get("panel") || tabAliases[hashTabName],
          hashParams.get("workspace"),
        );
        if (fromHashParams) {
          fromHashParams.params = paramsObject(hashParams);
          if (hashTabName === "faq" && !fromHashParams.params.section) {
            fromHashParams.params.section = "faq-section";
          }
          return fromHashParams;
        }
      }

      for (const [routeSuffix, panel] of Object.entries(routeAliases)) {
        if (pathname === routeSuffix || pathname.endsWith(routeSuffix)) {
          const fromRoute = validState(panel);
          if (fromRoute) return fromRoute;
        }
      }
      return fallbackState();
    }

    function notify(next, previous, cause) {
      state = Object.freeze({
        workspace: next.workspace,
        panel: next.panel,
        params: Object.freeze(Object.assign(Object.create(null), next.params || {})),
      });
      for (const listener of Array.from(listeners)) {
        listener(state, previous, cause);
      }
      return state;
    }

    function urlFor(next) {
      const params = new URLSearchParams(next.params || {});
      params.set("workspace", next.workspace);
      params.set("panel", next.panel);
      const entry = pathEnv.entryRoute === "/" ? "" : pathEnv.entryRoute;
      return `${pathEnv.route(entry)}?${params.toString()}`;
    }

    function navigate(workspace, panel, params, replace) {
      const next = validState(panel, workspace);
      if (!next) throw new TypeError("Unknown Dashboard workspace or panel");
      next.params = Object.assign(Object.create(null), params || {});
      const previous = state;
      if (historyLike) {
        const method = replace ? "replaceState" : "pushState";
        if (typeof historyLike[method] === "function") {
          historyLike[method]({}, "", urlFor(next));
        }
      }
      return notify(next, previous, replace ? "replace" : "navigate");
    }

    function syncFromLocation(cause) {
      const previous = state;
      return notify(canonicalizeLegacyState(locationLike), previous, cause);
    }

    const onPopState = () => syncFromLocation("popstate");
    const onHashChange = () => syncFromLocation("hashchange");

    return Object.freeze({
      start() {
        if (!started) {
          started = true;
          if (eventTarget && typeof eventTarget.addEventListener === "function") {
            eventTarget.addEventListener("popstate", onPopState);
            eventTarget.addEventListener("hashchange", onHashChange);
          }
        }
        return syncFromLocation("start");
      },
      stop() {
        if (!started) return;
        started = false;
        if (eventTarget && typeof eventTarget.removeEventListener === "function") {
          eventTarget.removeEventListener("popstate", onPopState);
          eventTarget.removeEventListener("hashchange", onHashChange);
        }
      },
      go: (workspace, panel, params) => navigate(workspace, panel, params, false),
      replace: (workspace, panel, params) => navigate(workspace, panel, params, true),
      current: () => state || canonicalizeLegacyState(locationLike),
      onChange(listener) {
        if (typeof listener !== "function") throw new TypeError("Router listener must be a function");
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      registerPanel(panel, workspace) {
        if (!panel || !workspace || inferredWorkspace(panel) !== workspace) {
          throw new TypeError("Panel id and workspace do not match");
        }
        panelWorkspaces.set(panel, workspace);
      },
      canonicalizeLegacyState,
    });
  }

  core.createRouter = createRouter;
})(window);
