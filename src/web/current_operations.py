"""Current-production diagnostics, workers, configuration, and backup routes."""

from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backup_archive import BackupArchiveError, MAX_ARCHIVE_BYTES
from config_diagnostics import effective_config_report
from entity_edges import extract_entity_edges_from_bucket
from identity import identity_names
from self_anchor import is_self_anchor_bucket

from .current_contract import (
    CurrentWebDependencies,
    bool_value,
    cleanup_bucket_indexes,
    dashboard_auth,
    exception_response,
    float_between,
    int_between,
    json_body_error,
    maybe_await,
    queue_embedding,
    read_json_object,
    require_dependency,
    require_service,
    service_json,
)
from .import_api import _CleanupFileResponse
from .upload_limits import read_multipart_form_limited


_NO_WORKER_RESULT = object()


async def _settle_cancelled_worker(worker: asyncio.Task[Any]) -> Any:
    """Reap a thread worker even if its caller is cancelled repeatedly."""

    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            continue
        except BaseException:
            break
    try:
        return worker.result()
    except BaseException:
        return _NO_WORKER_RESULT


async def _run_blocking(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run blocking work off-loop and never abandon a live worker on cancellation."""

    worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await _settle_cancelled_worker(worker)
        raise


def _invoke_maybe_async(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke sync or async dependency code inside a worker-owned event loop."""

    return asyncio.run(maybe_await(operation(*args, **kwargs)))


def _unlink_file(path: str) -> None:
    Path(path).unlink(missing_ok=True)


def _discard_upload_temp(result: tuple[int, str]) -> None:
    descriptor, upload_path = result
    try:
        os.close(descriptor)
    finally:
        _unlink_file(upload_path)


async def _create_upload_temp_off_loop() -> tuple[int, str]:
    """Create a restore spool without leaking it across request cancellation."""

    worker = asyncio.create_task(
        asyncio.to_thread(
            tempfile.mkstemp,
            prefix="ombre-upload-",
            suffix=".zip",
        )
    )
    try:
        result = await asyncio.shield(worker)
    except asyncio.CancelledError:
        result = await _settle_cancelled_worker(worker)
        if (
            isinstance(result, tuple)
            and len(result) == 2
            and isinstance(result[0], int)
        ):
            cleanup_worker = asyncio.create_task(
                asyncio.to_thread(_discard_upload_temp, result)
            )
            await _settle_cancelled_worker(cleanup_worker)
        raise
    return int(result[0]), str(result[1])


async def _create_archive_off_loop(manager: Any) -> tuple[str, dict[str, Any]]:
    """Build an archive in a worker and remove a late result after cancellation."""

    worker = asyncio.create_task(
        asyncio.to_thread(_invoke_maybe_async, manager.create_archive)
    )
    try:
        result = await asyncio.shield(worker)
    except asyncio.CancelledError:
        result = await _settle_cancelled_worker(worker)
        if isinstance(result, tuple) and result and result[0]:
            cleanup_worker = asyncio.create_task(
                asyncio.to_thread(_unlink_file, str(result[0]))
            )
            await _settle_cancelled_worker(cleanup_worker)
        raise
    candidate_path = (
        str(result[0])
        if isinstance(result, tuple) and result and result[0]
        else ""
    )
    if not isinstance(result, tuple) or len(result) != 2:
        if candidate_path:
            await _run_blocking(_unlink_file, candidate_path)
        raise TypeError("backup manager returned an invalid archive result")
    archive_path, manifest = result
    if not isinstance(manifest, dict):
        if candidate_path:
            await _run_blocking(_unlink_file, candidate_path)
        raise TypeError("backup manager returned an invalid archive manifest")
    return str(archive_path), manifest


async def _store_activity_summary(
    dependencies: CurrentWebDependencies,
    result: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"status": "invalid", "reason": "result_not_object"}
    if result.get("status") != "ready":
        return result
    item = result.get("activity_summary")
    if not isinstance(item, dict) or not item:
        return {**result, "status": "skipped", "reason": "empty_activity_summary"}
    engine = dependencies.portrait_engine
    if engine is None:
        return {**result, "status": "error", "error": "portrait_engine unavailable"}
    date_key = str(result.get("date") or item.get("source_date") or "").strip()
    stored = await maybe_await(engine.upsert_recent_timeline_item(item, date_key))
    return {**result, "status": "stored", "portrait": stored}


async def _daily_impression(
    dependencies: CurrentWebDependencies,
    date_key: str,
) -> dict[str, Any]:
    safe_date = str(date_key or "").strip()[:10]
    manager = dependencies.bucket_mgr
    if not safe_date or manager is None:
        return {}
    bucket = await manager.get(f"reflection_daily_{safe_date}")
    if not bucket:
        return {}
    metadata = bucket.get("metadata", {})
    return {
        "id": bucket.get("id") or f"reflection_daily_{safe_date}",
        "content": bucket.get("content") or "",
        "confidence": metadata.get("confidence", 0.7),
        "date": safe_date,
    }


async def _refresh_restore_indexes(
    dependencies: CurrentWebDependencies,
    bucket_ids: list[str],
) -> dict[str, Any]:
    service = dependencies.services.refresh_restore_indexes
    if callable(service):
        return await maybe_await(service(bucket_ids))

    manager = require_dependency(dependencies, "bucket_mgr")
    refreshed = 0
    errors: list[str] = []
    for bucket_id in dict.fromkeys(str(item or "").strip() for item in bucket_ids):
        if not bucket_id:
            continue
        try:
            await cleanup_bucket_indexes(dependencies, bucket_id)
            bucket = await manager.get(bucket_id)
            if not bucket:
                errors.append(bucket_id)
                continue
            for target_name, method_name in (
                ("memory_moment_store", "upsert_bucket"),
                ("memory_node_store", "upsert_bucket"),
            ):
                target = getattr(dependencies, target_name, None)
                method = getattr(target, method_name, None)
                if callable(method):
                    await maybe_await(method(bucket))
            entity_store = dependencies.entity_edge_store
            replace_edges = getattr(entity_store, "replace_bucket_edges", None)
            if callable(replace_edges) and not is_self_anchor_bucket(bucket):
                await maybe_await(
                    replace_edges(
                        bucket_id,
                        extract_entity_edges_from_bucket(
                            bucket,
                            identity_names(dict(dependencies.config)),
                        ),
                    )
                )
            reflection = dependencies.reflection_engine
            rebuild_memory_edges = getattr(
                reflection,
                "backfill_edges_for_bucket",
                None,
            )
            if callable(rebuild_memory_edges):
                await maybe_await(
                    rebuild_memory_edges(
                        bucket_id,
                        manager,
                        dependencies.memory_edge_store,
                        dependencies.embedding_engine,
                        dry_run=False,
                    )
                )
            refreshed += 1
        except Exception:
            errors.append(bucket_id)

    try:
        buckets = await manager.list_all(include_archive=True)
        identity_store = dependencies.identity_semantic_store
        if identity_store is not None:
            await maybe_await(identity_store.rebuild_alias_index(buckets))
        word_map = dependencies.word_map_store
        if word_map is not None:
            await maybe_await(
                word_map.rebuild(
                    [
                        bucket
                        for bucket in buckets
                        if not is_self_anchor_bucket(bucket)
                    ]
                )
            )
    except Exception:
        errors.append("global_indexes")
    return {"refreshed": refreshed, "errors": errors}


async def _read_backup_upload(request: Request, target) -> int:
    total = 0
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await read_multipart_form_limited(request, MAX_ARCHIVE_BYTES)
        try:
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                raise ValueError("No file field")
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise BackupArchiveError("备份压缩包超过 512 MiB 上限")
                await _run_blocking(target.write, chunk)
        finally:
            await form.close()
    else:
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise BackupArchiveError("备份压缩包超过 512 MiB 上限")
            await _run_blocking(target.write, chunk)
    return total


async def _restore_backup_off_loop(
    dependencies: CurrentWebDependencies,
    manager: Any,
    upload_path: str,
    mode: str,
) -> dict[str, Any]:
    """Restore and rebuild its disk-backed indexes on a worker-owned loop."""

    async def restore_operation() -> dict[str, Any]:
        result = await maybe_await(manager.restore_archive(upload_path, mode=mode))
        if not isinstance(result, dict):
            raise TypeError("backup manager returned an invalid restore result")
        result["scope"] = "memory-vault"
        restored_ids = [str(item) for item in result.get("restored_ids", [])]
        result["derived_indexes"] = await _refresh_restore_indexes(
            dependencies, restored_ids
        )
        return result

    return await _run_blocking(lambda: asyncio.run(restore_operation()))


def register(mcp: Any, dependencies: CurrentWebDependencies) -> None:
    """Register operational current-production compatibility routes."""

    # Archive creation and restore both scan or mutate the full vault. FastMCP
    # can dispatch this route set from different event loops/threads, so a
    # process-wide threading lock is the admission boundary for this register.
    vault_operation_lock = threading.Lock()
    export_ticket_lock = threading.Lock()
    export_tickets: dict[str, dict[str, Any]] = {}

    def reserve_vault_operation() -> Callable[[], None] | None:
        if not vault_operation_lock.acquire(blocking=False):
            return None
        release_guard = threading.Lock()
        released = False

        def release() -> None:
            nonlocal released
            with release_guard:
                if released:
                    return
                released = True
            vault_operation_lock.release()

        return release

    def vault_busy_response() -> JSONResponse:
        return JSONResponse(
            {"error": "A backup operation is already active"},
            status_code=409,
        )

    def expire_export_ticket(ticket: str) -> None:
        with export_ticket_lock:
            entry = export_tickets.pop(ticket, None)
        if entry is None:
            return
        try:
            _unlink_file(str(entry["archive_path"]))
        finally:
            entry["release"]()

    def store_export_ticket(
        archive_path: str,
        filename: str,
        release: Callable[[], None],
    ) -> str:
        ticket = secrets.token_urlsafe(32)
        timer = threading.Timer(120.0, expire_export_ticket, args=(ticket,))
        timer.daemon = True
        with export_ticket_lock:
            export_tickets[ticket] = {
                "archive_path": archive_path,
                "filename": filename,
                "release": release,
                "timer": timer,
            }
        timer.start()
        return ticket

    def consume_export_ticket(ticket: str) -> dict[str, Any] | None:
        with export_ticket_lock:
            entry = export_tickets.pop(ticket, None)
        if entry is not None:
            timer = entry.get("timer")
            if isinstance(timer, threading.Timer):
                timer.cancel()
        return entry

    @mcp.custom_route("/api/diffusion-debug", methods=["GET"])
    async def diffusion_debug(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            service = require_service(dependencies, "inspect_diffusion")
            payload = await maybe_await(
                service(
                    query=str(request.query_params.get("q") or ""),
                    max_seeds=int_between(request.query_params.get("max_seeds"), 3, 1, 20),
                    max_hits=int_between(request.query_params.get("max_hits"), 5, 0, 20),
                    edge_min_confidence=float_between(
                        request.query_params.get("edge_min_confidence"), 0.55, 0.0, 1.0
                    ),
                )
            )
            status_code = 400 if isinstance(payload, dict) and payload.get("status") == "error" else 200
            return service_json(payload, status_code=status_code)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/recall-debug", methods=["GET"])
    async def recall_debug(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            service = require_service(dependencies, "inspect_recall")
            valence = request.query_params.get("valence")
            arousal = request.query_params.get("arousal")
            valence_value = (
                None if valence is None or valence == "" else float(valence)
            )
            arousal_value = (
                None if arousal is None or arousal == "" else float(arousal)
            )
            payload = await maybe_await(
                service(
                    query=str(request.query_params.get("q") or ""),
                    max_candidates=int_between(
                        request.query_params.get("max_candidates"), 20, 1, 100
                    ),
                    max_results=int_between(
                        request.query_params.get("max_results"), 3, 1, 20
                    ),
                    max_tokens=int_between(
                        request.query_params.get("max_tokens"), 800, 1, 20000
                    ),
                    direct_render_mode=str(
                        request.query_params.get("direct_render_mode") or "auto"
                    ),
                    domain=str(request.query_params.get("domain") or ""),
                    valence=valence_value,
                    arousal=arousal_value,
                )
            )
            status_code = 400 if isinstance(payload, dict) and payload.get("status") == "error" else 200
            return service_json(payload, status_code=status_code)
        except (TypeError, ValueError, OverflowError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/gateway-injections", methods=["GET"])
    async def gateway_injections(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        session_id = str(request.query_params.get("session_id") or "").strip()
        limit = int_between(request.query_params.get("limit"), 10, 1, 100)
        include_context = bool_value(request.query_params.get("include_context"), False)
        try:
            service = dependencies.services.fetch_gateway_injections
            if callable(service):
                payload = await maybe_await(
                    service(
                        session_id=session_id,
                        limit=limit,
                        include_context=include_context,
                    )
                )
            else:
                store = require_dependency(dependencies, "gateway_state_store")
                payload = {
                    "status": "ok",
                    "items": await maybe_await(
                        store.list_injection_debug(
                            session_id=session_id,
                            limit=limit,
                            include_context=include_context,
                        )
                    ),
                }
            status_code = 200 if payload.get("status") == "ok" else 502
            return service_json(payload, status_code=status_code)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/reflection/run", methods=["POST"])
    async def reflection_run(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request, allow_empty=True)
        except ValueError as exc:
            return json_body_error(exc)
        except TypeError:
            body = {}
        try:
            engine = require_dependency(dependencies, "reflection_engine")
            manager = require_dependency(dependencies, "bucket_mgr")
            result = await engine.reflect(
                period=str(body.get("period") or "daily"),
                bucket_mgr=manager,
                persona_engine=dependencies.persona_engine,
                embedding_engine=dependencies.embedding_engine,
                force=bool_value(body.get("force"), False),
                conversation_turn_store=dependencies.gateway_state_store,
            )
            return JSONResponse(result)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/daily-chat-memory/run", methods=["POST"])
    async def daily_chat_memory_run(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request, allow_empty=True)
        except ValueError as exc:
            return json_body_error(exc)
        except TypeError:
            body = {}
        try:
            engine = require_dependency(dependencies, "reflection_engine")
            manager = require_dependency(dependencies, "bucket_mgr")
            result = await engine.run_daily_chat_memory(
                manager,
                conversation_turn_store=dependencies.gateway_state_store,
                raw_event_store=dependencies.raw_event_store,
                persona_engine=dependencies.persona_engine,
                embedding_engine=dependencies.embedding_engine,
                key=str(body.get("date") or ""),
                mode=str(body.get("mode") or ""),
                force=bool_value(body.get("force"), False),
            )
            try:
                date_key = str(body.get("date") or result.get("date") or "")
                impression = await _daily_impression(dependencies, date_key)
                activity = await engine.run_daily_activity_summary(
                    conversation_turn_store=dependencies.gateway_state_store,
                    raw_event_store=dependencies.raw_event_store,
                    persona_engine=dependencies.persona_engine,
                    daily_chat_memory_candidates=[
                        item
                        for item in result.get("candidates", [])
                        if isinstance(item, dict)
                    ],
                    daily_impressions=[impression] if impression else [],
                    key=str(body.get("date") or ""),
                    force=bool_value(body.get("force"), False),
                )
                result["daily_activity_summary"] = await _store_activity_summary(
                    dependencies, activity
                )
            except Exception as exc:
                result["daily_activity_summary"] = {
                    "status": "error",
                    "error": str(exc),
                }
            return JSONResponse(result)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/daily-activity-summary/run", methods=["POST"])
    async def daily_activity_summary_run(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request, allow_empty=True)
        except ValueError as exc:
            return json_body_error(exc)
        except TypeError:
            body = {}
        try:
            engine = require_dependency(dependencies, "reflection_engine")
            impression = await _daily_impression(
                dependencies, str(body.get("date") or "")
            )
            result = await engine.run_daily_activity_summary(
                conversation_turn_store=dependencies.gateway_state_store,
                raw_event_store=dependencies.raw_event_store,
                persona_engine=dependencies.persona_engine,
                daily_impressions=[impression] if impression else [],
                key=str(body.get("date") or ""),
                force=bool_value(body.get("force"), False),
            )
            return JSONResponse(await _store_activity_summary(dependencies, result))
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/daily-chat-memory/pending", methods=["GET"])
    async def daily_chat_memory_pending(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            engine = require_dependency(dependencies, "reflection_engine")
            items = await maybe_await(
                engine.list_daily_chat_memory_pending(
                    status=str(request.query_params.get("status") or "pending"),
                    limit=int_between(request.query_params.get("limit"), 50, 1, 200),
                )
            )
            return JSONResponse({"status": "ok", "items": items})
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/daily-chat-memory/confirm", methods=["POST"])
    async def daily_chat_memory_confirm(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        action = str(body.get("action") or "confirm").strip().lower()
        required = "REJECT" if action == "reject" else "WRITE"
        if body.get("confirm") != required:
            return JSONResponse(
                {"error": f"confirmation required: {required}"},
                status_code=400,
            )
        candidate_ids = body.get("candidate_ids")
        if not isinstance(candidate_ids, list) or not candidate_ids:
            return JSONResponse(
                {"error": "candidate_ids must be a non-empty list"},
                status_code=400,
            )
        try:
            engine = require_dependency(dependencies, "reflection_engine")
            manager = require_dependency(dependencies, "bucket_mgr")
            result = await engine.confirm_daily_chat_memory(
                [str(item or "") for item in candidate_ids],
                manager,
                embedding_engine=dependencies.embedding_engine,
                action=action,
                edits=body.get("edits") if isinstance(body.get("edits"), dict) else None,
            )
            return JSONResponse(result)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/config/effective", methods=["GET"])
    async def config_effective(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            service = dependencies.services.effective_config
            if callable(service):
                report = await maybe_await(service())
            else:
                config_path = os.environ.get(
                    "OMBRE_CONFIG_PATH",
                    str(Path(__file__).resolve().parents[1] / "config.yaml"),
                )
                state_dir = str(
                    dependencies.config.get("state_dir") or Path(config_path).parent
                )
                runtime_path = str(
                    dependencies.config.get("_runtime_config_path")
                    or os.environ.get("OMBRE_RUNTIME_CONFIG_PATH")
                    or Path(state_dir) / "config.runtime.yaml"
                )
                report = effective_config_report(
                    dict(dependencies.config),
                    config_path=config_path,
                    runtime_config_path=runtime_path,
                )
            outbox = dependencies.embedding_outbox
            if isinstance(report, dict) and callable(getattr(outbox, "status", None)):
                report["embedding_outbox"] = outbox.status()
            return service_json(report)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/backup/export/prepare", methods=["POST"])
    async def backup_export_prepare(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            manager = require_dependency(dependencies, "backup_manager")
        except Exception as exc:
            return exception_response(exc)
        release_operation = reserve_vault_operation()
        if release_operation is None:
            return vault_busy_response()
        archive_path = ""
        try:
            archive_path, _manifest = await _create_archive_off_loop(manager)
            filename = (
                f"ombre-memory-vault-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
            )
            ticket = store_export_ticket(
                archive_path,
                filename,
                release_operation,
            )
            return JSONResponse({"ok": True, "ticket": ticket})
        except asyncio.CancelledError:
            release_operation()
            raise
        except Exception as exc:
            try:
                if archive_path:
                    await _run_blocking(_unlink_file, archive_path)
            finally:
                release_operation()
            return exception_response(exc)

    @mcp.custom_route("/api/backup/export/status", methods=["GET"])
    async def backup_export_status(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        return JSONResponse({"ok": True, "active": vault_operation_lock.locked()})

    @mcp.custom_route("/api/backup/export", methods=["GET"])
    async def backup_export(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        ticket = str(request.query_params.get("ticket") or "").strip()
        if ticket:
            entry = consume_export_ticket(ticket)
            if entry is None:
                return JSONResponse(
                    {"error": "Backup export ticket expired or was already used"},
                    status_code=410,
                )
            archive_path = str(entry["archive_path"])
            release_operation = entry["release"]

            async def cleanup_prepared_export() -> None:
                try:
                    await _run_blocking(_unlink_file, archive_path)
                finally:
                    release_operation()

            try:
                return _CleanupFileResponse(
                    archive_path,
                    media_type="application/zip",
                    filename=str(entry["filename"]),
                    cleanup=cleanup_prepared_export,
                )
            except BaseException:
                await cleanup_prepared_export()
                raise
        try:
            manager = require_dependency(dependencies, "backup_manager")
        except Exception as exc:
            return exception_response(exc)
        release_operation = reserve_vault_operation()
        if release_operation is None:
            return vault_busy_response()
        archive_path = ""
        try:
            archive_path, _manifest = await _create_archive_off_loop(manager)
            filename = f"ombre-memory-vault-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"

            async def cleanup_export() -> None:
                try:
                    await _run_blocking(_unlink_file, archive_path)
                finally:
                    release_operation()

            try:
                return _CleanupFileResponse(
                    archive_path,
                    media_type="application/zip",
                    filename=filename,
                    cleanup=cleanup_export,
                )
            except BaseException:
                await cleanup_export()
                raise
        except asyncio.CancelledError:
            release_operation()
            raise
        except Exception as exc:
            try:
                if archive_path:
                    await _run_blocking(_unlink_file, archive_path)
            finally:
                release_operation()
            return exception_response(exc)

    @mcp.custom_route("/api/backup/restore", methods=["POST"])
    async def backup_restore(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        mode = str(request.query_params.get("mode") or "skip").strip().lower()
        if mode not in {"skip", "overwrite"}:
            return JSONResponse(
                {"error": "mode must be skip or overwrite"},
                status_code=400,
            )
        try:
            manager = require_dependency(dependencies, "backup_manager")
        except Exception as exc:
            return exception_response(exc)
        release_operation = reserve_vault_operation()
        if release_operation is None:
            return vault_busy_response()
        upload_path = ""
        try:
            descriptor, upload_path = await _create_upload_temp_off_loop()
            try:
                target = os.fdopen(descriptor, "wb")
            except BaseException:
                await _run_blocking(
                    _discard_upload_temp,
                    (descriptor, upload_path),
                )
                upload_path = ""
                raise
            try:
                total = await _read_backup_upload(request, target)
                await _run_blocking(target.flush)
                await _run_blocking(os.fsync, target.fileno())
            finally:
                await _run_blocking(target.close)
            if total == 0:
                return JSONResponse({"error": "Empty backup"}, status_code=400)
            result = await _restore_backup_off_loop(
                dependencies,
                manager,
                upload_path,
                mode,
            )
            # The outbox start hook intentionally captures the long-lived
            # application loop. Archive extraction/index rebuild stays on the
            # worker, then durable embedding jobs are admitted back here.
            if result.get("embedding_snapshot") != "restored":
                embeddings_queued = 0
                for bucket_id in result.get("restored_ids", []):
                    if await queue_embedding(dependencies, str(bucket_id)):
                        embeddings_queued += 1
                result["embeddings_queued"] = embeddings_queued
            else:
                result["embeddings_queued"] = 0
            return JSONResponse(result)
        except (BackupArchiveError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            return exception_response(exc)
        finally:
            try:
                if upload_path:
                    await _run_blocking(_unlink_file, upload_path)
            finally:
                release_operation()


__all__ = ["register"]
