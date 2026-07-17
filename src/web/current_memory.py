"""Current-production memory, raw-event, reminder, and edge routes."""

from __future__ import annotations

import re
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from favorite_tags import has_favorite_memory_tag
from identity import identity_names
from memory_metadata import normalize_domain_key
from utils import local_date_key, now_iso

from tools.current._helpers import bucket_light_payload, bucket_read_payload

from .current_contract import (
    CurrentWebDependencies,
    MissingDependency,
    authorized_memory_write,
    bool_value,
    cleanup_bucket_indexes,
    dashboard_auth,
    exception_response,
    float_between,
    int_between,
    json_body_error,
    maybe_await,
    memory_write_token,
    public_reminder,
    queue_embedding,
    raw_api_auth,
    read_json_object,
    refresh_bucket_indexes,
    require_dependency,
    service_json,
    valid_memory_id,
)


def _identity(dependencies: CurrentWebDependencies) -> dict[str, str]:
    return identity_names(dict(dependencies.config))


def _favorite_reason_present(content: str) -> bool:
    return bool(
        re.search(
            r"(?im)^\s{0,3}#{2,6}\s+(?:reflection|喜欢它的原因)\s*$",
            str(content or ""),
        )
    )


def _favorite_reason_error() -> str:
    return "标记 favorite memory 需要在正文写明「### reflection」。旧的「喜欢它的原因」仍兼容。"


def _string_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = [value]
    return [str(item).strip() for item in raw if str(item).strip()]


