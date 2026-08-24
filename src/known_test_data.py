"""Closed identities for historical test artifacts eligible for erasure."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class LegacyTestBucketIdentity:
    marker: str
    content_sha256: str
    metadata_sha256: str


VERIFIED_LEGACY_TEST_BUCKETS = {
    "9eded7951adf": LegacyTestBucketIdentity(
        marker="CODEX_SMOKE_20260718020107_4fg3hi_GROW",
        content_sha256="fce982eb45a1d16086e9b981486cd2e42b09d4137f79a1a1a718efd9ea197b24",
        metadata_sha256="38f898978b3e54e55c0e1f03054d4154add11685110b5cc4162ce4bc9c95bae0",
    ),
    "50659384861b": LegacyTestBucketIdentity(
        marker="CODEX_SMOKE_20260718020107_4fg3hi_PLAN",
        content_sha256="b61a0848d46357cede8e9411c82eeb5ce5da8259373fa75dd0ea64ea962ce6eb",
        metadata_sha256="261a57ebf3206558c82f739d9e5dcb07fcefa4efc464500bf4ed1b45f331ebfd",
    ),
    "9d9a75361142": LegacyTestBucketIdentity(
        marker="CODEX_SMOKE_20260718020107_4fg3hi_LETTER",
        content_sha256="37e3a3c15e3f3728451e5df4422ceaea288c6767d1876b3d0fa305331515ff84",
        metadata_sha256="e7ebed9f122ddac2c9999ec73c469f98c71e2532456ce2615753dd0a6ac12a4a",
    ),
    "7a52ee7090ab": LegacyTestBucketIdentity(
        marker="CODEX_SMOKE_20260718020107_4fg3hi_PROFILE",
        content_sha256="42d9fbdfde6d6d4ab83d9a6b067db3ed7f3960548b6046830d463b2c32c2a9ff",
        metadata_sha256="59b0a71a571b3ad517022fd2bb5f0c1cc3c4d18b3caa2e18ad87e7990a45320a",
    ),
}


def _metadata_hash(metadata: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(metadata),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def legacy_test_bucket_identity_error(
    bucket_id: str,
    metadata: Mapping[str, Any],
    content: str,
) -> str | None:
    identity = VERIFIED_LEGACY_TEST_BUCKETS.get(str(bucket_id or ""))
    if identity is None:
        return "not_verified_legacy_test_data"
    if str(metadata.get("id") or "") != bucket_id:
        return "legacy_test_bucket_id_mismatch"
    content_hash = hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()
    if content_hash != identity.content_sha256:
        return "legacy_test_content_mismatch"
    if _metadata_hash(metadata) != identity.metadata_sha256:
        return "legacy_test_metadata_mismatch"
    return None
