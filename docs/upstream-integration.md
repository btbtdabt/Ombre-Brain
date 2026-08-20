# Upstream Integration Policy

This repository uses the P0luz implementation as its executable base and keeps
Yinglianchun's repository as a secondary source of product behavior. Feature
preservation is opt-out, not opt-in: every user-visible or production-runtime
capability is ported unless this document records a concrete exclusion and its
verification evidence.

## Recorded Baselines

- P0luz primary baseline: `v2.17.9` / `594d636`
- Yinglianchun secondary baseline: `284c9c7`
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

### 2026-08-20 P0luz 3.2.0 quotes, bidirectional relations, automatic relations, and connector views

This integration batch reviews `594d636..6f7335d` from P0luz. Yinglianchun
`main` remains at the recorded `284c9c7` baseline, so there are no secondary
upstream commits to port in this range. The recorded P0luz baseline remains
`594d636` until the exact combined commit passes isolated staging.

| Source commit(s) | Disposition | Evidence |
| --- | --- | --- |
| P0luz `c7a0883` | Merged from the author and adapted to the unified Dashboard | Human archive/delete-to-archive now has one approval entry and produces a tombstoned archive after approval. Low-level decay/system archive remains a distinct operation. Dashboard and web-path regressions cover both boundaries. |
| P0luz `d3ceb7a` | Merged from the author | Identical `grow` requests share an in-flight/completed result for 30 minutes, preventing client timeout retries from writing duplicate buckets while allowing genuine failures to be retried. |
| P0luz `666545f`, `480dfa1` | Superseded by combined-tree release generation | Upstream-only manifests are not retained verbatim. `deploy/gen_update_manifest.py` regenerates hashes from the final combined Git index after all integration changes. |
| P0luz `691d5d9`, `f809ea1`, `58aa11a` | Merged by ancestry | These merge commits contain only the already classified Dashboard and grow changes. |
| P0luz `0fbc287`, `d57f520` | Merged from the author and registered through the canonical manifest | New relations are ID-first and bidirectional, fixed relation types automatically mirror reverse semantics, custom labels support an explicit reverse label, detach/restore updates both mirrors under ordered locks, and legacy one-way ledgers remain readable and locally manageable. Tool schemas expose the seven-value enum and optional title guards. |
| P0luz `14c55ee`, `7c88175` | Merged and superseded by the final 3.2.0 release metadata | The 2.17.10/2.17.11 relation history remains in the changelog; final version files advance to 3.2.0. |
| P0luz `10d8722` | Selectively merged; destructive surface reduction superseded by the combined compatibility contract | The standalone keyword-scoped `feel` tool, timezone/config corrections, Plan read path, Docker/config fixes, and applicable regressions are retained. Removing Source/Relation actions and shrinking the main connector is intentionally excluded: this fork keeps its established Source, Relation, Gateway, and current/Ying clients through one 40-tool canonical manifest. Contract and server integration tests assert the retained surface. |
| P0luz `d46e34c`, `033a894` | Merged from the author and adapted to current hold/grow wrappers | Explicit `quotes` can be stored only at write time, with the author's count/length validation and merge warning. Quotes remain outside embeddings and passive surfaces; `breath_search(quotes=True)` is their sole public read path. Static MCP contract snapshots cover the added parameters. |
| P0luz `176deb7` | Merged and superseded by 3.2.0 release metadata | The 3.1.0 quote history remains in the changelog while version and final manifest advance to 3.2.0. |
| P0luz `fc95865` | Merged from the author and adapted to shared connector registration | Provider-free automatic relation inference and relevance-ranked Dream feels are retained. `/mcp-extra` is restored with the author's auth/request-size boundaries, but it mirrors the three canonical Letter implementations instead of removing them from the backward-compatible main `/mcp` surface. |
| P0luz `198b814` | Merged from the author | Pinned/permanent core memories and anchors remain visible through their intended surfaces even when historical data carries `digested`; ordinary digested memories remain passive-hidden. |
| P0luz `c78d137` | Merged from the author through shared configuration | Pure semantic admission uses configurable `matching.vector_recall_threshold`, defaulting to `0.55`, without changing the seven-dimensional ranking weights or weakening domain/type filters. |
| P0luz `6da3f05` | Merged from the author | The retrieval design-debt explanation remains in the 3.2.0 changelog alongside the configurable threshold. |
| P0luz `4249ef8`, `09d075f` | Merged and adapted to the two-view connector contract | Docker/web tests exercise `/mcp-extra` as the Letter-only view while the independent canonical snapshot continues to assert all 40 tools on `/mcp`. Both views use the same handlers and schemas. |
| P0luz `6f7335d` | Selectively merged into combined documentation and tests | The active `/mcp-extra` behavior is retained. Upstream wording that describes mutually exclusive 13-tool/3-tool connectors is superseded by the tested 40-tool main endpoint plus optional three-tool Letter mirror. |

