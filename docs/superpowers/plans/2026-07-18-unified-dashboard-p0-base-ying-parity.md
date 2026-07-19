# Unified Dashboard on the P0luz Base — Ying/Current Parity Plan

- **Status:** Implementation complete; local verification complete; staging and deployment in progress
- **Primary base:** P0luz modular runtime and Dashboard
- **Secondary feature source:** Yinglianchun/current Dashboard behavior
- **Goal:** One coherent Dashboard experience, every retained Ying/current function available, no repeated implementations, and clear boundaries between distinct functions.

---

## Problem Statement

Before this batch, Ombre Brain exposed two large frontend applications:

- the P0luz-based system Dashboard at `/`;
- the Ying/current memory Dashboard at `/memory-dashboard`.

They share authentication, bucket browsing, search, Breath diagnostics, network, import, configuration loading, retry behavior, and common presentation logic. Those shared concerns have drifted because each page implements its own copy. The recent bucket-loading and identity mismatch bugs are examples of the maintenance cost.

The two pages are not interchangeable, however. The P0luz Dashboard owns the system and operational control plane, while the Ying/current Dashboard contains memory-care, Persona, portrait, profile, dream, Gateway, and recall-tuning capabilities that must remain available.

The implemented result is not a concatenated page. It is one P0luz-based shell with shared capabilities implemented once and distinct capabilities kept as separate feature modules.

## Current Implementation State

Implementation phases 1–8 are complete in the current working tree. Phase 9
local verification is complete, but staging and production remain pending; this
document does not claim staging success or production cutover for this batch.

- `frontend/dashboard.html` is the single canonical shell served for `/` and
  `/memory-dashboard`; `/dashboard` remains a compatibility redirect into that
  shell rather than a second application.
- The Embeddings editor is physically moved into the `models-embeddings` host
  and is canonically owned by **Models & Data**. System exposes only the link and
  read-only diagnostic context; it has no second embedding save surface.
- Lightweight bucket reads use bounded server-side pagination. The backend
  retains only the requested ordered prefix, reads previews only for the selected
  page, caps offsets, and applies exact `type` plus all-requested exact `tags`
  filtering before returning the page.
- Environment-backed configuration uses the managed `OMBRE_ENV_PATH` source as
  one durable transaction. Shared locking, atomic or bind-file-safe persistence,
  runtime staging, YAML rollback, and secret publication ordering prevent a
  failed partial update from becoming the active or restart-time configuration.
- The unified shell now presents one canonical dashboard with bounded,
  nonblocking Buckets reads, transactional config/Gateway persistence, verified
  backup tickets, Ying Reflection/profile/insights parity, and the Amy identity
  display-name fix.
- Local verification gates are green: `pytest` 2531 passed / 75 skipped / 0
  failed, Ruff clean, changed production Pyright 0, whole-tree Pyright 491
  errors / 21 warnings (improved from the recorded 530 / 21 without any warning
  increase), Node syntax clean, `docker build .` successful, and independent
  code/Python/TypeScript/security/root reviews resolved all findings.

## Non-Negotiable Requirements

1. Keep the P0luz modular runtime as the architectural and deployment base.
2. Retain every user-visible Ying/current feature unless an exclusion is explicitly justified, documented, and covered by replacement evidence.
3. Present one Dashboard experience with one navigation model and one authentication session.
4. Implement each shared concern once.
5. Keep distinct concepts, APIs, actions, and stored-data semantics separate.
6. Preserve all backend route contracts during the frontend migration.
7. Preserve `/`, `/dashboard`, and `/memory-dashboard` as working entry points.
8. Preserve existing bookmarks through stable workspace/tab deep links.
9. Do not change bucket formats, identity semantics, Gateway state, Persona state, portrait state, reminders, plans, letters, or other persisted data as part of this refactor.
10. Do not move Ying's historical root runtime into production. Port only its retained behavior into P0luz seams.
11. Do not add a new production frontend framework or dependency without separate approval.
12. Keep every incremental commit deployable and green.

---

## Product Information Architecture

The unified shell has four top-level areas. These are navigation groups, not separate applications.

### Shared

Capabilities that operate on the same canonical data and should exist only once:

- Home/status summary
- Buckets
- Search
- Bucket detail and editing
- Breath trace
- Memory network
- Import and import review
- Shared loading, timeout, retry, and error states

