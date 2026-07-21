(function initDashboardPath(global) {
  "use strict";

  const core = global.OmbreDashboardCore = global.OmbreDashboardCore || {};
  const ENTRY_SUFFIXES = ["/memory-dashboard", "/dashboard", "/letters"];
  const BOOT_MODES = new Set(["shared", "memory", "models-data", "system"]);
  const LOCAL_DASHBOARD_ORIGIN = "http://localhost:18001";

  function normalizePathname(pathname) {
    let value = typeof pathname === "string" ? pathname : "/";
    const queryAt = value.search(/[?#]/);
    if (queryAt !== -1) value = value.slice(0, queryAt);
    if (!value.startsWith("/")) value = `/${value}`;
    value = value.replace(/\/{2,}/g, "/");
    if (value.length > 1) value = value.replace(/\/+$/, "");
    return value || "/";
  }

  function entryForPath(pathname) {
    const normalized = normalizePathname(pathname);
    for (const suffix of ENTRY_SUFFIXES) {
      if (normalized === suffix || normalized.endsWith(suffix)) return suffix;
    }
    return "/";
  }

  function mountPrefixForPath(pathname) {
    const normalized = normalizePathname(pathname);
    const entry = entryForPath(normalized);
    if (entry === "/") return normalized === "/" ? "" : normalized;
    const prefix = normalized.slice(0, -entry.length);
    return prefix === "/" ? "" : prefix;
  }

  function bootModeFromPath(pathname, fallback) {
    const entry = entryForPath(pathname);
    if (entry === "/memory-dashboard" || entry === "/dashboard") return "memory";
    if (entry === "/letters") return "system";
    return BOOT_MODES.has(fallback) ? fallback : "shared";
  }

  function safeOrigin(value, fallback) {
    const candidate = value || fallback;
    const parsed = new URL(candidate);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      throw new TypeError("Dashboard origin must use HTTP or HTTPS");
    }
    return parsed.origin;
  }

  function normalizeMountPrefix(value) {
    if (value === undefined || value === null || value === "") return "";
    const normalized = normalizePathname(String(value));
    return normalized === "/" ? "" : normalized;
  }

  function createPathEnv(options) {
    const settings = options || {};
    const currentLocation = settings.location || global.location || {};
    const currentProtocol = String(currentLocation.protocol || "").toLowerCase();
    const isFilePreview = settings.origin === undefined
      && (currentProtocol === "file:" || currentLocation.origin === "null");
    const pathname = normalizePathname(
      isFilePreview ? "/" : settings.pathname || currentLocation.pathname || "/",
    );
    const origin = safeOrigin(
      isFilePreview ? LOCAL_DASHBOARD_ORIGIN : settings.origin || currentLocation.origin,
      "http://localhost",
    );
    const basePath = isFilePreview
      ? ""
      : settings.mountPrefix === undefined
        ? mountPrefixForPath(pathname)
        : normalizeMountPrefix(settings.mountPrefix);
    const entryRoute = entryForPath(pathname);
    const bootMode = bootModeFromPath(pathname, settings.defaultBootMode);
    const baseUrl = `${origin}${basePath}`;

    function assertContainedPath(path) {
      const rawPath = path.split(/[?#]/, 1)[0];
      if (rawPath.includes("\\")) {
        throw new TypeError("Backslashes are not allowed in Dashboard URLs");
      }
      for (const segment of rawPath.split("/")) {
        let decoded = segment;
        try {
          decoded = decodeURIComponent(segment);
        } catch (_error) {
          throw new TypeError("Dashboard URL contains invalid encoding");
        }
        if (decoded === "." || decoded === ".." || decoded.includes("/") || decoded.includes("\\")) {
          throw new TypeError("Dashboard URL cannot traverse the mount prefix");
        }
      }
    }

    function belongsToMount(path) {
      return !basePath || path === basePath || path.startsWith(`${basePath}/`);
    }

    function pathFor(value) {
      const raw = value === undefined || value === null ? "" : String(value);
      if (/^[a-z][a-z\d+.-]*:/i.test(raw)) {
        const absolute = new URL(raw);
        if (absolute.origin !== origin) {
          throw new TypeError("Cross-origin Dashboard URLs are not allowed");
        }
        assertContainedPath(absolute.pathname);
        if (!belongsToMount(absolute.pathname)) {
          throw new TypeError("Dashboard URL is outside the mount prefix");
        }
        return `${absolute.pathname}${absolute.search}${absolute.hash}`;
      }
      const normalized = raw.replace(/^\.\//, "").replace(/^\/+/, "");
      assertContainedPath(normalized);
      const suffix = normalized ? `/${normalized}` : "/";
      return `${basePath}${suffix}` || "/";
    }

    function urlFor(value) {
      return new URL(pathFor(value), origin).toString();
    }

    return Object.freeze({
      origin,
      pathname,
      basePath,
      baseUrl,
      entryRoute,
      bootMode,
      isFilePreview,
      path: pathFor,
      route: pathFor,
      api: urlFor,
      asset: urlFor,
      url: urlFor,
    });
  }

  core.normalizePathname = normalizePathname;
  core.bootModeFromPath = bootModeFromPath;
  core.createPathEnv = createPathEnv;
})(window);
