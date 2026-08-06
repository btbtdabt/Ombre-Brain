"""Persistent letters kept outside ordinary memory surfacing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from identity import identity_names
from stored_data import stored_data_marker
from utils import normalize_memory_title, strip_wikilinks


class LetterService:
    """Store and read permanent user/AI letters using bucket storage."""

    def __init__(self, config: dict, bucket_mgr: Any, embedding_engine: Any) -> None:
        self.config = config
        self.bucket_mgr = bucket_mgr
        self.embedding_engine = embedding_engine

    def _ai_name(self, explicit: str = "") -> str:
        return str(explicit or "").strip() or identity_names(self.config)["ai_name"]

    def _content_error(self, content: str) -> str:
        limits = self.config.get("limits", {})
        cap = int(limits.get("max_bucket_bytes") or 50 * 1024)
        size = len(content.encode("utf-8"))
        if cap > 0 and size > cap:
            return f"信件内容过大（{size / 1024:.1f} KB > 上限 {cap / 1024:.0f} KB）。"
        return ""

    def normalize_author(self, author: str, ai_name: str = "") -> str:
        """Normalize user/AI aliases against the configured identity."""

        raw_author = str(author or "").strip()
        ai = self._ai_name(ai_name)
        low = raw_author.lower()
        if low == "user":
            return "user"
        if low in {"ai", "claude"} or raw_author == ai:
            return ai
        return raw_author

    async def write(
        self,
        author: str,
        content: str,
        user_name: str = "",
        title: str = "",
        date: str = "",
        ai_name: str = "",
    ) -> str:
        try:
            bucket_id, normalized_author = await self.create(
                author=author,
                content=content,
                user_name=user_name,
                title=title,
                date=date,
                ai_name=ai_name,
                event_actor="llm",
            )
        except ValueError as exc:
            return str(exc)
        return f"💌letter→{bucket_id} [{normalized_author}]"

    async def create(
        self,
        author: str,
        content: str,
        user_name: str = "",
        title: str = "",
        date: str = "",
        ai_name: str = "",
        *,
        event_actor: str = "llm",
    ) -> tuple[str, str]:
        """Create one canonical letter and return its ID and stored author."""

        raw_author = str(author or "").strip()
        body = str(content or "").strip()
        if not raw_author:
            raise ValueError("author 不能为空。")
        if not body:
            raise ValueError("信件内容不能为空。")
        if error := self._content_error(body):
            raise ValueError(error)

        normalized_author = self.normalize_author(raw_author, ai_name)

        clean_title = normalize_memory_title(title)
        clean_date = str(date or "").strip()
        clean_user_name = str(user_name or "").strip()
        extra_metadata = {
            "author": normalized_author,
            "source_tool": "letter",
        }
        if clean_user_name:
            extra_metadata["user_name"] = clean_user_name
        if clean_date:
            extra_metadata["letter_date"] = clean_date

        bucket_id = await self.bucket_mgr.create(
            content=body,
            tags=["__letter__"],
            importance=10,
            domain=["letter"],
            valence=0.5,
            arousal=0.3,
            name=clean_title[:60] or f"{normalized_author}_{clean_date or 'letter'}",
            title=clean_title,
            bucket_type="letter",
            source="letter",
            source_tool="letter",
            event_actor=event_actor,
            extra_metadata=extra_metadata,
        )
        return bucket_id, normalized_author

    async def read(
        self,
        query: str = "",
        limit: int = 10,
        author: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> str:
        query_text = str(query or "").strip()
        if len(query_text.encode("utf-8")) > 16 * 1024:
            return "查询内容过长。"
        try:
            safe_limit = max(1, min(50, int(limit)))
        except (TypeError, ValueError, OverflowError):
            safe_limit = 10

        try:
            list_letters = getattr(self.bucket_mgr, "list_letters", None)
            if callable(list_letters):
                list_letters_fn = cast(
                    Callable[[], Awaitable[list[dict[str, Any]]]],
                    list_letters,
                )
                letters = await list_letters_fn()
            else:
                letters = [
                    item
                    for item in await self.bucket_mgr.list_all(include_archive=False)
                    if str((item.get("metadata") or {}).get("type") or "").lower()
                    == "letter"
                ]
        except Exception as exc:
            return f"读取信件失败: {exc}"

        author_filter = str(author or "").strip()
        if author_filter:
            normalized_author = self.normalize_author(author_filter)
            ai = self.normalize_author("ai")
            if normalized_author == "user":
                letters = [
                    item for item in letters
                    if item["metadata"].get("author") == "user"
                ]
            elif normalized_author == ai:
                aliases = {ai, "claude"}
                letters = [
                    item for item in letters
                    if item["metadata"].get("author") in aliases
                ]
            else:
                letters = [
                    item for item in letters
                    if item["metadata"].get("author") == normalized_author
                ]

        def within_date(item: dict) -> bool:
            metadata = item["metadata"]
            value = str(metadata.get("letter_date") or metadata.get("created") or "")
            if date_from and value and value < date_from:
                return False
            if date_to and value and value > date_to:
                return False
            return True

        letters = [item for item in letters if within_date(item)]

        def matches_query(item: dict) -> bool:
            if not query_text:
                return True
            metadata = item.get("metadata", {})
            fields = [
                item.get("content", ""),
                str(metadata.get("name") or ""),
                str(metadata.get("title") or ""),
                str(metadata.get("author") or ""),
                *(str(tag) for tag in (metadata.get("tags") or [])),
            ]
            return query_text.lower() in "\n".join(fields).lower()

        vector_scores: dict[str, float] = {}
        if query_text and self.embedding_engine and getattr(self.embedding_engine, "enabled", False):
            try:
                vector_scores = dict(
                    await self.embedding_engine.search_similar(
                        query_text, top_k=safe_limit * 3
                    )
                )
            except Exception:
                vector_scores = {}

        vector_matches = (
            [item for item in letters if item["id"] in vector_scores]
            if vector_scores
            else []
        )
        if vector_matches:
            letters = vector_matches
            letters.sort(key=lambda item: vector_scores.get(item["id"], 0.0), reverse=True)
        else:
            letters = [item for item in letters if matches_query(item)]
            letters.sort(
                key=lambda item: str(
                    item["metadata"].get("letter_date")
                    or item["metadata"].get("created")
                    or ""
                ),
                reverse=True,
            )

        if not letters:
            return "没有找到匹配的信件。"
        parts = []
        for item in letters[:safe_limit]:
            metadata = item["metadata"]
            stored_author = metadata.get("author", "?")
            stored_date = str(
                metadata.get("letter_date") or metadata.get("created") or ""
            )[:10]
            stored_title = metadata.get("title") or metadata.get("name", "")
            title_suffix = f" · {stored_title}" if stored_title else ""
            payload = (
                f"[{item['id']}] {stored_author} · {stored_date}{title_suffix}\n"
                + strip_wikilinks(item["content"])
            )
            parts.append(
                stored_data_marker(payload, provenance=f"letter:{item['id']}")
                + "\n"
                + payload
            )
        return "=== 信件 ===\n" + "\n\n---\n\n".join(parts)
