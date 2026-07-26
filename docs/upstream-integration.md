# Upstream Integration Policy

This repository uses the P0luz implementation as its executable base and keeps
Yinglianchun's repository as a secondary source of product behavior. Feature
preservation is opt-out, not opt-in: every user-visible or production-runtime
capability is ported unless this document records a concrete exclusion and its
verification evidence.

## Recorded Baselines

- P0luz primary baseline: `v2.8.10` / `0582a3b`
- Yinglianchun secondary baseline: `0b4a877`
- Historical deployed root runtime: `8e68a7d`
- P0luz-base production cutover: `c806f78`

The migration branch started directly from the P0luz baseline. It does not merge
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

### Canonical Shared Logic

- `src/tools/current/manifest.py`: the only production MCP registration inventory
- `src/recall_pipeline.py`: candidate thresholds, diagnostics, admission, recallability,
  and shared rendering metadata used by MCP, Dashboard, and Gateway adapters
- `src/profile_facts.py` plus `src/web/profile_support.py`: profile classification,
  evidence, and HTTP payload construction
- `src/config_modes.py`: Gateway/tool retrieval, direct-render, and provider thinking modes
- `src/runtime_values.py`: active-runtime boundary coercion, clamping, metadata views,
  identifiers, and comparable timestamp parsing
- `src/query_normalization.py`: compact lookup, symbol, and phrase keys used across
  Gateway and memory retrieval
- `src/sqlite_support.py`: row-producing SQLite connections for the active stores
- `src/operation_runtime.py`: optional v3 operation dispatch shared by MCP and HTTP
  boundary adapters
- `src/edge_records.py`: confidence-aware edge upsert and JSONL loading behavior
- `src/semantic_search.py`: strict/fallback embedding search invocation
- `src/file_tail.py`: bounded reverse log reads for errors and diagnostics
- `src/tools/hold/metadata.py` and `src/tools/_common.py`: hold metadata normalization
  and trace-compatible write policies

The remaining exact function-body repetitions are intentional boundaries rather
than duplicated production policy: stores keep one-line connection adapters over
the shared SQLite constructor; MCP and HTTP keep thin transport wrappers over the
shared operation dispatcher; dormant v3 protocol modules keep their public
interfaces; and separate dataclasses normalize their own fields. Gateway and MCP
direct-memory rendering share recall/admission primitives but retain
protocol-specific final composition.

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

### 2026-07-25 P0luz 2.8.10 merge and Yinglianchun chat-memory policy port

This batch reviews `65bf8c3..0582a3b` from P0luz and
`9393232..0b4a877` from Yinglianchun. The recorded baselines advanced after
the exact integration commit passed isolated staging.

| Source commit(s) | Disposition | Evidence |
| --- | --- | --- |
| P0luz `7efe2dc` | Merged with the unified Dashboard session boundary | Successful setup, recovery, and login now enter the authenticated Dashboard through one reload boundary. This retains the current path-aware shell and fixes the same first-login stale-page failure without introducing a second in-place initializer. Auth-gated polling, deep links, sensitive-state cleanup, and network-panel visibility remain covered by the unified Dashboard regression tests. |
| P0luz `e591aed` | Merged with current storage compatibility | Filesystem leases distinguish contention from broken locking, derived indexing runs after committed writes, imports preserve provenance while deferring index work, and the component outbox retains current `state_dir` migration compatibility. Provider-facing embedding caching no longer retains full bucket text. |
| P0luz `0e83d46` | Merged except for the release-specific archive shortcut | Hot-update dependency comparison, legacy upgrade handling, entrypoint bootstrap, and integrity regressions are retained. The `/requirements.txt export-ignore` shortcut is intentionally superseded because this fork's release lock differs from P0luz's fixed 2.8.4 lock; source constraints and the hashed lock both remain in release archives so dependency changes fail closed. |
| P0luz `4fb7714` | Merged | Meaning content is restored in the canonical memory detail view with escaped rendering. CI lock regeneration remains pinned to a fixed package-index cutoff. |
| P0luz `0582a3b` | Merged into current module ownership | Storage/media race hardening, sanitized tool errors, atomic migration/config behavior, bounded API work, OAuth refresh handling, diagnostics offloading, strict MCP schemas, and the added regression coverage are retained. The canonical 30-tool manifest applies strict unknown-argument rejection to the complete public surface rather than duplicating tool definitions in `src/server.py`. |
| Yinglianchun `0b4a877` | Policy port with richer engine retained | Automatic daily chat-memory extraction now defaults to `off` across the example config, runtime schedulers, API defaults, and Dashboard. Explicitly configured modes still use the modular candidate extraction, validation, and fallback pipeline; the legacy root runtime and wholesale prompt/heuristic removal are superseded by the active P0luz-base implementation. A one-time Persona schema migration materializes the retained Ying `libido` default in historical rows so copied and upgraded databases pass SQLite integrity checks without changing existing values. |