def _unique_clean_list(value: Any, *, limit: int = 40) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = re.split(r"[,，、\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()[:64]
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
        if len(result) >= limit:
            break
    return result


def _merge_metadata_list(
    current: Any,
    *,
    add: list[str],
    remove: list[str],
) -> tuple[list[str], bool]:
    values = _unique_clean_list(current, limit=120)
    original = list(values)
    remove_set = set(remove)
    values = [item for item in values if item not in remove_set]
    values.extend(item for item in add if item not in values)
    return values, values != original


async def _delete_bucket(
    dependencies: CurrentWebDependencies,
    bucket_id: str,
) -> dict[str, str]:
    manager = require_dependency(dependencies, "bucket_mgr")
    if not valid_memory_id(bucket_id):
        return {"id": bucket_id, "status": "invalid"}
    if not await manager.get(bucket_id):
        return {"id": bucket_id, "status": "not_found"}
    if not await manager.delete(bucket_id):
        return {"id": bucket_id, "status": "failed"}
    await cleanup_bucket_indexes(dependencies, bucket_id)
    return {"id": bucket_id, "status": "deleted"}


def _embedding_enabled(dependencies: CurrentWebDependencies) -> bool:
    return bool(getattr(dependencies.embedding_engine, "enabled", False))


async def _create_memory_direct(
    dependencies: CurrentWebDependencies,
    body: dict[str, Any],
) -> dict[str, Any] | Response:
    manager = require_dependency(dependencies, "bucket_mgr")
    title = str(body.get("title") or body.get("name") or "").strip()
    content = str(body.get("content") or "").strip()
    if not title:
        return JSONResponse({"error": "missing title"}, status_code=400)
    if not content:
        return JSONResponse({"error": "missing content"}, status_code=400)

    bucket_id = str(body.get("id") or "").strip() or None
    if bucket_id and not valid_memory_id(bucket_id):
        return JSONResponse({"error": "invalid id"}, status_code=400)
    bucket_type = str(body.get("type") or "dynamic").strip()
    if bucket_type not in {"dynamic", "permanent", "feel"}:
        return JSONResponse({"error": "invalid type"}, status_code=400)

    existing = await manager.get(bucket_id) if bucket_id else None
    existing_metadata = (
        existing.get("metadata", {}) if isinstance(existing, dict) else {}
    )
    if not isinstance(existing_metadata, dict):
        existing_metadata = {}

    tags = _string_list(
        body.get("tags"),
        _string_list(existing_metadata.get("tags"), []) if existing else [],
    )
    if has_favorite_memory_tag(tags) and not _favorite_reason_present(content):
        return JSONResponse({"error": _favorite_reason_error()}, status_code=400)
    domain = _string_list(body.get("domain"), ["general"])
    now = now_iso()
    self_anchor = bool_value(body.get("self_anchor"), False)
    event_date = str(body.get("date") or body.get("event_date") or "").strip()
    common = {
        "tags": tags,
        "importance": int_between(body.get("importance"), 5, 1, 10),
        "domain": domain,
        "valence": float_between(body.get("valence"), 0.5, 0.0, 1.0),
        "arousal": float_between(body.get("arousal"), 0.3, 0.0, 1.0),
        "name": title,
        "pinned": bool_value(body.get("pinned"), False),
        "anchor": bool_value(body.get("anchor"), False),
        "resolved": bool_value(body.get("resolved"), False),
        "digested": bool_value(body.get("digested"), False),
        "confidence": float_between(body.get("confidence"), 0.5, 0.0, 1.0),
        "source": "chatgpt",
        "last_active": str(body.get("last_active") or now),
        "updated_at": str(body.get("updated_at") or now),
    }

    if existing and bucket_id:
        updates: dict[str, Any] = {
            "content": content,
            "name": title,
            "updated_at": str(body.get("updated_at") or now),
        }
        update_fields = {
            "tags": common["tags"],
            "importance": common["importance"],
            "domain": common["domain"],
            "valence": common["valence"],
            "arousal": common["arousal"],
            "pinned": common["pinned"],
            "anchor": common["anchor"],
            "resolved": common["resolved"],
            "digested": common["digested"],
            "confidence": common["confidence"],
            "last_active": common["last_active"],
        }
        for field, value in update_fields.items():
            if field in body:
                updates[field] = value
        if "self_anchor" in body:
            updates["extra_metadata"] = {"self_anchor": self_anchor}
        if event_date:
            updates["date"] = event_date
        if not await manager.update(bucket_id, **updates):
            return JSONResponse({"error": "update failed"}, status_code=500)
        status = "updated"
    else:
        bucket_id = await manager.create(
            content=content,
            bucket_type=bucket_type,
            protected=bool_value(body.get("protected"), False),
            bucket_id=bucket_id,
            created=str(body.get("created") or now),
            date=event_date or None,
            extra_metadata={"self_anchor": True} if self_anchor else None,
            **common,
        )
        status = "created"

    stored = await manager.get(bucket_id)
    await refresh_bucket_indexes(dependencies, stored)
    if _embedding_enabled(dependencies):
        embedding = "queued" if await queue_embedding(dependencies, bucket_id) else "failed"
    else:
        embedding = "disabled"
    return {
        "status": status,
        "id": bucket_id,
        "source": "chatgpt",
        "embedding": embedding,
    }


async def _inspect_moments_direct(
    dependencies: CurrentWebDependencies,
    *,
    bucket_id: str,
    limit: int,
) -> dict[str, Any]:
    manager = require_dependency(dependencies, "bucket_mgr")
    store = require_dependency(dependencies, "memory_moment_store")
    if bucket_id:
        if not valid_memory_id(bucket_id):
            return {"status": "error", "error": "invalid bucket_id"}
        bucket = await manager.get(bucket_id)
        if not bucket:
            return {"status": "error", "error": "not_found", "bucket_id": bucket_id}
        moments = store.upsert_bucket(bucket)
        edges = store.list_edges(bucket_id)
        metadata = bucket.get("metadata", {})
        return {
            "status": "ok",
            "mode": "bucket",
            "bucket_id": bucket_id,
            "name": metadata.get("name") or bucket_id,
            "count": len(moments),
            "edge_count": len(edges),
            "db_path": str(getattr(store, "db_path", "")),
            "moments": moments[:limit],
            "edges": edges[:limit],
        }
    buckets = await manager.list_all(include_archive=False)
    indexed = store.bulk_upsert(buckets)
    stats = store.stats()
    return {
        "status": "ok",
        "mode": "bulk",
        "indexed_buckets": indexed.get("buckets", 0),
        "indexed_moments": indexed.get("moments", 0),
        "total_buckets": stats.get("buckets", 0),
        "total_moments": stats.get("moments", 0),
        "total_edges": stats.get("edges", 0),
        "db_path": str(getattr(store, "db_path", "")),
        "sample": store.sample(limit),
    }


def _raw_events_from_body(
    body: dict[str, Any],
    *,
    default_session_id: str,
) -> list[dict[str, Any]]:
    if isinstance(body.get("events"), list):
        events = [dict(item) for item in body["events"] if isinstance(item, dict)]
    elif isinstance(body.get("event"), dict):
        events = [dict(body["event"])]
    elif any(key in body for key in ("role", "text", "content")):
        events = [dict(body)]
    else:
        return []
    common = {
        "source": body.get("source"),
        "conversation_id": body.get("conversation_id") or default_session_id,
        "session_id": body.get("session_id") or default_session_id,
        "client": body.get("client"),
    }
    for event in events:
        for key, value in common.items():
            if value is not None:
                event.setdefault(key, value)
    return events


def _raw_search_value(
    body: dict[str, Any],
    params: Any,
    name: str,
    default: Any = "",
) -> Any:
    return body.get(name, params.get(name, default))


def register(mcp: Any, dependencies: CurrentWebDependencies) -> None:
    """Register current memory and store-backed API routes."""

    @mcp.custom_route("/api/bucket/{bucket_id}/comments", methods=["POST"])
    async def add_comment(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        manager = dependencies.bucket_mgr
        if manager is None:
            return exception_response(MissingDependency("bucket_mgr"))
        bucket_id = str(request.path_params.get("bucket_id") or "").strip()
        if not valid_memory_id(bucket_id):
            return JSONResponse({"error": "invalid bucket_id"}, status_code=400)
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        content = str(body.get("content") or "").strip()
        if not content:
            return JSONResponse({"error": "empty content"}, status_code=400)
        if not await manager.get(bucket_id):
            return JSONResponse({"error": "not found", "id": bucket_id}, status_code=404)
        try:
            author = _identity(dependencies)["user_name"]
            entry = await manager.add_comment(
                bucket_id,
                content,
                author=author,
                kind=str(body.get("kind") or "comment"),
                valence=(
                    float_between(body.get("valence"), 0.5, 0.0, 1.0)
                    if body.get("valence") is not None
                    else None
                ),
                arousal=(
                    float_between(body.get("arousal"), 0.3, 0.0, 1.0)
                    if body.get("arousal") is not None
                    else None
                ),
                source="dashboard",
                touch=True,
            )
            if not entry:
                return JSONResponse({"error": "write failed", "id": bucket_id}, status_code=500)
            queued = await queue_embedding(dependencies, bucket_id)
            bucket = await manager.get(bucket_id)
            await refresh_bucket_indexes(dependencies, bucket)
            return JSONResponse(
                {
                    "status": "commented",
                    "id": bucket_id,
                    "comment": entry,
                    "embedding_refreshed": False,
                    "embedding_queued": queued,
                    "metadata": bucket_read_payload(bucket).get("metadata", {}) if bucket else {},
                }
            )
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route(
        "/api/bucket/{bucket_id}/comments/{comment_id}",
        methods=["DELETE"],
    )
    async def delete_comment(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        manager = dependencies.bucket_mgr
        if manager is None:
            return exception_response(MissingDependency("bucket_mgr"))
        bucket_id = str(request.path_params.get("bucket_id") or "").strip()
        comment_id = str(request.path_params.get("comment_id") or "").strip()
        if not valid_memory_id(bucket_id):
            return JSONResponse({"error": "invalid bucket_id"}, status_code=400)
        if not valid_memory_id(comment_id):
            return JSONResponse({"error": "invalid comment_id"}, status_code=400)
        if not await manager.get(bucket_id):
            return JSONResponse({"error": "not found", "id": bucket_id}, status_code=404)
        try:
            result = await manager.delete_comment(
                bucket_id,
                comment_id,
                allowed_author=_identity(dependencies)["user_name"],
                allowed_source="dashboard",
            )
            status = result.get("status")
            if status == "not_found":
                return JSONResponse({"error": "comment not found"}, status_code=404)
            if status == "forbidden":
                return JSONResponse(
                    {"error": "only dashboard user comments can be deleted"},
                    status_code=403,
                )
            if status != "deleted":
                return JSONResponse({"error": "delete failed"}, status_code=500)
            queued = await queue_embedding(dependencies, bucket_id)
            bucket = await manager.get(bucket_id)
            await refresh_bucket_indexes(dependencies, bucket)
            return JSONResponse(
                {
                    "status": "deleted",
                    "id": bucket_id,
                    "comment_id": comment_id,
                    "embedding_refreshed": False,
                    "embedding_queued": queued,
                    "metadata": bucket_read_payload(bucket).get("metadata", {}) if bucket else {},
                }
            )
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/buckets/light", methods=["GET"])
    async def buckets_light(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            manager = require_dependency(dependencies, "bucket_mgr")
            include_archive = bool_value(request.query_params.get("include_archive"), False)
            limit = int_between(request.query_params.get("limit"), 500, 1, 2000)
            offset = int_between(request.query_params.get("offset"), 0, 0, 2**31 - 1)
            buckets = await manager.list_all(include_archive=include_archive)
            items = [bucket_light_payload(bucket) for bucket in buckets]
            items.sort(key=lambda item: str(item.get("created") or ""), reverse=True)
            return JSONResponse(
                {
                    "buckets": items[offset : offset + limit],
                    "count": len(items),
                    "include_archive": include_archive,
                    "limit": limit,
                    "offset": offset,
                }
            )
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/memories", methods=["POST"])
    async def create_memory(request: Request) -> Response:
        if not memory_write_token(dependencies):
            return JSONResponse(
                {"error": "memory write token is not configured"},
                status_code=503,
            )
        if not authorized_memory_write(dependencies, request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        try:
            service = dependencies.services.create_memory
            if callable(service):
                return service_json(await maybe_await(service(body)))
            return service_json(await _create_memory_direct(dependencies, body))
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/bucket/{bucket_id}", methods=["PATCH"])
    async def update_bucket(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        bucket_id = str(request.path_params.get("bucket_id") or "").strip()
        if not valid_memory_id(bucket_id):
            return JSONResponse({"error": "invalid bucket_id"}, status_code=400)
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        content = str(body.get("content") or "").strip() if "content" in body else None
        name = str(body.get("name") or "").strip() if "name" in body else None
        event_date = str(body.get("date") or "").strip() if "date" in body else None
        if content is None and name is None and event_date is None:
            return JSONResponse({"error": "missing content, name, or date"}, status_code=400)
        if event_date:
            event_date = local_date_key(event_date)
            if not event_date:
                return JSONResponse({"error": "invalid date"}, status_code=400)
        try:
            manager = require_dependency(dependencies, "bucket_mgr")
            before = await manager.get(bucket_id)
            if not before:
                return JSONResponse({"error": "not found"}, status_code=404)
            metadata = before.get("metadata", {})
            if content is not None:
                if not content:
                    return JSONResponse({"error": "empty content"}, status_code=400)
                if has_favorite_memory_tag(metadata.get("tags", [])) and not _favorite_reason_present(content):
                    return JSONResponse({"error": _favorite_reason_error()}, status_code=400)
            updates: dict[str, Any] = {
                "last_active": metadata.get("last_active") or metadata.get("created")
            }
            if content is not None:
                updates["content"] = content
            if name is not None:
                updates["name"] = name or None
            if event_date is not None:
                updates["date"] = event_date
            if not await manager.update(bucket_id, **updates):
                return JSONResponse({"error": "update failed"}, status_code=500)
            bucket = await manager.get(bucket_id)
            if bucket is None:
                return JSONResponse({"error": "updated bucket not found"}, status_code=500)
            queued = (
                await queue_embedding(dependencies, bucket_id)
                if content is not None or name is not None
                else False
            )
            await refresh_bucket_indexes(dependencies, bucket)
            return JSONResponse(
                {
                    "status": "updated",
                    "id": bucket_id,
                    "embedding_refreshed": False,
                    "embedding_queued": queued,
                    **bucket_read_payload(bucket),
                }
            )
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/buckets/delete", methods=["POST"])
    async def delete_buckets(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        if body.get("confirm") != "DELETE":
            return JSONResponse({"error": "confirmation required"}, status_code=400)
        raw_ids = body.get("bucket_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return JSONResponse(
                {"error": "bucket_ids must be a non-empty list"},
                status_code=400,
            )
        if len(raw_ids) > 200:
            return JSONResponse({"error": "too many bucket_ids"}, status_code=400)
        summary = {"deleted": 0, "skipped": 0, "not_found": 0, "invalid": 0, "failed": 0}
        results: list[dict[str, Any]] = []
        try:
            for bucket_id in dict.fromkeys(str(item or "").strip() for item in raw_ids):
                result = await _delete_bucket(dependencies, bucket_id)
                status = result.get("status", "failed")
                summary[status if status in summary else "failed"] += 1
                results.append(result)
            return JSONResponse({**summary, "results": results})
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/buckets/bulk-update", methods=["POST"])
    async def bulk_update_buckets(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        raw_ids = body.get("bucket_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return JSONResponse(
                {"error": "bucket_ids must be a non-empty list"},
                status_code=400,
            )
        if len(raw_ids) > 300:
            return JSONResponse({"error": "too many bucket_ids"}, status_code=400)
        domain = normalize_domain_key(body.get("domain")) if "domain" in body else ""
        if "domain" in body and not domain:
            return JSONResponse({"error": "invalid domain"}, status_code=400)
        tags_add = _unique_clean_list(body.get("tags_add"))
        tags_remove = _unique_clean_list(body.get("tags_remove"))
        facets_add = _unique_clean_list(body.get("facets_add"))
        facets_remove = _unique_clean_list(body.get("facets_remove"))
        target_status = str(body.get("status") or "").strip().lower()
        if target_status not in {"", "active", "archived"}:
            return JSONResponse(
                {"error": "status must be archived or active"},
                status_code=400,
            )
        if not any((domain, tags_add, tags_remove, facets_add, facets_remove, target_status)):
            return JSONResponse({"error": "no bulk operation requested"}, status_code=400)

        summary = {"matched": 0, "changed": 0, "unchanged": 0, "not_found": 0, "invalid": 0, "failed": 0}
        changed_ids: list[str] = []
        results: list[dict[str, str]] = []
        try:
            manager = require_dependency(dependencies, "bucket_mgr")
            for bucket_id in dict.fromkeys(str(item or "").strip() for item in raw_ids):
                if not valid_memory_id(bucket_id):
                    summary["invalid"] += 1
                    results.append(
                        {
                            "id": bucket_id,
                            "status": "invalid",
                            "reason": "invalid_bucket_id",
                        }
                    )
                    continue
                bucket = await manager.get(bucket_id)
                if not bucket:
                    summary["not_found"] += 1
                    results.append(
                        {"id": bucket_id, "status": "not_found", "reason": "not_found"}
                    )
                    continue
                summary["matched"] += 1
                metadata = bucket.get("metadata", {})
                updates: dict[str, Any] = {}
                if domain and metadata.get("domain") != [domain]:
                    updates["domain"] = [domain]
                tags, tags_changed = _merge_metadata_list(
                    metadata.get("tags"), add=tags_add, remove=tags_remove
                )
                facets, facets_changed = _merge_metadata_list(
                    metadata.get("facets"), add=facets_add, remove=facets_remove
                )
                if tags_changed:
                    updates["tags"] = tags
                if facets_changed:
                    updates["facets"] = facets
                changed = bool(updates)
                if updates and not await manager.update(
                    bucket_id,
                    **updates,
                    last_active=metadata.get("last_active") or metadata.get("created"),
                ):
                    summary["failed"] += 1
                    results.append(
                        {"id": bucket_id, "status": "failed", "reason": "update_failed"}
                    )
                    continue
                current = await manager.get(bucket_id)
                current_meta = (current or {}).get("metadata", {})
                if target_status == "archived" and current_meta.get("type") != "archived":
                    if not await manager.archive(bucket_id):
                        summary["failed"] += 1
                        results.append(
                            {
                                "id": bucket_id,
                                "status": "failed",
                                "reason": "archive_failed",
                            }
                        )
                        continue
                    changed = True
                elif target_status == "active" and current_meta.get("type") == "archived":
                    if not await manager.activate(bucket_id):
                        summary["failed"] += 1
                        results.append(
                            {
                                "id": bucket_id,
                                "status": "failed",
                                "reason": "activate_failed",
                            }
                        )
                        continue
                    changed = True
                elif target_status == "active" and (
                    current_meta.get("active") is False
                    or current_meta.get("resolved")
                    or current_meta.get("deprecated")
                ):
                    if not await manager.update(
                        bucket_id,
                        active=True,
                        deprecated=False,
                        resolved=False,
                        last_active=current_meta.get("last_active") or current_meta.get("created"),
                    ):
                        summary["failed"] += 1
                        results.append(
                            {
                                "id": bucket_id,
                                "status": "failed",
                                "reason": "activate_failed",
                            }
                        )
                        continue
                    changed = True
                state = "changed" if changed else "unchanged"
                summary[state] += 1
                if changed:
                    changed_ids.append(bucket_id)
                    await refresh_bucket_indexes(dependencies, await manager.get(bucket_id))
                results.append({"id": bucket_id, "status": state})
            return JSONResponse(
                {
                    **summary,
                    "changed_ids": changed_ids,
                    "changed_count": len(changed_ids),
                    "results": results,
                }
            )
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/moments", methods=["GET"])
    async def moments(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        bucket_id = str(request.query_params.get("bucket_id") or "").strip()
        limit = int_between(request.query_params.get("limit"), 20, 1, 200)
        try:
            service = dependencies.services.inspect_moments
            if callable(service):
                payload = await maybe_await(service(bucket_id=bucket_id, limit=limit))
            else:
                payload = await _inspect_moments_direct(
                    dependencies,
                    bucket_id=bucket_id,
                    limit=limit,
                )
            status_code = 200
            if isinstance(payload, dict) and payload.get("status") == "error":
                status_code = 404 if payload.get("error") == "not_found" else 400
            return service_json(payload, status_code=status_code)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/todos", methods=["GET"])
    async def todos(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        return JSONResponse(
            {
                "count": 0,
                "todos": [],
                "disabled": True,
                "message": "Derived followup/todo items are disabled. Use /api/reminders instead.",
            }
        )

    @mcp.custom_route("/api/todos/{todo_id}", methods=["PATCH"])
    async def update_todo(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        return JSONResponse(
            {"error": "derived followup/todo items are disabled; use /api/reminders instead"},
            status_code=410,
        )

    @mcp.custom_route("/api/todos/{todo_id}/writeback", methods=["POST"])
    async def writeback_todo(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        return JSONResponse(
            {"error": "todo writeback is disabled; use /api/reminders instead"},
            status_code=410,
        )

    @mcp.custom_route("/api/reminders", methods=["GET"])
    async def reminders(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            store = require_dependency(dependencies, "reminder_store")
            items = await maybe_await(
                store.list(
                    status=str(request.query_params.get("status") or "active").strip().lower(),
                    limit=int_between(request.query_params.get("limit"), 50, 1, 200),
                )
            )
            return JSONResponse(
                {"count": len(items), "reminders": [public_reminder(item) for item in items]}
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/reminders", methods=["POST"])
    async def create_reminder(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        try:
            store = require_dependency(dependencies, "reminder_store")
            item = await maybe_await(
                store.create(
                    title=str(body.get("title") or ""),
                    content=str(body.get("content") or body.get("text") or ""),
                    next_due_at=str(body.get("next_due_at") or ""),
                    start_at=str(body.get("start_at") or ""),
                    end_at=str(body.get("end_at") or ""),
                    repeat_rule=str(body.get("repeat_rule") or "every_n_rounds"),
                    interval_rounds=int_between(body.get("interval_rounds"), 6, 0, 100000),
                    cooldown_minutes=int_between(body.get("cooldown_minutes"), 0, 0, 525600),
                    daily_limit=(
                        int_between(body.get("daily_limit"), 1, 0, 100)
                        if "daily_limit" in body
                        else None
                    ),
                    max_injections=int_between(body.get("max_injections"), 0, 0, 100000),
                    channel=str(body.get("channel") or "global"),
                    session_id=str(body.get("session_id") or ""),
                    source=str(body.get("source") or "dashboard"),
                )
            )
            return JSONResponse({"status": "created", "reminder": public_reminder(item)})
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/reminders/{reminder_id}", methods=["PATCH"])
    async def update_reminder(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        reminder_id = str(request.path_params.get("reminder_id") or "").strip()
        if not reminder_id:
            return JSONResponse({"error": "missing reminder_id"}, status_code=400)
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        try:
            store = require_dependency(dependencies, "reminder_store")
            if body.get("mark_reminded"):
                item = await maybe_await(
                    store.mark_reminded(
                        reminder_id,
                        round_id=int_between(body.get("round_id"), 0, 0, 100000000),
                    )
                )
            elif body.get("snooze_minutes"):
                item = await maybe_await(
                    store.snooze(
                        reminder_id,
                        minutes=int_between(body.get("snooze_minutes"), 60, 1, 525600),
                    )
                )
            else:
                content = body.get("content") if "content" in body else body.get("text")
                item = await maybe_await(
                    store.update(
                        reminder_id,
                        title=body.get("title") if "title" in body else None,
                        content=content,
                        status=body.get("status") if "status" in body else None,
                        channel=body.get("channel") if "channel" in body else None,
                        session_id=body.get("session_id") if "session_id" in body else None,
                        start_at=body.get("start_at") if "start_at" in body else None,
                        end_at=body.get("end_at") if "end_at" in body else None,
                        next_due_at=body.get("next_due_at") if "next_due_at" in body else None,
                        repeat_rule=body.get("repeat_rule") if "repeat_rule" in body else None,
                        interval_rounds=(
                            int_between(body.get("interval_rounds"), 0, 0, 100000)
                            if "interval_rounds" in body
                            else None
                        ),
                        cooldown_minutes=(
                            int_between(body.get("cooldown_minutes"), 0, 0, 525600)
                            if "cooldown_minutes" in body
                            else None
                        ),
                        daily_limit=(
                            int_between(body.get("daily_limit"), 1, 0, 100)
                            if "daily_limit" in body
                            else None
                        ),
                        max_injections=(
                            int_between(body.get("max_injections"), 0, 0, 100000)
                            if "max_injections" in body
                            else None
                        ),
                    )
                )
            if not item:
                return JSONResponse({"error": "not found"}, status_code=404)
            return JSONResponse({"status": "updated", "reminder": public_reminder(item)})
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/darkroom/status", methods=["GET"])
    async def darkroom_status(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            store = require_dependency(dependencies, "darkroom_store")
            return JSONResponse(await maybe_await(store.status()))
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/ingest-raw", methods=["POST"])
    async def ingest_raw(request: Request) -> Response:
        if error := await raw_api_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request, allow_empty=True)
        except (TypeError, ValueError) as exc:
            message = (
                "request body must be an object"
                if isinstance(exc, TypeError)
                else str(exc)
            )
            return JSONResponse({"error": message}, status_code=400)
        session_id = str(request.headers.get("x-ombre-session-id") or "").strip()
        events = _raw_events_from_body(body, default_session_id=session_id)
        if not events:
            return JSONResponse({"error": "missing events"}, status_code=400)
        try:
            store = require_dependency(dependencies, "raw_event_store")
            result = await maybe_await(store.ingest(events, source=str(body.get("source") or "raw")))
            return JSONResponse(result)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/search-raw", methods=["GET", "POST"])
    async def search_raw(request: Request) -> Response:
        if error := await raw_api_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request, allow_empty=True)
        except ValueError as exc:
            return json_body_error(exc)
        except TypeError:
            body = {}
        params = request.query_params
        query = str(
            _raw_search_value(
                body,
                params,
                "q",
                _raw_search_value(body, params, "query", ""),
            )
            or ""
        )
        try:
            store = require_dependency(dependencies, "raw_event_store")
            result = await maybe_await(
                store.search(
                    query=query,
                    limit=int_between(_raw_search_value(body, params, "limit", 10), 10, 1, 100),
                    source=str(_raw_search_value(body, params, "source", "") or ""),
                    role=str(_raw_search_value(body, params, "role", "") or ""),
                    conversation_id=str(
                        _raw_search_value(body, params, "conversation_id", "") or ""
                    ),
                    session_id=str(_raw_search_value(body, params, "session_id", "") or ""),
                    since=str(_raw_search_value(body, params, "since", "") or ""),
                    until=str(_raw_search_value(body, params, "until", "") or ""),
                )
            )
            return JSONResponse(result)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/edges", methods=["GET"])
    async def edges(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            store = require_dependency(dependencies, "memory_edge_store")
            return JSONResponse({"edges": await maybe_await(store.list_edges())})
        except Exception as exc:
            return exception_response(exc)


__all__ = ["register"]