### Memory

Ying/current functions retained as distinct panels:

- Care reminders
- Daily impressions/reflection
- Automatic chat-memory candidates and review
- Dreams
- Darkroom door/status
- Persona state
- Portrait state and maintenance
- Profile facts
- Profile-fact proposals and confirmation
- Anchor proposals and confirmation
- Word Map Lite
- Identity semantics and rebuild
- Moment diagnostics
- Recall diagnostics
- Diffusion diagnostics
- Gateway injection inspection
- Memory surfacing/tuning controls

### Models and Data

Capabilities that are currently scattered across both Dashboards but represent one resource each:

- Gateway upstream model selection
- Dehydration/tagging model settings
- Embedding configuration and maintenance
- Reranker configuration
- Persona model settings
- Dream model settings
- Relationship-memory processing settings
- Daily portrait settings
- Effective configuration inspection
- Full-vault backup and restore
- Simple compatibility ZIP export, kept distinct from the verified full-vault export
- GitHub backup/sync
- Deployment migration tools

Each capability remains its own panel and action set. The unification removes duplicate editors, not functional boundaries.

Historical conversation/Operit import remains the single Shared Import workflow. Models and Data may link to it, but must not register another Import destination or editor.

The Embeddings editor itself is mounted in Models and Data. System may display
read-only health/onboarding information and a deep link to that panel, but it
must not retain editable embedding fields or independent save logic.

### System

P0luz functions retained as distinct panels:

- Runtime and service status
- First-run onboarding state and embedding-configuration warning
- Plans
- Letters
- Anchors
- Logs
- Structured errors
- Diagnostics and repair tools
- Human/AI display identity settings
- Authentication and recovery settings
- MCP configuration and token management
- Transport configuration
- Environment configuration
- Tunnel management
- GitHub integration
- Version and update controls
- Restart controls
- Developer/test-data controls
- v2.4 replay/debug tooling
- About/author information

### Canonical-Editor Rule

When two current panels edit the same underlying resource, the unified Dashboard provides one canonical editor. Other workspaces may show a read-only summary and a link to that editor, but must not duplicate the form or save logic.

Examples:

- Embedding configuration is edited once under Models and Data; System diagnostics may link to it.
- Import is one shared workflow; Memory and Data navigation may deep-link to the same panel.
- Base Breath trace is shared; moment/recall/diffusion diagnostics remain separate advanced Memory panels.
- Human display identity remains a System setting; evidence-backed profile facts remain a separate Memory function.
- Plans and reminders remain separate because they have different trigger and persistence semantics.
- Existing anchors and anchor proposals remain separate actions within one Anchors area.

---

## Complete Ying/Current Parity Inventory

The machine-checkable parity manifest created in Phase 1 is the final authority. At minimum it must account for every family below.

### Memory CRUD and Inspection

- Light and full bucket listing
- Bucket search
- Bucket detail and raw content
- Bucket creation and editing
- Bulk update
- Single and bulk delete
- Bucket comments/year rings
- Moment listing and bucket-linked moment diagnostics
- Raw ingest and raw search
- Memory edges and domain taxonomy

### Care and Private Reflection

- Reminder list, filtering, creation, completion, snooze, and update
- Compatibility todo views and writeback behavior where still exposed
- Darkroom status/door behavior

### Daily Processing

- Reflection/daily-impression execution and calendar/history UI
- Daily activity summary execution
- Automatic chat-memory execution
- Pending chat-memory candidates
- Candidate edit, confirmation, and rejection

### Dreams and Persona

- Dream timeline
- Dream detail
- Persona state and recent Persona events
- Persona configuration
- Night Dream configuration

### Portrait, Profile, and Relationship Memory

- Portrait state views for supported scopes
- Portrait maintenance
- Portrait item add/update/delete
- Stable portrait update, lock, and rollback
- Portrait reset
- Profile facts
- Profile-fact proposal generation and confirmation
- Profile fact update and deletion
- Anchor proposal generation and confirmation
- Relationship-memory organization controls
- Self-entry display

### Semantic and Recall Tools

- Word Map Lite nodes and edges
- Word-map rebuild
- Word-map cards where supported by the API
- Private aliases and identity boundaries
- Identity semantics listing and rebuild
- Recall debug
- Diffusion debug
- Moment candidates and diffusion paths
- Gateway recent-injection inspection

