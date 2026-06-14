# Ombre-Brain Codex Instructions

These instructions are project-local for `C:\Users\Amy98\Projects\Ombre-Brain`.

## Core Rule: Local First, Then Align

- Treat the local repo as the editing source of truth.
- Make code, docs, scripts, examples, and prompt changes locally first.
- Commit local tracked changes, then push them to Amy's fork remote: `fork`.
- Deploy or sync the matching change to the VPS after local verification.
- Do not make a VPS-only code/config change unless it is an emergency hotfix. If that happens, immediately copy the same change back to local and commit any tracked equivalent.
- A task is not complete until local, GitHub, VPS, and production behavior are aligned or the remaining gap is explicitly reported.

## Git Remotes

- `fork` is Amy's working GitHub fork and the normal push target.
- `origin` is the upstream author repo. Fetch/compare from it; do not push there unless Amy explicitly asks.
- Before edits, check:

```powershell
git status -sb
git remote -v
```

- After tracked changes, commit and push:

```powershell
git status -sb
git add <files>
git commit -m "<message>"
git push fork main
```

## Runtime Files And Secrets

These files are intentionally gitignored and may contain secrets or deployment-local values:

- `.env`
- `config.yaml`
- `cloudflare.env`
- `buckets/`
- `state/`

Rules:

- Never print real secret values.
- Never commit secrets.
- It is acceptable to report `set/missing`, length, hash, or match/mismatch.
- Keep local `.env` and VPS `/opt/Ombre-Brain/.env` aligned for the Gateway keys.
- Keep local `config.yaml` and VPS `/srv/ombre-brain/config.yaml` aligned unless there is a deliberate documented reason.

Canonical production details are in:

```text
docs/production-alignment.md
```

## VPS Access

Use the SSH alias:

```powershell
ssh ombre-vps
```

Common checks:

```powershell
ssh ombre-vps "hostname; whoami; pwd"
ssh ombre-vps "docker ps --format '{{.Names}} {{.Status}}'"
```

Compare local/VPS `config.yaml` without exposing contents:

```powershell
$local=(Get-FileHash -Algorithm SHA256 .\config.yaml).Hash
$remote=(ssh ombre-vps "sha256sum /srv/ombre-brain/config.yaml | awk '{print `$1}'").Trim().ToUpper()
[pscustomobject]@{local=$local; vps=$remote; match=($local -eq $remote)} | ConvertTo-Json
```

Sync runtime config only after local is correct:

```powershell
scp .\config.yaml ombre-vps:/srv/ombre-brain/config.yaml
ssh ombre-vps "docker restart ombre-gateway ombre-brain"
```

If tracked source code changed, verify the VPS repo state before pulling or rebuilding:

```powershell
ssh ombre-vps "cd /srv/ombre-brain && git status -sb && git remote -v"
```

Then use the existing VPS deployment pattern for this repo. Do not overwrite user data directories such as `buckets/` or `state/`.

## Required Production Alignment Check

Run this after any change involving config, env, Gateway routing, API models, MCP tools, relay integration, VPS deployment, or Cloudflare secrets:

```powershell
python scripts\check_production_alignment.py
```

The check is read-only and does not print secrets. The deployment is not trustworthy unless it ends with:

```text
Alignment check passed.
```

If the check fails, fix the failed line before calling the task done.

## Gateway And Relay Contract

Normal final replies:

- Base URL: `https://gateway.btombre.men/v1`
- Token: `OMBRE_GATEWAY_TOKEN`
- Default model: `claude-opus-4-8`

Gemini coordinator/native route:

- Base URL: `https://gateway.btombre.men/v1beta`
- Token: `OMBRE_GATEWAY_GEMINI_TOKEN`
- Model: `gemini-3.5-flash`

Expected Gateway models:

- `claude-opus-4-6`
- `claude-opus-4-8`
- `claude-fable-5`
- `gemini-3.5-flash`

Related relay repo:

```text
C:\Users\Amy98\Projects\nuojiji-relay
```

Relay Cloudflare secrets must stay consistent with `docs/production-alignment.md`. Use `wrangler secret put` for secret updates and never echo token values into logs.

## Tooling Tips

- Use `rg` / `rg --files` for searching.
- Use `multi_tool_use.parallel` for independent reads/checks.
- Use `apply_patch` for manual file edits.
- Use PowerShell-native commands on Windows.
- Use `ssh ombre-vps` and `scp ... ombre-vps:...` for VPS work.
- Prefer the repo's debug/alignment tooling over ad hoc log tailing when checking Gateway/relay behavior.
- For production behavior, verify with `scripts\check_production_alignment.py` before trusting a manual API test.
- When comparing secrets or runtime files, output only booleans, lengths, hashes, or key names.

## Prompt And Memory Changes

- Treat prompt changes as behavior changes. Keep them small, reviewable, and committed.
- Before changing Gateway/MCP/persona/relay prompts, inspect the exact prompt path and recent debug output.
- Do not remove time anchors, current-message anchors, or tool-result context unless the exact pollution path has been proven.
- Do not add broad "do not" prompt rules as a quick fix if a narrower positive instruction or parser fix would solve the root cause.

## Finish Checklist

Before final response:

- `git status -sb` is clean or any remaining files are explained.
- Relevant tests or checks ran.
- Runtime config/env changes are mirrored local <-> VPS.
- GitHub contains tracked changes.
- VPS/Cloudflare production behavior was checked when relevant.
- The user is told exactly what changed and what verification passed.
