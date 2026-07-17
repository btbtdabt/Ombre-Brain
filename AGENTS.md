# Ombre-Brain Codex Instructions

These instructions apply to `C:\Users\Amy98\Projects\Ombre-Brain`.

## Runtime Architecture

- P0luz is the primary implementation base. The shipped single-node runtime lives in `src/`, with `src/server.py` as the Brain entrypoint, `src/tools/` as the MCP tool layer, and `src/web/` as the HTTP/Dashboard layer.
- Keep `src/ombrebrain/`, the Rust kernel, Raft, distributed fabric, and event-sourced v3 work out of the production boot path unless a separate migration explicitly activates them.
- Do not reintroduce the historical repository-root `server.py` runtime. Port useful behavior into the modular `src/` runtime.
- The production Gateway is a separate process and belongs under `src/gateway.py` plus focused supporting modules.

## Upstreams

- `p0luz` (`P0luz/Ombre-Brain`) is the primary upstream and normal merge source.
- `origin` (`Yinglianchun/Ombre-Brain`) is a secondary feature source. Fetch and compare it, then port applicable behavior into the P0luz architecture with tests. Do not merge its root runtime wholesale.
- `fork` (`btbtdabt/Ombre-Brain`) is Amy's writable GitHub repository and the normal push target.
- The default is to preserve every user-visible and production-runtime feature from the current fork and both upstreams. A feature may be excluded only when it is demonstrably obsolete, superseded by a compatible implementation, part of the intentionally dormant v3/distributed stack, or incompatible with stored-data/production contracts. Record every exclusion and its regression evidence in `docs/upstream-integration.md`.
- When implementations conflict, first preserve both behaviors behind one shared interface or an explicit configuration choice. If they are genuinely mutually exclusive, keep the P0luz modular architecture and the documented production/data contract, then port the more complete behavior into that boundary.

Future update procedure:

```powershell
git fetch p0luz --prune
git fetch origin --prune
git log --oneline <recorded-p0-baseline>..p0luz/main
git log --oneline <recorded-origin-baseline>..origin/main
```

Merge tested P0luz updates into the local branch. Review every Yinglianchun commit after the recorded baseline and port its behavior at the corresponding `src/tools`, `src/web`, engine, or Gateway seam. Add or update a parity test for each retained feature. Advance either recorded baseline in `docs/upstream-integration.md` only after the complete integration batch passes verification.

## Local First, Then Align

- Treat the local repository as the editing source of truth.
- Make code, prompt, config-template, documentation, and deployment changes locally first.
- Verify locally, commit, and push to `fork` before deployment.
- Deploy the exact tested commit to staging before production cutover.
- Do not make VPS-only tracked changes. If an emergency hotfix is unavoidable, immediately reproduce it locally and commit it.
- A production task is complete only when local, GitHub, VPS/Cloudflare, and observed behavior are aligned, or the remaining gap is explicitly reported.

## Migration Safety

- The historical root runtime remains the rollback implementation until P0luz-base parity is proven.
- Never point migration tests at live `buckets/`, `state/`, SQLite databases, or config files. Test with copies in temporary directories.
- Treat Markdown buckets as source data. Embeddings and projections are derived indexes and may be rebuilt only after a verified backup.
- Validate SQLite copies with `PRAGMA quick_check` and validate backup manifests/checksums before import.
- Preserve existing bucket frontmatter, raw-event history, Gateway state, persona/portrait state, and identity semantics unless a tested translator explicitly changes them.

## Production Contract

Normal final replies:

- Base URL: `https://gateway.btombre.men/v1`
- Relay API type: Anthropic Messages
- Default model: `claude-opus-4-8-native`
- OpenAI-compatible fallback model: `claude-opus-4-8`

Gemini native route:

- Base URL: `https://gateway.btombre.men/v1beta`
- Model: `gemini-3.5-flash`

The Gateway must preserve token-based Claude/Gemini routing, native Anthropic tool use, OpenAI compatibility, prompt/debug tracing, memory injection, and final-turn persona processing.

## Runtime Files And Secrets

The following are deployment-local and must remain untracked:

- `.env`
- `config.yaml`
- `cloudflare.env`
- `buckets/`
- `state/`
- SQLite databases and logs

Never print or commit secrets. Secret comparisons may report only key names, set/missing status, length, hashes, or match/mismatch.

## VPS And Deployment

Use the SSH alias:

```powershell
ssh ombre-vps
```

Production currently runs the historical root runtime. Do not replace it until the P0luz-base staging deployment passes the full parity checklist. Retain the old containers/images and data snapshot for rollback during cutover.

After Gateway, config, MCP, relay, VPS, or Cloudflare changes, run the production-alignment checker once it has been ported to the new layout:

```powershell
python scripts\check_production_alignment.py
```

## Engineering Rules

- Explore before editing when behavior or ownership is unclear.
- Use `rg` / `rg --files` for search and `apply_patch` for manual edits.
- Treat prompts as behavior changes; inspect the exact injection path and debug evidence before editing them.
- Do not remove time anchors, current-message anchors, or tool-result context without proving the pollution path.
- Prefer affirmative, narrow prompt instructions over broad lists of prohibitions.
- Add or port regression tests before changing behavior.
- Keep P0luz's module separation: tool logic in `src/tools`, web/API logic in `src/web`, reusable state/engines in focused `src/*.py` modules, and process assembly in entrypoints.

## Required Verification

For a migration batch, run as applicable:

```powershell
python -m pytest --asyncio-mode=auto
python -m ruff check .
pyright
docker build .
```

Also review the diff, validate copied production data, and exercise Gateway/MCP/persona/relay flows on staging. Do not call a migration complete with failing tests, an unexplained lint/type regression, or unverified production data.

## Finish Checklist

- `git status -sb` is clean or every remaining file is explained.
- P0luz's full applicable suite and all compatibility tests pass.
- Current/Yinglianchun feature parity is recorded in `docs/upstream-integration.md`.
- Runtime config and secrets are aligned without exposing values.
- The exact Git commit is deployed to staging and then production.
- Rollback remains available until post-cutover checks pass.
