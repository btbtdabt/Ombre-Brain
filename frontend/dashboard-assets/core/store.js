(function initDashboardStore(global) {
  "use strict";

  const core = global.OmbreDashboardCore = global.OmbreDashboardCore || {};

  function abortError(message) {
    if (typeof global.DOMException === "function") {
      return new global.DOMException(message, "AbortError");
    }
    const error = new Error(message);
    error.name = "AbortError";
    return error;
  }

  function createDashboardStore(options) {
    const settings = options || {};
    const values = new Map();
    const inFlight = new Map();
    const listeners = new Map();
    const revisions = new Map();

    function revisionFor(key) {
      return revisions.get(key) || 0;
    }

    function bumpRevision(key) {
      revisions.set(key, revisionFor(key) + 1);
    }

    function emit(key, value, reason) {
      const subscriptions = listeners.get(key);
      if (!subscriptions) return;
      for (const listener of Array.from(subscriptions)) listener(value, reason, key);
    }

    function set(key, value, metadata) {
      const now = Date.now();
      const ttlMs = metadata && Number.isFinite(metadata.ttlMs)
        ? Math.max(0, Number(metadata.ttlMs))
        : 0;
      values.set(key, {
        value,
        storedAt: now,
        expiresAt: ttlMs > 0 ? now + ttlMs : null,
      });
      emit(key, value, metadata && metadata.reason ? metadata.reason : "set");
      return value;
    }

    function peek(key, optionsForPeek) {
      const entry = values.get(key);
      if (!entry) return undefined;
      if (
        entry.expiresAt !== null
        && entry.expiresAt <= Date.now()
        && !(optionsForPeek && optionsForPeek.allowStale)
      ) {
        return undefined;
      }
      return entry.value;
    }

    function abortRecord(record, reason) {
      if (!record || record.controller.signal.aborted) return;
      record.controller.abort(reason || abortError("Invalidated"));
    }

    function invalidate(keys) {
      const requested = Array.isArray(keys) ? keys : [keys];
      for (const key of requested) {
        if (typeof key !== "string" || !key) continue;
        values.delete(key);
        bumpRevision(key);
        abortRecord(inFlight.get(key));
        emit(key, undefined, "invalidate");
      }
    }

    function clear() {
      const keys = new Set([...values.keys(), ...inFlight.keys()]);
      invalidate(Array.from(keys));
    }

    async function resource(key, loader, resourceOptions) {
      if (typeof key !== "string" || !key) throw new TypeError("Resource key is required");
      if (typeof loader !== "function") throw new TypeError("Resource loader must be a function");
      const opts = resourceOptions || {};
      const cached = opts.refresh ? undefined : peek(key);
      if (cached !== undefined) return cached;
      let existing = inFlight.get(key);
      if (existing && existing.controller.signal.aborted) {
        inFlight.delete(key);
        existing = null;
      }
      if (opts.refresh && existing) {
        abortRecord(existing);
        bumpRevision(key);
      } else if (opts.dedupe !== false && existing) {
        return existing.promise;
      } else if (opts.dedupe === false && existing) {
        bumpRevision(key);
      }
      const controller = new AbortController();
      const startedRevision = revisionFor(key);
      const record = {
        controller,
        scopeId: opts.scopeId || null,
        promise: null,
      };
      const promise = Promise.resolve().then(() => loader({
        api: settings.api,
        key,
        signal: controller.signal,
        store,
      })).then((value) => {
        if (!controller.signal.aborted && revisionFor(key) === startedRevision) {
          set(key, value, { ttlMs: opts.ttlMs, reason: "load" });
        }
        return value;
      }).finally(() => {
        if (inFlight.get(key) === record) inFlight.delete(key);
      });
      record.promise = promise;
      inFlight.set(key, record);
      return promise;
    }

    function abortScope(scopeId) {
      if (!scopeId) return;
      for (const [key, record] of inFlight.entries()) {
        if (record.scopeId !== scopeId) continue;
        bumpRevision(key);
        abortRecord(record, abortError("Scope changed"));
      }
    }

    async function mutate(label, operation, mutationOptions) {
      if (typeof operation !== "function") throw new TypeError("Mutation operation must be a function");
      const result = await operation({ api: settings.api, label, store });
      const ownedKeys = mutationOptions && mutationOptions.invalidate;
      if (ownedKeys) invalidate(ownedKeys);
      return result;
    }

    function subscribe(key, listener) {
      if (typeof listener !== "function") throw new TypeError("Store listener must be a function");
      if (!listeners.has(key)) listeners.set(key, new Set());
      listeners.get(key).add(listener);
      return () => {
        const subscriptions = listeners.get(key);
        if (!subscriptions) return;
        subscriptions.delete(listener);
        if (subscriptions.size === 0) listeners.delete(key);
      };
    }

    const store = Object.freeze({
      resource,
      invalidate,
      abortScope,
      mutate,
      peek,
      set,
      clear,
      subscribe,
    });
    return store;
  }

  core.createDashboardStore = createDashboardStore;
})(window);