Local verification completed so far:

- Full suite: **2735 passed / 95 skipped / 0 failed**.
- Repository-wide Ruff: **clean**.
- Conflict-marker scan: **clean**.
- All changed Python files: **0 Pyright errors / 0 warnings**. The whole-tree
  debt audit is **371 errors / 21 warnings**, improved from the recorded
  **388/21** baseline without weakening its configuration.
- Dashboard JavaScript syntax and the local
  `ombre-brain:upstream-sync-20260725` Docker build: **clean**.
- Independent final code review: **approved with no remaining findings**.
- Isolated VPS staging: **passed** for exact runtime commit `053d864` and image
  `ombre-brain:staging-053d864`.
  - Brain and Gateway health checks returned 200 on loopback-only ports.
  - All 12 copied SQLite databases returned `PRAGMA quick_check = ok`.
  - The one-time `materialize_session_libido_v1` Persona migration repaired the
    historical physical-NULL integrity warning while preserving all 20 stored
    values at `0.18`.
  - The project alignment checker passed the Claude 4.8 and native Gemini 3.5
    routes with the expected token-selected model inventory.
  - Authenticated Streamable HTTP MCP negotiated protocol `2025-03-26`, exposed
    30 unique tools, and successfully called the read-only `pulse` tool.
  - Persona post-reply processing advanced the copied `main` session state on
    the successful Claude final response.
  - Fresh Brain and Gateway logs contained no error or warning entries.
- Production alignment: **pending**.

### 2026-07-22 P0luz 2.8.5 merge and Yinglianchun behavior port

| Source commit(s) | Disposition | Evidence |
| --- | --- | --- |
| P0luz `87c4a4e` | Merged | Release 2.7.7 trace long-bucket replacement behavior is present through merge commit `73024dd`; the complete P0 suite passed before the secondary ports began. |
| P0luz `282569d`, `b0343e1`, `1ff5c75`, `36c0bc6`, `95529d1`, `c81df31`, `c1f30c4`, `8427eb4`, `c0335fc`, `ab985fa`, `50342c2`, `c3de6c3`, `ccbc905`, `e671c3e`, `c352f42`, `9710be7` | Merged | The 2.7.8 package extraction series is retained in P0's modular `src/ombrebrain` support packages without moving the dormant distributed stack into the production boot path. |
| P0luz `1cf0bc2`, `5b0bd10`, `648fe1a`, `796db07` | Merged | Releases 2.7.8 through 2.7.10, digest-viewpoint behavior, proxy documentation, and FAQ assets are present in `73024dd`; existing unified-Dashboard ownership remains intact. |
| P0luz `1ebb233`, `1c6ecfe`, `d3651a8`, `321aaea`, `39d81b6`, `9c6c32a`, `e6c36e7`, `bdee836`, `2784f41` | Merged | Releases 2.8.0 through 2.8.4, retrieval/deployment corrections, memory footprint/restore, CI and Docker Web assertions, and passive resurfacing of digested memories are included by ancestry. |
| P0luz `c741a6f`, `0e1d22e`, `898ef61`, `65bf8c3` | Merged | Collaboration/update rules, the current `CLAUDE_PROMPT.md`, and the 2.8.5 Kelivo MCP discovery compatibility release are included by ancestry. |
| Yinglianchun `3054384` | Ported with documented conflict resolution | Retained libido state, recent net-delta Persona guidance, and the optional pre-reply conflict scout in the modular Persona/Gateway seams. The proposal to retire personality state and skip Persona evaluation for two out of every three final replies conflicts with the retained production contract, so personality remains available and every successful final reply remains eligible for post-reply evaluation. |
| Yinglianchun `4a941e9` | Ported | A triggered conflict/withdrawal reminder takes precedence over the ordinary recent-state note for the same request. Regression coverage checks the private dynamic-context injection and fail-soft detector behavior. |
| Yinglianchun `8bb06dd` | Ported and merged with P0 configuration ownership | Conflict guidance has its own Persona setting, live apply/public-config path, and Dashboard control/status. Recent-state guidance remains controlled independently by the Gateway interval, with the 15-round comparison default from this commit. Persona evaluation itself is not disabled by either control. |
| Yinglianchun `9393232` | Ported and merged with P0 import safety | Conversation imports recognize configured identity labels, preserve raw exported timestamps, derive a local event date, and form first-person identity-aware memories. P0's untrusted-upload boundary, parser worker, resume hash, bounded extraction, quota enforcement, duplicate provenance, and configurable safe auto-merge remain authoritative. |
| Yinglianchun `Haven/memory-server-p0-20260722` through `95a7377` | Deferred; not a mainline update | This non-main work-in-progress branch introduces the Scene/event-sourced architecture. It remains outside the single-node production boot path under the standing v3/distributed exclusion and requires a separate storage/runtime migration before activation. |