### Current Configuration and Operations

- Effective configuration view
- Upstream model configuration
- Dehydration/tagging model configuration
- Embedding configuration
- Reranker configuration
- Memory surfacing configuration
- Other current parameters
- Full-vault export
- Full-vault restore modes
- The one shared Import workflow: upload, status, pause, results, patterns, and review

The Import entry above uses the same canonical Shared feature ID and destination described in the product information architecture. It is listed here only to prove Ying/current parity; it must not create another panel.

Every item must be classified as one of:

- shared canonical panel;
- P0/System-only panel;
- Ying/Memory-only panel;
- compatibility route with no direct panel;
- superseded by an equivalent canonical panel;
- intentionally excluded with documented evidence.

No feature may disappear merely because it was not represented by a top-level tab.

---

## Technical Architecture

### Frontend Shell

Keep `frontend/dashboard.html` as the canonical P0luz shell and progressively reduce it to layout, mount points, and safe boot metadata. Use the existing `frontend/dashboard-assets/chat-memory.js` extraction as the precedent for modular frontend assets.

Create focused assets under `frontend/dashboard-assets/`:

- `core/path.js` — mount-prefix and URL handling
- `core/api.js` — authenticated requests, JSON errors, aborts, timeouts, and idempotent retries
- `core/auth.js` — setup, login, logout, recovery, session state, and the single auth overlay
- `core/router.js` — workspace/tab/deep-link state
- `core/store.js` — small shared state and request deduplication
- `core/ui.js` — escaping, loading, retry, toast, modal, and destructive-action confirmation primitives
- `shared/buckets.js`
- `shared/bucket-detail.js`
- `shared/search.js`
- `shared/breath.js`
- `shared/network.js`
- `shared/import.js`
- `shared/config-client.js`
- `memory/reminders.js`
- `memory/reflection.js`
- `memory/chat-memory.js`
- `memory/dreams.js`
- `memory/darkroom.js`
- `memory/persona.js`
- `memory/portrait.js`
- `memory/profile.js`
- `memory/anchor-proposals.js`
- `memory/word-map.js`
- `memory/identity-semantics.js`
- `memory/recall-debug.js`
- `memory/gateway-injections.js`
- `models/` modules for each distinct model/configuration panel
- `system/` modules for P0luz plans, letters, anchors, logs, settings, diagnostics, integrations, updates, and debug functions

The exact directory names may be adjusted during implementation, but the dependency direction must remain:

`shell -> core -> shared/domain module -> backend API`

Domain modules must not reach into another domain module's private DOM or state. Cross-module navigation and refresh events go through the router/store contracts.

### Backend Ownership

Keep backend ownership unchanged unless a separate parity test proves a shared contract:

- P0luz canonical shared/system routes remain in the modular `src/web` routes.
- Ying/current compatibility routes remain registered through `current_compat.py` and its memory/profile/operations modules.
- `current_contract.py` remains the executable inventory of current route ownership and P0 route conflicts.
- Frontend unification does not justify combining route handlers with different semantics.

Only minimal shell-serving changes are expected in `src/web/dashboard.py` and the legacy `/dashboard` redirect seam. The preferred result is:

- `/` serves the unified shell at the shared home or Buckets view;
- `/memory-dashboard` serves the same shell and boots into the Memory workspace;
- `/dashboard` preserves its existing compatibility behavior while landing in the unified shell;
- workspace and tab state are bookmarkable without creating new backend page applications.

### Shared State Rules

- One auth/session bootstrap per page load.
- One canonical bucket collection in memory.
- One in-flight request per canonical resource unless a caller explicitly requests refresh.
- Navigation must abort stale panel requests.
- GET/HEAD retries may be bounded; writes must never be automatically replayed.
- Failed optional metadata, such as taxonomy, must not block primary bucket rendering.
- Bucket list pagination and exact type/tag filtering execute at the storage/API boundary; the browser does not fetch the full vault and paginate it locally.
- Each mutation invalidates only the resources it owns.
- Configuration writes must refresh the shared effective-config state before another editor is shown.
- Environment-backed configuration commits through the managed `OMBRE_ENV_PATH` transaction and restores staged runtime/YAML state if durable persistence fails.