Pre-staging local verification for this batch is recorded below once the full
suite, lint, changed-file type checks, update manifest, and container build
complete. The P0luz baseline advances only after isolated staging verifies the
exact integration commit and copied production data remains intact.

### 2026-08-15 P0luz 2.17.9 source/relation bindings and breath headroom

This integration batch reviews `278668e..594d636` from P0luz. Yinglianchun
`main` remains at the recorded `284c9c7` baseline, so there are no secondary
upstream commits to port in this range. P0luz remains the implementation base;
the author's new tools are adapted into the canonical current-tool manifest
rather than registered a second time in `src/server.py`.

| Source commit(s) | Disposition | Evidence |
| --- | --- | --- |
| P0luz `60b16cc` | Merged from the author and adapted to the current breath composer | Selected pinned memories consume the explicit token budget before anchors, dynamic memories, related memories, or Dream overlays. If the selected core cannot fit, ordinary surfacing is skipped instead of silently displacing a pin. Current Dream overlays remain available when the selected core fits. |
| P0luz `1815fb0` | Merged through the shared configuration boundary | Explicit `breath_max_tokens` accepts up to 40,000 tokens across YAML, Dashboard controls, API validation, and the canonical MCP adapter. The ordinary default remains unchanged and `feel_max_tokens` retains its 20,000-token ceiling. |
| P0luz `d9fbc22`, `1d6694e` | Merged from the author and registered through the canonical manifest | Reversible source attachments use an append-only `source_links` ledger with an active `source_refs` projection. Attach, detach, restore, selective read, immutable content-addressed evidence, archive/lock behavior, backup closure, import preflight, and index non-mutation are covered by the author's applicable tests. Final blob publication occurs under the bucket mutation lock after the repeated title/limit checks, so a rejected concurrent attach does not leave an unreachable source object. |
| P0luz `69c6c60`, `8ea3541`, `9669f02` | Merged from the author and registered through the canonical manifest | Reversible typed memory relations remain metadata-only, preserve unrelated bucket lifecycle fields, remap safely during import, render bounded hints in breath/Dream, and expose read/attach/detach/restore through the shared `/mcp` endpoint. Restore revalidates the current target, and import detaches every active target that was not successfully mapped into the destination vault. |
| P0luz `467ac02` | Merged from the author | Claude conversation exports are recognized by import preflight without weakening the existing bounded parser, provenance, or migration safety boundaries. |
| P0luz `08afeed`, `73d9bd7`, `b64bfbd`, `5e98d58` | Merged from the author and regenerated for the combined tree | Version/changelog history advances through 2.17.9; the final hot-update manifest is generated from the combined Git index rather than retaining an upstream-only file inventory. |
| P0luz `c84a7e6`, `08965bf`, `bb7fdae`, `594d636` | Merged by ancestry | These merge commits contain only the already classified author changes and introduce no competing runtime owner. |

The combined public MCP contract contains **39 unique tools**: P0luz's current
23-tool surface plus 16 retained current/Yinglianchun extensions. Every tool is
registered once through `src/tools/current/manifest.py`, exposed through the
same authenticated `/mcp` endpoint, and represented in the Docker contract
snapshot.

Local verification before isolated staging:

- Focused source/relation/breath/backup/migration/manifest suite: **263 passed / 0 failed**.
- Full pytest suite: **3207 passed / 117 skipped / 0 failed**.
- Whole-tree Ruff: **clean**; all **35 changed Python files**: **0 Pyright
  errors / 0 warnings**.
- The `src` Pyright debt audit is **61 errors / 21 warnings**, improved from the
  recorded **68/21** baseline without weakening its configuration. The full
  repository audit is **349 errors / 21 warnings**, concentrated in the
  intentionally dormant v3/distributed stack and historical compatibility
  tests.

