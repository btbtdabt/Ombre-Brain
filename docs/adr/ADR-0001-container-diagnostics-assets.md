# ADR-0001: Package read-only diagnostics assets in container releases

## Decision

Container releases include the vNext preflight CLI, the minimal Rust replay-kernel
scaffold, and this ADR directory as read-only image assets. A runtime using the
persistent `src/` tree may inspect those assets through `OMBRE_IMAGE_ROOT`; it does
not copy them into the writable memory volume.

## Why this is not cognition

The packaged files describe and validate software boundaries. They do not infer,
store, or alter thoughts, identity, affect, preferences, or decisions.

## Why this is not a database feature

The assets are ordinary release files baked into the image. They do not add a
database, become canonical memory storage, or change projection ownership.

## How forgetting still works

Memory decay, resolution, digestion, archive behavior, and retrieval policy are
unchanged. Diagnostics only report whether release contracts are present.

## How tombstones are preserved

The change does not write to the ledger or bucket store. Existing tombstones and
their replay semantics remain untouched.

## How present thinking remains with the LLM

The preflight reports static implementation facts. They have no instructional
force and cannot replace current model reasoning.

## Rejected alternatives

Copying diagnostics assets into the persistent runtime tree was rejected because
it would mix immutable release evidence with writable hot-update state. Disabling
the diagnostics in containers was rejected because it would hide packaging drift.

## Tests required

Tests must verify the Docker build context allowlist, Dockerfile copy directives,
image-root fallback, and unchanged behavior when a complete repository root is
available.