### Security and Privacy Rules

- Preserve fail-closed authentication behavior.
- Preserve `Cache-Control: no-store` on authenticated memory and identity responses.
- Do not expose identity through unauthenticated boot metadata.
- Keep destructive actions explicit and scoped; never generalize confirmations across unrelated actions.
- Do not place secrets, tokens, passwords, raw private memories, or deployment-local configuration in browser boot payloads, fixtures, logs, screenshots, or the parity manifest.
- Keep current upload and request-size limits.

---

## Testing Decisions

### Test External Behavior

Tests should assert visible capabilities, navigation, requests, response handling, and stored outcomes. They should not lock incidental DOM nesting or private helper names unless the assertion enforces a deliberate architectural boundary such as “one auth client.”

### Parity Manifest

Add `tests/fixtures/dashboard_parity_manifest.json` as a machine-checkable inventory containing:

- entry routes;
- workspaces and panels;
- feature identifiers;
- required API methods and route templates;
- shared/system/memory ownership;
- destructive-action requirements;
- compatibility aliases;
- legacy hash/query/tab aliases and their canonical destinations;
- replacement feature IDs for superseded UI;
- regression test references.

Add a validator test that rejects:

- duplicate feature IDs;
- unclassified current route families;
- a Ying/current UI feature without a unified-shell destination;
- shared resources with multiple canonical editors;
- compatibility aliases without a target;
- a legacy hash/query/tab state without an explicit canonical destination;
- exclusions without a reason and evidence.

### Static and Contract Tests

- Extend the existing dual-Dashboard regression suite during migration.
- Add shell asset, boot mode, and deep-link contract tests.
- Test that both entry routes load the same canonical shell assets.
- Test that shared auth, bucket, network, import, and config implementations are loaded once.
- Test that every manifest panel is registered.
- Test that the current compatibility route contract remains unchanged.
- Update code-fingerprint and static-surface manifests when assets become part of the deployed runtime.

### Backend Integration Tests

- `/`, `/dashboard`, and `/memory-dashboard` behavior
- authenticated and unauthenticated shell bootstrap
- no private identity in unauthenticated responses
- all P0 route-conflict ownership assertions
- all current route-family registration assertions
- config and mutation response compatibility
- no-store headers for sensitive reads

### Browser/E2E Journeys

1. First-time setup from `/`.
2. Login and logout from `/memory-dashboard`.
3. Buckets load once and remain visible if optional taxonomy fails.
4. Search and open a bucket, then return without losing filters.
5. Open every Shared panel.
6. Open every Memory panel and exercise at least one safe read per family.
7. Open every System panel and exercise at least one safe read per family.
8. Verify canonical editors for Import, Embedding, and shared configuration.
9. Verify reminders, plans, profile facts, and identity settings remain distinct.
10. Verify deep links, refresh, browser back/forward, and both legacy entry URLs.
11. Verify mobile navigation and narrow-screen settings.
12. Verify zero browser console errors and zero wrong-prefix requests.
13. Verify the first-run onboarding/embedding warning and both distinct export actions.

Mutation tests must use copied/staging data, never live production data.

### Verification Commands

Each implementation batch runs the smallest relevant tests first, then the required repository checks:

- focused pytest contract and API tests;
- complete pytest suite;
- Ruff across the repository;
- Pyright for every changed Python file, plus the whole-tree debt audit;
- JavaScript syntax checks for every extracted asset;
- Docker image build;
- staging browser journeys;
- production-alignment checker after deployment-related changes.

No implementation phase is complete with an unexplained regression, missing manifest entry, or unverified current/Ying function.

---

## Tiny-Commit Implementation Ledger

The sequence below is retained as the implementation ledger and original slice
boundaries. Phases 1–8 are implemented in the current tree; the numbering does
not claim that every slice landed as a separate final commit. Phase 9 remains the
verification and deployment gate.

### Phase 1 — Freeze Parity Before Moving Code