Local verification completed so far for this batch:

- P0 merge checkpoint: **2607 passed / 80 skipped**, Ruff clean, changed-file
  Pyright clean, and Docker build successful.
- Secondary-port and reviewer-regression suites: **127 passed**, then **33
  passed**, with no failures.
- Final full suite: **2622 passed / 80 skipped / 0 failed**.
- Repository-wide Ruff and changed Dashboard JavaScript syntax: **clean**.
- All changed Python files: **0 Pyright errors / 0 warnings**. The whole-tree
  debt audit is **388 errors / 21 warnings**, improved from the recorded
  **469/21** baseline without weakening its configuration.
- Production Docker image `ombre-brain:upstream-sync-20260722`: **built
  successfully**.
- Exact commit `3c2df50` was built on the VPS as
  `ombre-brain:staging-3c2df50` and started with image-only code on isolated
  loopback ports and copied state. All five copied SQLite databases passed
  `PRAGMA quick_check`; Brain and Gateway health checks returned 200 with no
  startup errors.
- Authenticated staging checks returned 200 for the Claude 4.8 and native
  Gemini 3.5 routes. Streamable HTTP MCP negotiated protocol `2025-06-18`,
  exposed all **30 tools**, and successfully called the read-only `pulse` tool.
- The copied Persona database migrated the `affect_delta` exchange-log column,
  and a unique normal final reply produced a new Persona exchange for its
  staging session. This verifies that the retained every-final-reply Persona
  contract still runs after the upstream integration.
- Both upstream baselines were advanced only after this staging evidence and
  the completed local/reviewer gates above. Production and public-alignment
  verification remain pending for the exact final commit.

### 2026-07-21 Dashboard consolidation and P0 visual contract

| Source range | Disposition | Evidence |
| --- | --- | --- |
| P0luz and current/Ying Dashboard inventories | One visible owner per function, with distinct behavior preserved | The parity manifest assigns every retained panel, route family, and action to one canonical destination. The former Bucket Studio tab is now the **Advanced** mode inside the canonical **Buckets** workspace, while its bulk editing, raw ingest/search, comments, moments, taxonomy, and edge tooling remain intact. The legacy `shared-bucket-studio` deep link opens that advanced mode. |
| P0luz FAQ and About content | Consolidated without a duplicate top-level tab | P0luz's original usage guide is exposed inside **System / About**. Legacy `?tab=faq` state resolves to the same canonical About panel. |
| Persona surfaces | Separate responsibilities with unambiguous labels | **Persona State** remains the read-only memory/identity view, while **Persona Settings** remains the model/configuration editor. Neither panel loses its original HTTP surface or controls. |
| Unified visual surface | P0luz style is the canonical contract | Every registered panel receives the shared warm cream/gold, retro-handheld P0 surface contract: mono labels, inset fields and data screens, raised key controls, consistent cards, focus states, status states, spacing, and responsive behavior. Feature-specific layouts remain intact beneath that common contract. The contract stylesheet is also restored to final precedence after every feature batch, including assets queued after the initial shell boot. |

