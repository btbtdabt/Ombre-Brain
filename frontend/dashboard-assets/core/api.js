(function initDashboardApi(global) {
  "use strict";

  const core = global.OmbreDashboardCore = global.OmbreDashboardCore || {};
  const RETRYABLE_STATUSES = new Set([408, 425, 429, 500, 502, 503, 504]);
  const IDEMPOTENT_METHODS = new Set(["GET", "HEAD"]);

  class DashboardApiError extends Error {
    constructor(message, options) {
      super(message);
      this.name = "DashboardApiError";
      this.status = options && options.status ? options.status : 0;
      this.payload = options ? options.payload : undefined;
      this.response = options ? options.response : undefined;
    }
  }

  function timeoutError(timeoutMs) {
    const error = new Error(`Dashboard request timed out after ${timeoutMs} ms`);
    error.name = "TimeoutError";
    return error;
  }

  function abortError(message) {
    if (typeof global.DOMException === "function") {
      return new global.DOMException(message, "AbortError");
    }
    const error = new Error(message);
    error.name = "AbortError";
    return error;
  }

  function delay(milliseconds, signal) {
    if (!milliseconds) return Promise.resolve();
    return new Promise((resolve, reject) => {
      let timer = null;
      const finish = () => {
        if (signal) signal.removeEventListener("abort", onAbort);
        resolve();
      };
      const onAbort = () => {
        if (timer !== null) global.clearTimeout(timer);
        reject(signal.reason || abortError("Aborted"));
      };
      timer = global.setTimeout(finish, milliseconds);
      if (!signal) return;
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
    });
  }

  function requestSignal(externalSignal, timeoutMs) {
    const controller = new AbortController();
    let timeoutId = null;
    let timedOut = false;

    const forwardAbort = () => controller.abort(externalSignal.reason);
    if (externalSignal) {
      if (externalSignal.aborted) forwardAbort();
      else externalSignal.addEventListener("abort", forwardAbort, { once: true });
    }
    if (Number.isFinite(timeoutMs) && timeoutMs > 0) {
      timeoutId = global.setTimeout(() => {
        timedOut = true;
        controller.abort(timeoutError(timeoutMs));
      }, timeoutMs);
    }

    return {
      signal: controller.signal,
      timedOut: () => timedOut,
      cleanup() {
        if (timeoutId !== null) global.clearTimeout(timeoutId);
        if (externalSignal) externalSignal.removeEventListener("abort", forwardAbort);
      },
    };
  }

  function isJsonBody(body) {
    if (body === null || body === undefined) return false;
    if (typeof body === "string") return false;
    if (global.FormData && body instanceof global.FormData) return false;
    if (global.Blob && body instanceof global.Blob) return false;
    if (global.URLSearchParams && body instanceof global.URLSearchParams) return false;
    if (global.ArrayBuffer && body instanceof global.ArrayBuffer) return false;
    return typeof body === "object" || typeof body === "number" || typeof body === "boolean";
  }

  function createApiClient(options) {
    const settings = options || {};
    const pathEnv = settings.pathEnv || settings.env;
    if (!pathEnv || typeof pathEnv.api !== "function") {
      throw new TypeError("createApiClient requires a path environment");
    }
    const fetchImpl = settings.fetchImpl || global.fetch.bind(global);
    const defaultTimeoutMs = settings.defaultTimeoutMs === undefined
      ? 15000
      : Number(settings.defaultTimeoutMs);
    const defaultRetries = settings.defaultRetries === undefined
      ? 2
      : Math.max(0, Number(settings.defaultRetries) || 0);
    const retryDelayMs = settings.retryDelayMs === undefined
      ? 120
      : Math.max(0, Number(settings.retryDelayMs) || 0);

    async function request(path, requestOptions) {
      const init = Object.assign({}, requestOptions || {});
      const unauthorizedHandler = Object.prototype.hasOwnProperty.call(init, "onUnauthorized")
        ? init.onUnauthorized
        : settings.onUnauthorized;
      const method = String(init.method || "GET").toUpperCase();
      const canRetry = IDEMPOTENT_METHODS.has(method);
      const retries = canRetry
        ? Math.max(0, init.retries === undefined ? defaultRetries : Number(init.retries) || 0)
        : 0;
      const timeoutMs = init.timeoutMs === undefined
        ? defaultTimeoutMs
        : Number(init.timeoutMs);
      const externalSignal = init.signal;
      delete init.onUnauthorized;
      delete init.retries;
      delete init.timeoutMs;
      init.method = method;
      init.credentials = init.credentials || "same-origin";
      init.headers = new Headers(init.headers || {});
      if (!init.headers.has("Accept")) init.headers.set("Accept", "application/json");
      if (isJsonBody(init.body)) {
        if (!init.headers.has("Content-Type")) {
          init.headers.set("Content-Type", "application/json");
        }
        init.body = JSON.stringify(init.body);
      }

      const url = pathEnv.api(path);
      for (let attempt = 0; attempt <= retries; attempt += 1) {
        const composed = requestSignal(externalSignal, timeoutMs);
        init.signal = composed.signal;
        try {
          const response = await fetchImpl(url, init);
          if (response.status === 401 && typeof unauthorizedHandler === "function") {
            await unauthorizedHandler(response);
          }
          if (
            canRetry
            && attempt < retries
            && RETRYABLE_STATUSES.has(response.status)
          ) {
            if (response.body && typeof response.body.cancel === "function") {
              await response.body.cancel();
            }
            composed.cleanup();
            await delay(retryDelayMs * (2 ** attempt), externalSignal);
            continue;
          }
          return response;
        } catch (error) {
          if (composed.timedOut()) throw timeoutError(timeoutMs);
          if (externalSignal && externalSignal.aborted) throw error;
          if (error && error.name === "AbortError") throw error;
          if (!canRetry || attempt >= retries) throw error;
          await delay(retryDelayMs * (2 ** attempt), externalSignal);
        } finally {
          composed.cleanup();
        }
      }
      throw new Error("Dashboard request failed without a response");
    }

    async function readJson(response) {
      const text = await response.text();
      let payload = null;
      if (text) {
        try {
          payload = JSON.parse(text);
        } catch (error) {
          throw new DashboardApiError("Dashboard returned invalid JSON", {
            status: response.status,
            response,
          });
        }
      }
      if (!response.ok) {
        const message = payload && (payload.error || payload.detail || payload.message);
        throw new DashboardApiError(message || `Dashboard request failed (${response.status})`, {
          status: response.status,
          payload,
          response,
        });
      }
      return payload;
    }

    function withMethod(method, path, body, optionsForRequest) {
      const requestInit = Object.assign({}, optionsForRequest || {}, { method });
      if (body !== undefined) requestInit.body = body;
      return request(path, requestInit);
    }

    return Object.freeze({
      request,
      readJson,
      get: (path, opts) => withMethod("GET", path, undefined, opts),
      head: (path, opts) => withMethod("HEAD", path, undefined, opts),
      post: (path, body, opts) => withMethod("POST", path, body, opts),
      patch: (path, body, opts) => withMethod("PATCH", path, body, opts),
      put: (path, body, opts) => withMethod("PUT", path, body, opts),
      delete: (path, body, opts) => withMethod("DELETE", path, body, opts),
      upload: (path, formData, opts) => withMethod("POST", path, formData, opts),
    });
  }

  core.DashboardApiError = DashboardApiError;
  core.createApiClient = createApiClient;
})(window);