1. **Document the unified information architecture.** Add this plan and a Dashboard parity section to the upstream integration ledger.
2. **Add the parity manifest schema and validator.** Populate it from the existing two Dashboards and `CURRENT_ROUTE_SPECS`; do not change runtime behavior.
3. **Characterize all three entry routes.** Lock current status codes, cache headers, asset resolution, and authentication behavior.
4. **Characterize shared feature behavior.** Add external-behavior tests for auth, Buckets, Search, Breath, Network, Import, and configuration loading from both current pages.
5. **Characterize all Ying/current feature registrations.** Ensure every parity-manifest Memory feature maps to its current panel and API family before porting.
6. **Characterize P0/System feature registrations.** Ensure every existing P0 tab and nested settings action is represented in the manifest.

### Phase 2 — Establish Shared Core Without Changing the UI

7. **Serve nested Dashboard assets through one tested safe path.** Extend the existing asset contract only if the planned module layout requires it.
8. **Extract mount-prefix and URL resolution.** Both pages consume the same path helper; preserve mounted deployments.
9. **Extract response/error parsing and timeout helpers.** Preserve existing messages and abort behavior.
10. **Extract the authenticated API client.** Preserve fail-closed sessions and GET/HEAD-only retry behavior.
11. **Extract the auth overlay and session bootstrap.** Both pages consume the same setup/login/logout/recovery implementation.
12. **Extract shared loading, retry, modal, and toast primitives.** Do not move domain behavior yet.
13. **Introduce the small shared store and request-deduplication contract.** Add tests for stale-request cancellation and mutation invalidation.

### Phase 3 — Move Shared Product Features Once

14. **Extract the canonical bucket collection and filter state.** Keep existing presentation unchanged.
15. **Extract bucket detail and edit actions.** Preserve P0 and current-only detail fields through capability hooks rather than duplicate renderers.
16. **Extract Search.** Preserve query encoding, session handling, and result navigation.
17. **Extract base Breath trace.** Keep recall/moment/diffusion diagnostics outside this shared module.
18. **Extract the Memory Network.** Preserve graph modes and detail navigation.
19. **Extract Import upload and preflight.** Keep limits and validation unchanged.
20. **Extract Import status, pause, results, patterns, and review.** Use one poller and one result store.
21. **Extract the shared configuration client.** Do not consolidate distinct configuration panels yet.

### Phase 4 — Introduce the Unified P0luz Shell

22. **Add the workspace registry and route-aware router behind the existing P0 layout.** No panel moves in this commit.
23. **Register Shared navigation.** Buckets, Search, Breath, Network, and Import point to the extracted modules.
24. **Wrap existing P0 tabs as System workspace panels.** Preserve their DOM/actions while routing through the new shell.
25. **Add an empty Memory workspace with a parity-driven navigation list.** It must clearly mark unported panels and remain disabled in production navigation until the first panel lands.
26. **Add stable workspace/tab deep links and browser history.** Test refresh and back/forward.

The deep-link contract introduced in this phase must include an explicit compatibility map. At minimum:

- `/` maps to the unified Shared landing view;
- `/memory-dashboard` maps to the unified Memory entry state;
- `/dashboard` preserves its legacy redirect semantics and lands in the unified shell;
- `/#letters` maps to the System Letters panel;
- every legacy P0 and Ying `data-tab` identifier maps to exactly one canonical workspace/panel ID;
- unknown legacy state fails safely to the route's normal landing view without an open redirect or broken shell.

The parity manifest owns this map, and route tests must assert every mapping rather than relying on ad hoc conditionals in feature modules.

### Phase 5 — Port Ying/Current Memory Functions in Independent Slices

27. **Port reminder list and filters.** Read-only behavior first.
28. **Port reminder create/update/complete/snooze mutations.** Add confirmation and refresh tests.
29. **Port Darkroom status.** Keep it read-only and privacy-preserving.
30. **Port daily impression/reflection history and calendar.** Preserve date navigation.
31. **Port manual reflection and daily-activity execution controls.** Keep operations separate from history display.
32. **Attach the existing chat-memory module to the unified shell.** Preserve candidate loading.
33. **Port chat-memory candidate edit/confirm/reject actions.** Test every decision path.
34. **Port Dreams timeline and detail.** Preserve stable identifiers and empty/error states.
35. **Port Persona state and recent events.** Keep Persona configuration separate for the Models phase.
36. **Port Portrait state views and maintenance trigger.** Read and execute behavior in one bounded slice.
37. **Port Portrait item mutations.** Add/update/delete as distinct tested actions.
38. **Port stable Portrait update, lock, rollback, and reset.** Require explicit destructive confirmations where applicable.
39. **Port Profile Facts read/update/delete.** Preserve evidence and metadata fields.
40. **Port profile-fact proposal generation and confirmation.** Keep proposals separate from stable facts.
41. **Port anchor proposal generation and confirmation.** Integrate navigation with P0 anchors without merging semantics.
42. **Port Word Map Lite and cards.** Preserve node/edge/private-alias/boundary views.
43. **Port word-map rebuild.** Keep the mutation explicitly triggered.
44. **Port identity semantics and rebuild.** Keep this distinct from display-name settings.
45. **Port moment diagnostics.** Attach them to bucket details through a tested extension point.
46. **Port recall and diffusion diagnostics.** Preserve query inputs, candidates, seeds, hits, paths, and warnings.
47. **Port Gateway injection inspection.** Keep sensitive payload handling and empty states intact.

