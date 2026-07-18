"""Current-runtime adapter for verified Ombre Brain backup archives."""

from __future__ import annotations

import inspect
import os
from contextlib import AsyncExitStack
from pathlib import Path
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any, cast

import frontmatter

from backup_archive import (
    BackupArchiveError,
    build_export_archive_file,
    extract_backup_archive_file,
    validate_sqlite_file,
)
from media_store import media_bucket_directory_name
from utils import atomic_write_text, now_iso, same_path


class VaultBackupManager:
    """Export and restore the current Markdown vault without exporting secrets."""

    def __init__(self, config: dict, bucket_mgr: Any, embedding_engine: Any) -> None:
        self.config = config
        self.bucket_mgr = bucket_mgr
        self.embedding_engine = embedding_engine
        self.buckets_dir = Path(config["buckets_dir"]).resolve()
        configured_media = getattr(getattr(bucket_mgr, "media_store", None), "media_dir", None)
        self.media_dir = Path(
            configured_media or config.get("media_dir") or self.buckets_dir / "_media"
        ).resolve()

    def create_archive(self) -> tuple[str, dict[str, Any]]:
        args = (
            str(self.buckets_dir),
            str(getattr(self.embedding_engine, "db_path", "") or ""),
            {
                "exported_at": now_iso(),
                "version": str(self.config.get("version") or "current-runtime"),
                "format": "verified-local-backup",
                "scope": "memory-vault",
            },
        )
        parameters = inspect.signature(build_export_archive_file).parameters
        if "media_dir" in parameters or len(parameters) >= 4:
            return build_export_archive_file(*args, str(self.media_dir))
        if self._has_persisted_media():
            raise BackupArchiveError(
                "当前备份归档器不支持持久媒体；已拒绝生成不完整备份"
            )
        return build_export_archive_file(*args)

    def _has_persisted_media(self) -> bool:
        if not self.media_dir.exists():
            return False
        return any(
            path.is_file() or path.is_symlink() for path in self.media_dir.rglob("*")
        )

    async def restore_archive(self, archive_path: str, *, mode: str = "skip") -> dict[str, Any]:
        mode = str(mode or "skip").strip().lower()
        if mode not in {"skip", "overwrite"}:
            raise ValueError("restore mode must be 'skip' or 'overwrite'")

        staging_dir = tempfile.mkdtemp(prefix="ombre-restore-")
        rollback_dir = Path(tempfile.mkdtemp(prefix="ombre-rollback-"))
        operations: list[dict[str, Any]] = []
        try:
            parsed = extract_backup_archive_file(archive_path, staging_dir)
            if not parsed.get("integrity_verified"):
                raise BackupArchiveError("恢复只接受带 SHA-256 完整性清单的备份")

            candidates = self._bucket_candidates(parsed.get("files") or {})
            media_candidates = self._media_candidates(parsed.get("files") or {})
            existing_before = await self.bucket_mgr.list_all(include_archive=True)
            list_letters = getattr(self.bucket_mgr, "list_letters", None)
            if callable(list_letters):
                read_letters = cast(
                    Callable[[], Awaitable[list[dict[str, Any]]]], list_letters
                )
                existing_before.extend(await read_letters())
            bucket_ids = sorted({candidate["bucket_id"] for candidate in candidates})
            created = 0
            overwritten = 0
            skipped = 0
            restored_ids: list[str] = []

            async with AsyncExitStack() as stack:
                for bucket_id in bucket_ids:
                    await stack.enter_async_context(self.bucket_mgr._bucket_turn(bucket_id))

                for candidate in candidates:
                    bucket_id = candidate["bucket_id"]
                    source_path = candidate["source_path"]
                    target_path = candidate["target_path"]
                    if target_path.is_symlink():
                        raise BackupArchiveError(f"恢复目标不能是符号链接: {target_path}")
                    if target_path.exists():
                        target_bucket_id = self._read_bucket_id(target_path)
                        if target_bucket_id and target_bucket_id != bucket_id:
                            raise BackupArchiveError(
                                "恢复目标路径已属于其他 bucket: "
                                f"{target_bucket_id} != {bucket_id}"
                            )
                    existing_by_id = self.bucket_mgr._find_bucket_file(bucket_id)
                    existing_paths = {
                        os.path.abspath(path)
                        for path in (existing_by_id, str(target_path) if target_path.exists() else "")
                        if path
                    }
                    if existing_paths and mode == "skip":
                        skipped += 1
                        continue

                    backups = []
                    for index, existing_path in enumerate(sorted(existing_paths)):
                        rollback_path = rollback_dir / f"{len(operations):05d}-{index}.md"
                        shutil.copy2(existing_path, rollback_path)
                        backups.append((existing_path, str(rollback_path)))

                    operation = {
                        "target": str(target_path),
                        "backups": backups,
                        "target_existed": str(target_path) in existing_paths,
                    }
                    operations.append(operation)
                    atomic_write_text(target_path, candidate["serialized"])
                    for existing_path in existing_paths:
                        if not same_path(existing_path, str(target_path)):
                            os.remove(existing_path)

                    if existing_paths:
                        overwritten += 1
                    else:
                        created += 1
                    restored_ids.append(bucket_id)

            embedding_snapshot = "not_present"
            snapshot_path = (parsed.get("files") or {}).get("embeddings.db")
            if snapshot_path:
                validate_sqlite_file(snapshot_path)
                if not existing_before and skipped == 0:
                    self._replace_embedding_db(snapshot_path, rollback_dir, operations)
                    embedding_snapshot = "restored"
                else:
                    embedding_snapshot = "validated_reindex_required"
                    for bucket_id in restored_ids:
                        self.embedding_engine.delete_embedding(bucket_id)

            media_restored = 0
            media_skipped = 0
            for candidate in media_candidates:
                source_path = Path(candidate["source_path"])
                target_path = candidate["target_path"]
                if target_path.exists() and mode == "skip":
                    media_skipped += 1
                    continue
                backups = []
                if target_path.exists():
                    rollback_path = rollback_dir / f"{len(operations):05d}-media.bin"
                    shutil.copy2(target_path, rollback_path)
                    backups.append((str(target_path), str(rollback_path)))
                operations.append(
                    {
                        "target": str(target_path),
                        "backups": backups,
                        "target_existed": target_path.exists(),
                    }
                )
                self._replace_file(source_path, target_path)
                media_restored += 1

            self._invalidate_bucket_cache()
            return {
                "created": created,
                "overwritten": overwritten,
                "skipped": skipped,
                "restored_ids": restored_ids,
                "embedding_snapshot": embedding_snapshot,
                "media_restored": media_restored,
                "media_skipped": media_skipped,
                "integrity_verified": True,
                "scope": "memory-vault",
                "manifest": parsed.get("manifest"),
            }
        except Exception:
            self._rollback(operations)
            self._invalidate_bucket_cache()
            raise
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)
            shutil.rmtree(rollback_dir, ignore_errors=True)

    def _invalidate_bucket_cache(self) -> None:
        invalidate = getattr(self.bucket_mgr, "_invalidate_bm25", None)
        if callable(invalidate):
            invalidate()

    def _bucket_candidates(self, files: dict[str, str]) -> list[dict[str, Any]]:
        candidates = []
        seen_ids: set[str] = set()
        seen_targets: set[str] = set()
        archived_media = self._archived_media_relatives(files)
        for archive_path, source_path in sorted(files.items()):
            if (
                not archive_path.startswith("buckets/")
                or archive_path.startswith("buckets/_media/")
                or not archive_path.endswith(".md")
            ):
                continue
            relative = Path(*archive_path.split("/")[1:])
            target_path = self._safe_target(relative)
            try:
                text = Path(source_path).read_text(encoding="utf-8")
                post = frontmatter.loads(text)
            except Exception as exc:
                raise BackupArchiveError(f"记忆文件无法解析: {archive_path}: {exc}") from exc
            bucket_id = str(post.get("id") or "").strip()
            if not bucket_id:
                raise BackupArchiveError(f"记忆文件缺少 id: {archive_path}")
            if bucket_id in seen_ids:
                raise BackupArchiveError(f"备份包含重复 bucket id: {bucket_id}")
            seen_ids.add(bucket_id)
            target_key = os.path.normcase(os.path.abspath(target_path))
            if target_key in seen_targets:
                raise BackupArchiveError(f"备份包含重复记忆目标路径: {archive_path}")
            seen_targets.add(target_key)
            self._rewrite_media_references(post, bucket_id, archived_media)
            candidates.append(
                {
                    "bucket_id": bucket_id,
                    "source_path": str(source_path),
                    "target_path": target_path,
                    "serialized": frontmatter.dumps(post),
                }
            )
        return candidates

    def _media_candidates(self, files: dict[str, str]) -> list[dict[str, Any]]:
        candidates = []
        seen_targets: set[str] = set()
        for archive_path, source_path in sorted(files.items()):
            if archive_path.startswith("media/"):
                relative = Path(*archive_path.split("/")[1:])
            elif archive_path.startswith("buckets/_media/"):
                relative = Path(*archive_path.split("/")[2:])
            else:
                continue
            target_path = self._safe_media_target(relative)
            target_key = os.path.normcase(os.path.abspath(target_path))
            if target_key in seen_targets:
                raise BackupArchiveError(f"备份包含重复媒体目标路径: {archive_path}")
            seen_targets.add(target_key)
            candidates.append(
                {
                    "source_path": str(source_path),
                    "target_path": target_path,
                }
            )
        return candidates

    @staticmethod
    def _archived_media_relatives(files: dict[str, str]) -> set[str]:
        relatives: set[str] = set()
        for archive_path in files:
            if archive_path.startswith("media/"):
                relatives.add("/".join(archive_path.split("/")[1:]))
            elif archive_path.startswith("buckets/_media/"):
                relatives.add("/".join(archive_path.split("/")[2:]))
        return relatives

    def _rewrite_media_references(
        self,
        post: frontmatter.Post,
        bucket_id: str,
        archived_media: set[str],
    ) -> None:
        media = post.get("media")
        if not isinstance(media, list):
            return
        safe_bucket = media_bucket_directory_name(bucket_id)
        rewritten = []
        for raw_item in media:
            if not isinstance(raw_item, dict) or not raw_item.get("stored"):
                rewritten.append(raw_item)
                continue
            item = dict(raw_item)
            filename = Path(str(item.get("path") or "").replace("\\", "/")).name
            if not filename or filename in {".", ".."}:
                raise BackupArchiveError(f"bucket {bucket_id} 包含无效媒体路径")
            relative = Path(safe_bucket) / filename
            archive_relative = relative.as_posix()
            if archive_relative not in archived_media:
                raise BackupArchiveError(
                    f"bucket {bucket_id} 的持久媒体不在备份中: {filename}"
                )
            target = self._safe_media_target(relative)
            try:
                item["path"] = target.relative_to(self.buckets_dir).as_posix()
            except ValueError:
                item["path"] = str(target)
            rewritten.append(item)
        post["media"] = rewritten

    def _safe_target(self, relative: Path) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise BackupArchiveError(f"不安全的恢复路径: {relative}")
        candidate = self.buckets_dir / relative
        current = self.buckets_dir
        for part in relative.parts[:-1]:
            current = current / part
            if current.exists() and current.is_symlink():
                raise BackupArchiveError(f"恢复路径包含符号链接目录: {relative}")
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.buckets_dir):
            raise BackupArchiveError(f"恢复路径越界: {relative}")
        return candidate

    def _safe_media_target(self, relative: Path) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise BackupArchiveError(f"不安全的媒体恢复路径: {relative}")
        candidate = self.media_dir / relative
        current = self.media_dir
        for part in relative.parts[:-1]:
            current = current / part
            if current.exists() and current.is_symlink():
                raise BackupArchiveError(f"媒体恢复路径包含符号链接目录: {relative}")
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.media_dir):
            raise BackupArchiveError(f"媒体恢复路径越界: {relative}")
        return candidate

    @staticmethod
    def _read_bucket_id(path: Path) -> str:
        try:
            bucket_id = str(frontmatter.load(path).get("id") or "").strip()
        except Exception as exc:
            raise BackupArchiveError(f"恢复目标记忆文件无法解析: {path}: {exc}") from exc
        if not bucket_id:
            raise BackupArchiveError(f"恢复目标记忆文件缺少 id: {path}")
        return bucket_id

    def _replace_embedding_db(
        self,
        snapshot_path: str,
        rollback_dir: Path,
        operations: list[dict[str, Any]],
    ) -> None:
        target = Path(self.embedding_engine.db_path)
        backup_path = rollback_dir / "embeddings.db"
        existed = target.exists()
        if existed:
            shutil.copy2(target, backup_path)
        descriptor, temporary = tempfile.mkstemp(prefix=".embeddings.", suffix=".db", dir=target.parent)
        os.close(descriptor)
        try:
            shutil.copy2(snapshot_path, temporary)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        operations.append(
            {
                "target": str(target),
                "backups": [(str(target), str(backup_path))] if existed else [],
                "target_existed": existed,
            }
        )

    @staticmethod
    def _replace_file(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(descriptor)
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def _rollback(operations: list[dict[str, Any]]) -> None:
        for operation in reversed(operations):
            target = Path(operation["target"])
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            for original_path, rollback_path in operation.get("backups", []):
                try:
                    payload = Path(rollback_path).read_bytes()
                    destination = Path(original_path)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(f".{destination.name}.restore.tmp")
                    temporary.write_bytes(payload)
                    os.replace(temporary, destination)
                except OSError:
                    pass
