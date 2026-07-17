# Upstream Integration Policy

This repository uses the P0luz implementation as its executable base and keeps
Yinglianchun's repository as a secondary source of product behavior.

## Recorded Baselines

- P0luz primary baseline: `v2.7.6` / `6da5158`
- Yinglianchun secondary baseline: `4756c26`
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

- [ ] Native Anthropic and OpenAI-compatible Gateway routes
- [ ] Token-selected Claude and Gemini upstream routing
- [ ] Gateway memory injection, state tracking, and debug traces
- [ ] Current recall graph, relevance policy, moments, layers, and diffusion
- [ ] Raw events and source-reference continuity
- [ ] Persona, portrait, reflection, dream, and reminder workers
- [ ] Current MCP tool set and affirmative tool descriptions
- [ ] Darkroom, letters, media, backup, and import behavior
- [ ] Current Dashboard memory/config/debug surfaces
- [ ] Production alignment, Cloudflare, relay, and VPS deployment contract
- [ ] Read-only compatibility with copied production buckets and state

Each item is complete only when its compatibility tests pass and the staging
runtime demonstrates the same externally observable behavior.

## Future Updates

1. Fetch both upstreams.
2. Merge P0luz changes into a local update branch and run its full suite.
3. Review Yinglianchun commits after the recorded baseline.
4. Port applicable Yinglianchun behavior at the matching P0luz module seam.
5. Run compatibility tests, copied-data validation, lint, types, and container
   build checks.
6. Advance the baseline hashes in this document only after staging succeeds.

Do not resolve upstream conflicts by restoring the repository-root runtime or by
silently dropping one implementation's feature. Record intentional exclusions
with their reason and regression evidence.