### Phase 6 — Consolidate Models, Configuration, and Data Without Collapsing Functions

48. **Create the Models and Data workspace and canonical-editor registry.** No editor moves in this commit.
49. **Move upstream Gateway model settings to their canonical panel.** Replace duplicate entry points with links.
50. **Move dehydration/tagging settings.** Preserve test actions and validation.
51. **Move Embedding configuration and maintenance.** Preserve provider, migration, backfill, local model, install, pull, and status functions as separate controls.
52. **Move Reranker configuration.** Preserve its independent enablement and test behavior.
53. **Move Persona model settings.** Keep state inspection in Memory and model editing here.
54. **Move Dream model settings.** Keep dream history in Memory.
55. **Move relationship-memory and daily Portrait settings.** Keep their execution/state panels separate.
56. **Move memory-surfacing settings.** Preserve effective-value inspection and validation.
57. **Move export and restore functions without conflating them.** Preserve simple compatibility ZIP export as its own action, and preserve verified full-vault export, checksum/manifest UX, and restore modes as separate actions.
58. **Move GitHub backup/sync controls.** Keep them separate from local full-vault backup.
59. **Point the shared Import navigation to its single canonical panel.** Remove remaining duplicate Import markup.
60. **Keep deployment migration tools as a separate Data panel.** Do not merge them with memory import or restore.

### Phase 7 — Finish P0/System Modularity

61. **Extract Plans without behavior changes.** Preserve its distinct no-trigger-time semantics.
62. **Extract Letters without behavior changes.** Preserve write/read/delete flows.
63. **Extract existing Anchors without behavior changes.** Link to proposal review without merging the models.
64. **Extract Logs and structured errors.** Preserve filters, clearing, and error-level semantics.
65. **Extract identity and authentication settings.** Preserve Amy/Aki fallback and validation behavior.
66. **Extract runtime, onboarding, environment, MCP, transport, and tunnel settings.** Preserve first-run onboarding status and its embedding-configuration warning; keep each panel separate.
67. **Extract diagnostics and repair actions.** Preserve explicit scopes and confirmations.
68. **Extract version, update, restart, and developer controls.** Keep high-risk actions visibly separated.
69. **Extract replay/debug and About panels.** Preserve feature-flag behavior.

### Phase 8 — Compatibility Cutover and Removal of Repetition

70. **Make `/memory-dashboard` serve the unified shell in Memory mode.** Keep the old standalone page available only behind a temporary development rollback switch for this phase.
71. **Align `/dashboard` with the unified-shell destination.** Preserve legacy bookmarks.
72. **Run the parity manifest against the unified shell and close every missing mapping.** No deletions yet.
73. **Remove duplicate shared code from the legacy Memory page.** Keep its thin wrapper until browser parity passes.
74. **Replace the legacy Memory page with a minimal compatibility wrapper or the canonical shell response.** Ensure it cannot drift into a second app again.
75. **Remove obsolete cross-Dashboard links and labels.** Navigation now refers to workspaces, not two websites.
76. **Update fingerprints, packaging manifests, deployment docs, and the upstream parity ledger.** Record every superseded UI and any justified exclusion.

### Phase 9 — Verification, Staging, and Production (in progress)