Isolated VPS staging completed for integration commit `4aae235` before the
baseline advanced:

- Image `ombre-brain:stage-4aae235` was built from a detached worktree at the
  exact commit, with fresh staging buckets/state and production data untouched.
- Brain and Gateway health returned HTTP 200 and reported version `2.17.9` with
  the configured Claude Opus 5 main route, Gemini 3.7 Flash auxiliary routes,
  and the configured reranker ready.
- Authenticated MCP initialize/list returned **39 tools / 39 unique names**;
  a read-only `pulse` call returned HTTP 200 without a tool error.
- Brain and Gateway startup/call logs contained no error or traceback.

This staging evidence advances the recorded P0luz baseline to `v2.17.9` /
`594d636`. Production cutover and post-cutover alignment are verified against
the follow-up commit that records this evidence.

### 2026-08-14 P0luz 2.17.5 memory lifecycle, Letter locks, deletion approval, and Ying raw-event sync

This verified batch reviews `0bb1e4d..278668e` from P0luz and
`2651e2a..284c9c7` from Yinglianchun. P0luz remains the implementation base.
The Yinglianchun date-window correction is ported at the existing modular raw
event boundary instead of introducing a second runtime owner.

| Source commit(s) | Disposition | Evidence |
| --- | --- | --- |
| P0luz `03a0a1c` | Merged from the author | GPT-5.x Chat Completions uses the correct output-token parameter while the existing provider abstraction remains intact. |
| P0luz `a251563`, `cca0cd4` | Merged from the author | Pinned/anchor guidance is corrected and anchors remain exempt from automatic decay archival. |
| P0luz `08dd317` | Merged from the author | Obviously misaligned `source_ranges` are rejected before source-backed memory is persisted. |
| P0luz `5c402d2` | Merged from the author | Successful stdio startup clears the boot marker, preserving reliable restart diagnostics. |
| P0luz `79227b3`, `cb47ffe`, `fc1ab15`, `df434cb`, `f5b97f2`, `499e839` | Merged from the author and adapted to the unified current-tool manifest | Timed Letter locks, public Letter relocking, historical AI-name migration, locked-surface enforcement, Letter restore behavior, consistent write/update responses, and `grow(test_data=...)` are retained. `letter_lock_update` is registered once through the canonical manifest and the unified Dashboard exposes the author's lock controls. |
| P0luz `1755e45`, `c0782b5`, `70d608c`, `0b0411d`, `2f74b82`, `3d7a8f9`, `814fb3e` | Merged from the author | Hot-update progress, dependency gating, optional dependency installation, manifest synchronization, Git line-ending-aware SHA checks, the cryptography update, and removal of legacy SSE transport are retained. |
| P0luz `f6f24de`, `7906391`, `16fa34d`, `b6b43fa`, `1815f93` | Merged from the author | Docker integration setup and fixtures follow the current public-hook and anonymized-name contracts; expected outputs cover the combined tool surface. |
| P0luz `49a5301`, `d89d1a8` | Merged into the unified Dashboard | Remote first-run setup guidance and responsive narrow-PC/mobile layout are retained while preserving the fork's path-aware Dashboard/auth shell. |
| P0luz `b6b1eb8`, `94ace4c` | Merged from the author | Restored archive buckets refresh activity, and archived pinned/protected memories preserve their intended visibility and protection semantics. |
| P0luz `65bda95` | Merged from the author | `breath` and `dream` security envelopes are compacted without changing retained Gateway/current retrieval behavior. |
| P0luz `56d5877` | Merged from the author | The stdio lifespan owns the vector queue and the author's slow-provider create/merge regressions are retained. |
| P0luz `0867ae8` | Merged from the author; supersedes the prior Spark retention decision | Spark/inspiration and the hard importance quota are intentionally removed, and the author's cleaned `breath`/`dream` output boundary is authoritative. The dormant distributed/v3 stack remains separate from this removed experiment. |
| P0luz `ad73eb7` | Merged from the author | Memory reasons, anchor markers, and update-manifest safety are hardened together. |
| P0luz `e1fb27f`, `945d4ff` | Merged from the author | `grow` and provider errors are sanitized and actionable without making unsupported API-key claims. |
| P0luz `0bfc768`, `89bcd16`, `52f8128`, `ee39748` | Merged from the author | Dream records `I` witnesses, excludes pinned content, preserves the core selector boundary, and keeps pending `I` candidates eligible. |
| P0luz `231c8bc` | Merged from the author and combined with current metadata normalization | `hold` accepts an optional domain override; explicit tags remain unioned with model-derived tags instead of being discarded. |
| P0luz `e1a99ab`, `1db60df` | Merged from the author | Plan resolution requires an explicit decision and stale resolution suggestions expire. |
| P0luz `f3af09e` | Merged from the author | Human deletion requests require AI approval through the author's persisted request store, tool boundary, and Dashboard workflow. |
| P0luz `3e52912` | Merged from the author | `hold` can attach hidden source evidence without exposing it through ordinary memory surfaces. |
| P0luz `3d07b33`, `a814bcf`, `d32bfe8`, `88d4fc6` | Merged from the author | Version, changelog, dependency-lock, and release-manifest history is advanced through the author's release sequence and regenerated for the combined tree. |
| P0luz `d9cd468`, `b622db4`, `9964fc7`, `58572a1`, `4947cf7`, `0fd347f`, `5f3ec2e`, `2957c09`, `e76b5ed`, `13fff11`, `db758bd`, `fa4f5cd`, `3fb5b2d`, `a3cb116`, `278668e` | Merged by ancestry | These merge commits contribute the already classified author changes and no competing runtime owner. |
| Yinglianchun `284c9c7` | Ported into `src/raw_events.py` with compatibility coverage | Date filtering reads a padded physical event window before applying exact logical day boundaries, so timezone-offset events near midnight are no longer omitted. |