The visible navigation therefore contains no duplicate Buckets, FAQ, Persona,
backup, or migration owner. Compatibility aliases preserve bookmarks without
reintroducing duplicate tabs.

Local verification for this batch:

- `pytest`: **2548 passed / 75 skipped / 0 failed**.
- Ruff and all changed-file Node syntax checks: **clean**.
- Changed Python files: **0 Pyright errors / 0 warnings**.
- Whole-tree Pyright debt audit: **469 errors / 21 warnings**, unchanged from
  the recorded baseline and confined to pre-existing dormant/compatibility
  annotations.
- Final Docker image build: **successful**.
- Isolated browser staging: all **37 visible panels** opened successfully,
  carried the P0 surface contract, used the same mono typography, had no
  horizontal overflow, and produced no browser errors. The Basic/Advanced
  Buckets modes, legacy Bucket Studio and FAQ deep links, and a 390 px mobile
  viewport all passed.
- Independent code and JavaScript reviews: **approved with no remaining
  findings**.

Production cutover remains pending until this exact commit is pushed, staged,
and verified on the public deployment.

### 2026-07-18 unified Dashboard integration

| Source range | Disposition | Evidence |
| --- | --- | --- |
| Local fork `main` unified-Dashboard batch | P0luz remains the canonical shell and runtime | `frontend/dashboard.html` is the single Dashboard shell over the modular P0luz `src/` runtime. The historical repository-root runtime remains excluded. The former standalone current/Ying `frontend/memory-dashboard.html` application is superseded by feature modules mounted in the canonical shell: retained memory-care and inspection behavior lives in the **Memory** workspace, while model, provider, tuning, backup, and migration behavior lives in **Models & Data**. Shared and System functions continue to use their P0-owned implementations. |
| Current/Ying Dashboard feature and HTTP surface | Ported without collapsing distinct behavior | The parity manifest maps every retained current route family (`discovery`, `memory`, `profile`, and `operations`) to a unified-shell destination while the existing modular API handlers retain ownership and request/response semantics. Memory reminders, reflection/daily-impression history, chat memory, dreams, darkroom, Persona state, portrait, profile facts and proposals, anchor proposals, Word Map, identity semantics, moment/recall/diffusion diagnostics, and Gateway injection inspection remain separate panels rather than being merged into look-alike controls. |
| Shared resources and configuration controls | Superseded by one canonical editor per resource | Buckets, Search, base Breath, Network, and Import are each exposed once as shared P0 capabilities. Buckets stay bounded and nonblocking at the API boundary, with exact `type`/`tags` filtering and selected-page-only previews. Each editable configuration resource has one canonical editor. The Embeddings editor is physically mounted in, and canonically owned by, **Models & Data**; System retains only a link and read-only diagnostics, not a second embedding writer. Compatibility export, GitHub backup, and migration actions continue to delegate to their established guarded controls instead of duplicating mutation code. |
| Dashboard identity and bounded bucket reads | Retained in the unified shell | Authenticated `/auth/status` continues to return only the minimal display identity; `human` consistently uses an explicit override or validated identity fallback, including the Amy/Aki display-name fix. `/api/buckets/light` uses the server-side `BucketManager.list_light` path with bounded `limit`/`offset`, a capped retained prefix, and selected-page-only previews rather than materializing full bucket bodies. Optional `type` is an exact match and `tags` requires exact inclusion of every requested tag; malformed filters and excessive offsets fail at the HTTP boundary. Dashboard assets and APIs resolve from the application root rather than from an entry-route prefix. |
| Managed environment persistence | Implemented as one durable transaction | `OMBRE_ENV_PATH` identifies the absolute, non-symlink Dashboard-managed source shared by the active services. Supported deployments place `.ombre-managed.env` inside an already-persistent directory, separate from the operator/Compose `.env`, so atomic replacement remains available and shell parsing never touches Dashboard-written secrets. Configuration routes validate persistence before mutation, serialize all environment writers through the shared lock, and preserve unrelated lines. Runtime/provider rebuilds, YAML persistence, and environment persistence form one failure boundary: a failed later commit restores runtime state and any earlier YAML change without publishing staged secrets. |
| Dashboard entry points and legacy navigation | Compatibility retained | `/` serves the canonical shell in the Shared/Buckets state. `/memory-dashboard` serves the same shell in the Memory/Reminders state. `/dashboard` retains its `302` compatibility redirect to `/memory-dashboard`. On the production host these compatibility URLs are `https://brain.btombre.men/`, `https://brain.btombre.men/memory-dashboard`, and `https://brain.btombre.men/dashboard`; their post-deployment reachability is part of the pending verification below. Legacy P0/current hashes and tab names map to stable workspace/panel destinations. |