77. **Run complete local verification and independent reviews.** Include frontend, Python, security, and accessibility review. Completed with the exact evidence recorded above; staging and production remain pending.
78. **Build the exact Docker image and deploy it to isolated staging with copied data.** Validate SQLite copies and backup manifests first.
79. **Execute the full browser parity matrix on staging.** Test every workspace, both compatibility URLs, mobile layout, console output, and wrong-prefix requests.
80. **Exercise Gateway, MCP, Persona, Claude, Gemini, relay, and reranker alignment on staging.** The UI refactor must not disturb runtime routing.
81. **Create and verify the production pre-cutover backup.** Preserve rollback containers and image identifiers.
82. **Deploy the exact tested commit/image to production.** Preserve mounts, ports, secrets, and restart policy.
83. **Run public authenticated and unauthenticated smoke tests plus the production-alignment checker.** Verify identity, buckets, every workspace registration, and zero restart loops.
84. **Keep the pre-cutover rollback assets through the observation period.** Retire them only on Amy's explicit instruction.

---

## Rollback Strategy

### During Development

- Every extraction commit preserves the old presentation and can be reverted independently.
- New shell routing stays behind the existing entry routes until parity is complete.
- No data migration means frontend rollback does not require data rollback.

### During Staging

- Keep the last known-good P0luz image and the old standalone Memory page asset.
- If a Memory feature fails parity, revert only its module or switch the staging entry route back while keeping the backend unchanged.

### During Production

- Use only rollback images from the P0luz modular lineage.
- Retain the verified pre-cutover vault and cold filesystem snapshot.
- Retain stopped previous containers until the observation window closes.
- Never roll back to the historical root runtime.

---

## Acceptance Criteria

### Product

- [x] Users see one coherent Dashboard and navigation system.
- [x] Shared features appear once.
- [x] Memory, Models and Data, and System functions remain clearly separated.
- [x] Every Ying/current feature in the parity manifest has a unified-shell destination.
- [x] Every existing P0/System feature has a unified-shell destination.
- [x] The UI no longer requires switching to a second Dashboard application.

### Compatibility

- [x] `/`, `/dashboard`, and `/memory-dashboard` are implemented as compatible entry points.
- [x] Deep-link and browser-history handling is implemented.
- [x] The legacy `/#letters` bookmark and recorded legacy tab aliases map to canonical workspace/panel destinations.
- [x] Existing API methods, paths, payloads, and response contracts remain represented by the compatibility layer.
- [x] The implementation does not migrate stored data or identity semantics.
- [x] Mount-prefix-aware asset and API resolution remains implemented.

### De-duplication

- [x] One auth/session implementation.
- [x] One canonical bucket store and renderer.
- [x] One Search implementation.
- [x] One base Breath implementation.
- [x] One Network implementation.
- [x] One Import workflow and poller.
- [x] One configuration client and one canonical editor per resource.
- [x] `memory-dashboard.html` is no longer a second full application.

### Quality and Safety

- [x] Full pytest suite passes.
- [x] Ruff passes.
- [x] All changed Python files have zero Pyright errors.
- [ ] Extracted JavaScript parses and its contract/E2E tests pass.
- [x] Docker build passes.
- [x] Specialist frontend, Python, security, and accessibility reviews have no unresolved high-risk findings.
- [ ] Staging browser parity passes for every manifest family.
- [ ] First-run onboarding warnings and both simple and verified export functions pass parity checks.
- [ ] Production alignment passes.
- [ ] Backup checksums and rollback assets are verified.

---

## Out of Scope

- Replacing the P0luz modular backend with Ying's historical root runtime.
- Activating the dormant v3/distributed/Rust production stack.
- Changing memory algorithms merely because their controls move in the UI.
- Renaming or merging Plans, Reminders, Anchors, Anchor Proposals, Profile Facts, Identity Settings, Persona, or Portrait concepts.
- Changing persisted schemas or rewriting live buckets.
- Redesigning Gateway model routing, MCP behavior, Persona processing, or provider selection.
- Adding a frontend framework or production dependency without separate approval.
- Deleting rollback containers or backups during this refactor.

## Remaining Delivery Gate

The implementation slices are complete. Remaining work is the staging and production portion of Phase 9 only:

- the final local suite, lint/type, diff, and exact-image checks are already complete;
- run copied-data browser and route/API parity in isolated staging;
- deploy only the exact verified image with rollback assets retained;
- run public authenticated/unauthenticated smoke and production-alignment checks.

Until those gates finish, this plan records implemented code but does not claim
staging or production completion.