The combined public MCP contract contains 32 unique tools: P0luz's expanded
16-tool core plus 16 retained current/Yinglianchun extensions, each registered
once through `src/tools/current/manifest.py` on the same `/mcp` endpoint.

Verification complete for integration commit `f55f646`:

- Full pytest suite: **3144 passed / 110 skipped / 0 failures**.
- Whole-tree Ruff: **clean**; Pyright on every changed Python file: **0 errors**.
- Hot-update manifest: **298 files**, version `2.17.5`, exact repository-byte
  check passed.
- Local and VPS Docker builds passed. The isolated VPS Brain and Gateway both
  returned healthy status on fresh storage, reported version `2.17.5`, and the
  Brain registered the expected **32 unique MCP tools**.

### 2026-08-05 P0luz source evidence, Spark, I sediment, and Ying generation-failure sync

This batch reviews `ea5d8f5..0bb1e4d` from P0luz and
`c758a4d..2651e2a` from Yinglianchun. P0luz remains the implementation base;
Yinglianchun's distinct generation-failure behavior is ported at the matching
Persona/Reflection worker boundaries. Both recorded baselines advanced after
the exact integration commit passed isolated staging.

| Source commit | Disposition | Evidence |
| --- | --- | --- |
| P0luz `7b74699` | Merged from the author | MCP local-mode bind and authentication boundaries, configuration validation, documentation, and regression tests are retained. |
| P0luz `1ba4e7c` | Merged from the author | OAuth/static-token coexistence, import boundary hardening, release metadata, and the author's tests are retained in the modular runtime. |
| P0luz `b9c0c48` | Merged from the author | The CI listener-security guard's intentional test annotation is retained without weakening the runtime guard. |
| P0luz `360c9dd` | Merged from the author | Dehydration thinking configuration and actionable import UI failures are retained and combined with the current configuration API. |
| P0luz `1d1f2cd` | Merged from the author | Source-backed imports and structured grow entries retain precise explicit titles instead of replacing them with storage filenames. |
| P0luz `07f5eaf` | Merged from the author | The content-addressed source store, bounded `source_read`, source-reference validation, backup/restore integration, and source-layer tests are retained. |
| P0luz `8904f47` | Merged into the unified Dashboard | Reverse-proxy login origin handling is retained while the current single Dashboard shell remains the visible owner. |
| P0luz `4203516` | Merged from the author | The independent bucket co-activation edge projection and its regression coverage are retained. |
| P0luz `075515f` | Merged from the author behind the existing dormant research boundary | Spark R1, shadow, pilot, evaluation tools, fixtures, and tests are retained verbatim without entering the single-node production boot path. |
| P0luz `50321ea` | Merged from the author as an explicit read-only Dream option | Spark inspiration candidates remain response-only, provider-free, non-persistent, and opt-in; the author's security and policy tests are retained. |
| P0luz `cb4152e` | Merged from the author | Explicit MCP no-auth behavior and the clarified activation-score contract are retained. |
| P0luz `5944ced` | Merged from the author | The associated regression-only correction is retained. |
| P0luz `d051237` | Merged by ancestry | This upstream merge commit contributes no competing runtime owner beyond the already classified P0 changes. |
| P0luz `b2f7166` | Merged from the author and combined with current metadata extensions | Source provenance, atomic unpin/quota behavior, structured direct import, optional titles, and current media/meaning fields share the P0 storage boundary. |
| P0luz `5a01ed5` | Merged from the author | `I` now uses the candidate-to-sediment lifecycle, including three distinct Dream dates before explicit promotion, with the author's tests and tool guidance. |
| P0luz `6f3f5a8` | Merged from the author | Hot-update manifests are generated from Git index bytes so line-ending conversion cannot invalidate release archives. |
| P0luz `0bb1e4d` | Merged from the author | Manifest round-trip verification uses a real temporary Git repository and exact repository bytes. |
| Yinglianchun `d7551d2` | Ported into the P0 worker modules | Reflection and portrait generation no longer manufacture deterministic long-term state when the configured model is unavailable or fails. |
| Yinglianchun `2651e2a` | Ported into the P0 worker modules and pytest suite | Invalid or failed generated updates return a skipped result without writing buckets/state. The author's standalone verifier is represented by equivalent maintained pytest cases instead of a duplicate script. |

