# Dual-Upstream Integration

This deployment keeps the Yinglianchun root runtime as its executable base and
selectively carries compatible features from P0luz's second implementation.

## Upstreams

- `origin`: `Yinglianchun/Ombre-Brain`, current root-runtime base.
- `p0luz`: `P0luz/Ombre-Brain`, second implementation and feature source.
- P0 compatibility baseline: `v2.7.6` / `6da5158`.

A literal merge of that P0 baseline was tested in an isolated worktree. It
changed 555 files, added 520 files, and produced 22 conflicts because P0 moved
the runtime from repository-root modules into `src/`. Resolving those conflicts
by keeping both sides would install two runtime implementations. Resolving them
with either side would discard production behavior from the other.

## Integrated Features

The following P0 features are adapted at the current runtime's existing
boundaries and covered by local regression tests:

- atomic YAML updates and cross-loop/process bucket serialization
- durable embedding outbox and query embedding LRU cache
- dehydration cache identity and perspective-preservation guards
- verified memory-vault backup/restore with manifest hashing and persistent media; deployment runtime state remains covered by the VPS backup procedure
- redacted effective-config diagnostics
- persistent media on `hold` and `trace`
- isolated permanent letters through `letter_write` / `letter_read`
- `breath_search` / `breath_advanced` compatibility aliases

## Deliberately Not Merged

These P0 subsystems are an architecture migration rather than a compatible
feature port and are not part of the single-VPS production runtime:

- the parallel `src/ombrebrain` vNext application/kernel
- Rust kernel, event-sourced projections, Raft, and distributed fabric
- multi-owner deployment and P0's Cloudflare tunnel manager
- complete dashboard/runtime replacement

## Updating

Refresh both histories, then inspect only commits after the recorded baseline:

```powershell
git fetch origin --prune
git fetch p0luz --prune
git log --oneline 6da5158..p0luz/main
git diff --stat 6da5158..p0luz/main
```

Port compatible changes locally with regression tests, push to `fork`, deploy
the same commit to the VPS, and run `python scripts\check_production_alignment.py`.
Advance the baseline in this file only after those checks pass.
