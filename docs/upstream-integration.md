# Upstream Integration Policy

This repository uses the P0luz implementation as its executable base and keeps
Yinglianchun's repository as a secondary source of product behavior. Feature
preservation is opt-out, not opt-in: every user-visible or production-runtime
capability is ported unless this document records a concrete exclusion and its
verification evidence.

## Recorded Baselines

- P0luz primary baseline: `v2.7.6` / `6da5158`
- Yinglianchun secondary baseline: `4e0a546`
- Historical deployed root runtime: `8e68a7d`

The migration branch starts directly from the P0luz baseline. It does not merge
the historical root runtime because that would install two competing server
implementations. Current and Yinglianchun behavior is instead ported into the
P0luz `src/`, `src/tools/`, and `src/web/` module structure.

## Runtime Ownership

- `src/server.py` and `src/server_app.py`: Brain process assembly
- `src/tools/`: MCP tool interfaces and implementations
- `src/web/`: Dashboard and HTTP APIs
- `src/gateway.py`: Claude/Gemini Gateway process
- `src/*.py`: reusable memory, state, retrieval, persona, and worker modules
- `src/ombrebrain/` and `kernel/`: non-production v3/distributed work unless an
  explicit migration activates it

## Feature Parity Checklist

The P0luz base remains intact while the following historical capabilities are
ported and tested:

- [x] Native Anthropic and OpenAI-compatible Gateway routes
- [x] Token-selected Claude and Gemini upstream routing
- [x] Gateway memory injection, state tracking, and debug traces
- [x] Current recall graph, relevance policy, moments, layers, and diffusion
- [x] Raw events and source-reference continuity
- [x] Persona, portrait, reflection, dream, and reminder workers
- [x] Current MCP tool set and affirmative tool descriptions
- [x] Darkroom, letters, media, backup, and import behavior
- [x] Current Dashboard memory/config/debug surfaces
- [x] Production alignment, Cloudflare, relay, and VPS deployment contract
- [x] Read-only compatibility with copied production buckets and state

Each item is complete only when its compatibility tests pass and the staging
runtime demonstrates the same externally observable behavior.

## Future Updates

1. Fetch both upstreams.
2. Merge P0luz changes into a local update branch and run its full suite.
3. Review Yinglianchun commits after the recorded baseline.
4. Port applicable Yinglianchun behavior at the matching P0luz module seam.
5. Record every commit in both ranges as merged, ported, superseded, or excluded
   with evidence. A clean merge alone does not establish feature parity.
6. Run compatibility tests, copied-data validation, lint, types, and container
   build checks.
7. Advance the baseline hashes in this document only after staging succeeds.

Do not resolve upstream conflicts by restoring the repository-root runtime or by
silently dropping one implementation's feature. Record intentional exclusions
with their reason and regression evidence.

The historical fixed `OMBRE_CHATGPT_OAUTH_REFRESH_TOKEN` is superseded by the
P0 OAuth server's persisted, expiring, one-time rotating refresh tokens. The
configured client ID/secret still pre-registers the client, and the legacy
fixed access token remains a resource-bound compatibility credential.

## Integration Batch Ledger

### 2026-07-16 P0luz-base migration

| Source range | Disposition | Evidence |
| --- | --- | --- |
| P0luz `6da5158..p0luz/main` | No newer commits at integration time | `p0luz/main` still resolves to `6da5158` (`v2.7.6`). |
| Yinglianchun `4756c26..4e0a546` | Ported | The bounded Operit tagging queue, raw-first durable import, resume/retry state, preflight controls, and Dashboard progress UI are implemented in the modular import/web seams. Covered by `tests/test_operit_tagging_queue.py`, import preflight/start tests, and compatibility import tests. |
| Historical deployed fork `8e68a7d` | Ported by product boundary | Gateway routing/debug/persona behavior, current MCP tools, recall graph and memory stores, workers, Dashboard routes, OAuth, media/letters/darkroom, backup/import, deployment assets, and production alignment contracts are represented in `src/` and their `tests/compat_*` suites. |

Local verification for this batch:

- `pytest`: 2,156 passed, 79 skipped, 3 known warnings.
- Ruff: clean across the repository.
- Pyright: zero errors across all 57 changed Python files. The whole-tree debt
  audit remains non-zero in dormant v3/legacy annotations and is not hidden.
- `docker compose -f compose.cloudflare.yml config --quiet`: passed.
- VPS staging image `ombre-brain:p0luz-staging-07691f1` built from exact commit
  `07691f1`; Brain and Gateway health checks passed on loopback-only ports.
- Eleven copied SQLite databases passed `PRAGMA quick_check` before startup.
- OpenAI Claude, native Anthropic, and native Gemini staging routes returned 200.
- Authenticated Streamable HTTP MCP initialized a session, exposed 30 tools,
  contained the current tool set, and successfully called `pulse`.
- Live SiliconFlow reranking returned two ranked results using the dedicated
  reranker environment; no 401 or reranker failure remained in staging logs.
- The saved Dashboard runtime overlay matched the effective Gateway config, and
  Persona post-reply processing continued in the copied 118-event state database.
- The relay-facing native Claude and MCP contracts were exercised directly on
  staging. The public Cloudflare relay remains production-targeted and must be
  checked end to end immediately after production cutover.

## Intentional Architecture Exclusions

- P0luz's dormant Rust kernel, Raft, distributed fabric, and event-sourced v3
  scaffolding remain outside the single-node production boot path. Their source
  and tests stay in the repository, but activation requires a separate migration
  because it changes storage, operations, and failure semantics rather than adding
  an isolated product feature.
- Yinglianchun's active `wikilink:` example stanza is not retained. The shared
  storage implementation deliberately stopped mutating saved content with
  automatically generated links; it still reads author-supplied `[[wikilink]]`
  values for graph features. The deprecated settings are documented in
  `config.example.yaml` and ignored at runtime.