The public MCP contract after integration contains 31 unique tools: P0luz's 15
core tools plus 16 retained current/Yinglianchun extensions, all registered once
through `src/tools/current/manifest.py` on the same `/mcp` endpoint.

Verification complete:

- Focused source/grow/I/GitHub/Dream/import/Dashboard/worker suites: **440
  passed / 1 skipped / 0 functional failures** after updating the retained
  31-tool description marker.
- Full pre-commit suite: **3198 passed / 102 skipped / 0 failures**.
- Repository-wide Ruff, Python bytecode compilation, the update manifest, and
  all **49** changed production Python files: **clean / 0 Pyright errors / 0
  Pyright warnings**. The whole `src` debt audit remains **68 errors / 21
  warnings**, all outside this batch's changed production files and primarily
  in the intentionally dormant v3/distributed tree.
- All five changed inline Dashboard/onboarding scripts parse successfully under
  Node, and independent code/Python reviewers reported no remaining findings.
- VPS staging built `ombre-brain:staging-1999c70` from exact commit `1999c70`
  and started isolated Brain/Gateway containers on loopback ports 28001/28002.
  The copied 156-bucket/1086-state-file dataset passed `PRAGMA quick_check` for
  all 12 SQLite databases both before and after smoke tests; production mounts
  and containers remained untouched.
- Staging verified Brain/Gateway health, OAuth-authenticated MCP initialize/list
  and read-only `pulse`, all 31 unique tools, Claude OpenAI-compatible and native
  Anthropic routes, native Gemini, embedding, reranker, Persona post-reply
  updates, non-persistent Reflection JSON generation, Persona portrait update,
  and authenticated Dashboard worker reads. No staging 5xx, fatal, traceback,
  or worker-generation errors were observed.

### 2026-07-26 Gateway continuation, Persona JSON, and auth cache fixes

This batch reviews `0b4a877..c758a4d` from Yinglianchun and
`0582a3b..ea5d8f5` from P0luz. The authentication work first appeared on
P0luz's `codex/integration-dashboard-auth-fixes` branch and was ported while
the audit was in progress. When P0luz released the same work on `main` as
2.8.11, its Git history, release metadata, and compatible tests were merged
directly.

