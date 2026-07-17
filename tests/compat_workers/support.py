from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter

from bucket_manager import BucketManager


class LegacyBucketManager(BucketManager):
    """Adapt historical fixture arguments to the P0 storage boundary."""

    async def create(
        self,
        content: str,
        *,
        bucket_id: str = "",
        created: str = "",
        updated_at: str = "",
        last_active: str = "",
        extra_metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        actual_id = await super().create(
            content,
            bucket_id_override=bucket_id,
            **kwargs,
        )
        metadata = dict(extra_metadata or {})
        if created:
            metadata["created"] = created
        if updated_at:
            metadata["updated_at"] = updated_at
        if last_active:
            metadata["last_active"] = last_active
        if metadata:
            path = self._find_bucket_file(actual_id)
            assert path is not None
            post = frontmatter.load(path)
            post.metadata.update(metadata)
            Path(path).write_text(frontmatter.dumps(post), encoding="utf-8")
            self._invalidate_bm25()
        return actual_id