Verification status for this batch:

- Dashboard unification and the implementation dispositions above: **complete in
  the current working tree**.
- Final local verification gates: **complete**.
  - `pytest`: **2535 passed / 75 skipped / 0 failed**.
  - Ruff: **clean**.
  - Changed production Python files: **0 Pyright errors**.
  - Whole-tree Pyright audit: **469 errors / 21 warnings**, improved from the
    recorded **530 errors / 21 warnings** without any increase in warning count.
  - Node syntax: **clean**.
  - `docker build .`: **successful**.
  - Independent code, Python, TypeScript, security, and root reviews: **all
    findings resolved**.
- Staging browser smoke, route compatibility, authentication, identity display,
  and console-cleanliness verification on isolated staging: **complete**.
  Full manifest-family parity and persisted-data/browser matrix coverage remain
  pending.
- Production deployment and public production-alignment verification: **pending**.

This entry records a completed implementation with verification and deployment
still in progress. It does not advance an upstream baseline or claim final-suite,
staging, or production completion.

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
  staging.

Production cutover verification:

- The final P0luz-base tree was connected to the historical production lineage
  by merge commit `c806f78`; the merge did not change the verified tree.
- Production Brain and Gateway run image `ombre-brain:p0luz-main-c806f78` with
  loopback health checks returning 200 and the persisted runtime source hash
  matching the verified image.
- The public production-alignment suite passed Claude 4.8, native Gemini, relay,
  native Claude MCP tool use, and the expected current Ombre tool set.
- Production loaded `/state/config.runtime.yaml`, retained the historical
  118-event Persona database, and honored Dream retention settings.
- A live SiliconFlow reranker request returned two results with the dedicated
  reranker configuration; fresh Brain and Gateway logs contained no error-level
  entries after cutover.
- Rollback is retained as stopped containers based on `8e68a7d`, fork branch
  `codex/pre-p0luz-cutover-20260716`, and a verified pre-cutover data archive.

### 2026-07-17 runtime consolidation

| Source range | Disposition | Evidence |
| --- | --- | --- |
| P0luz `6da5158..p0luz/main` | No newer commits at integration time | `p0luz/main` still resolves to `6da5158` (`v2.7.6`). |
| Yinglianchun `4e0a546..bbd6500` | Ported | The config bind-source deployment guard, mounted config/environment backup behavior, and author regression script are retained in the shared operations scripts. |
| Fork `d3c66b8..HEAD` | Consolidated without contract changes | The active runtime now has one 30-tool manifest and shared recall, profile, configuration, SQLite, semantic-search, metadata, edge, operation, and diagnostics policies. Compatibility tests preserve both upstreams' externally observable behavior. |

Local verification for this batch:

- `pytest`: 2,213 passed, 79 skipped, 3 known warnings.
- Ruff and Vulture: clean across the repository.
- Pyright: zero errors across every changed Python file. The whole-tree audit
  improved from 667 errors and 21 warnings at `d3c66b8` to 530 errors and 21
  warnings without suppressing the dormant v3/distributed annotation debt.
- Exact AST comparison found no repeated multi-statement function bodies in the
  active `src/` runtime. The remaining small repetitions in dormant v3 modules
  stay local to their public bounded-context interfaces.
- `scripts/test_ops_bind_guard.sh`: passed under Git Bash.
- The local container image and Cloudflare compose configuration build and
  validate successfully; staging and production evidence is recorded after the
  exact committed image is deployed.

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