| Source commit(s) | Disposition | Evidence |
| --- | --- | --- |
| Yinglianchun `f7fbfb6` | Ported into the modular Gateway | OpenAI-compatible and native Anthropic routes now retain the exact first-round stable/dynamic injection prefix through tool continuations. Snapshot lookup binds the original message prefix to the model/tool contract, keeps intermediate tool calls and results, expires stale entries, and clears only after a final assistant response. Compatibility tests cover single injection, protocol-tail preservation, contract mismatch, route enablement, and final-only cleanup. |
| Yinglianchun `c758a4d` | Provider-neutral behavior ported; provider defaults superseded | Persona evaluator and conflict-scout requests use the author's provider-native JSON response option, including the author's focused conflict-scout token/timeout overrides. The option defaults on, remains configurable through YAML and the Dashboard configuration API, and can be disabled for incompatible providers. DeepSeek V4 model/base/thinking defaults are not imported because P0luz's modular defaults and the documented Claude/Gemini production routing contract remain authoritative. |
| P0luz `a1819f8`, `64b3777`, `ea5d8f5` | Merged from the author's released `main` | Dashboard and onboarding `/auth/status` requests explicitly bypass browser caches, and the backend returns `Cache-Control: no-store`. Conflict resolution keeps the unified Dashboard's path-aware URL handling, bounded retry/abort flow, authenticated identity payload, and session-generation protection while retaining the author's externally observable fix and tests. |
| P0luz `5e1b2d6` | Merged with the richer current error contract | Login failures display `error`, then `detail`, then the localized fallback. This preserves the author's backend-error behavior without narrowing current API compatibility. |
| P0luz `9ef00f4` | Merged as part of the official 2.8.11 release | Both version files and the P0luz changelog now carry the author's released 2.8.11 metadata. |

Local verification:

- Focused Gateway/Persona compatibility suite: **120 passed / 0 failed**.
- Focused P0luz 2.8.11 authentication/onboarding suite: **57 passed / 0 failed**.
- Full suite: **2742 passed / 95 skipped / 0 failed**.
- Repository-wide Ruff and Dashboard/onboarding JavaScript syntax: **clean**.
- All changed Python files: **0 Pyright errors / 0 warnings**. The whole-tree
  debt audit remains **371 errors / 21 warnings**, unchanged from the recorded
  baseline.
- Local Docker image `ombre-brain:upstream-audit-20260726-p0-2811` built
  successfully, reports version 2.8.11, and compiled the changed production
  modules inside the image.
- Exact integration commit `e9d5bb0` built on the VPS as
  `ombre-brain:staging-e9d5bb0` and passed isolated staging.
  - Brain and Gateway returned 200 on loopback-only ports using a fresh copy
    of production config, buckets, and state.
  - All 12 copied SQLite databases returned `PRAGMA quick_check = ok` before
    and after boot.
  - Authenticated Streamable HTTP MCP negotiated protocol `2025-03-26`,
    exposed 30 unique tools, and successfully called the read-only `pulse`.
  - The Dashboard auth-status route returned `Cache-Control: no-store`.
  - Empty-state live tests returned 200 through native Anthropic, native
    Gemini, and OpenAI-compatible Claude without exposing copied production
    memory to providers.
  - A real two-round Claude tool exchange produced one tool call, preserved
    the author's first-round injection snapshot on continuation, consumed the
    tool result, cleared the snapshot on the final answer, and did not run
    Persona on the intermediate tool-call response.
  - Persona completed both eligible exact-commit final replies with zero error
    rows. Synthetic embedder and reranker checks returned a 3072-dimensional
    vector and ranked the relevant document first.
  - Fresh exact-commit Brain and Gateway logs contained no warning or error
    entries.

Both upstream baselines were advanced only after the local and isolated
staging gates above passed.

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
- Production alignment: **complete**.
  - `fork/main` and the clean VPS production checkout were fast-forwarded to
    the verified integration tree.
  - A stopped-service archive of `/srv/ombre-brain` was created before cutover;
    its stored SHA-256 checksum and tar manifest both verify successfully.
  - Production Brain and Gateway passed loopback and public health checks, and
    all 12 live SQLite databases returned `PRAGMA quick_check = ok`.
  - The production Persona migration marker is present, with all 20 historical
    session values preserved at `0.18`.
  - The public production-alignment suite passed Claude 4.8, native Gemini 3.5,
    relay completion/debug, native Claude MCP tool-loop, and model-routing
    checks.
  - Direct authenticated production MCP exposed 30 unique tools and successfully
    called `pulse`; fresh production logs contained no error or warning entries.
  - The previous `41b0b2d` Brain and Gateway containers remain stopped under
    rollback names.

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
