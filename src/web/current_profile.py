"""Current-production portrait, profile, identity, dream, and word-map routes."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from memory_metadata import domain_options
from self_anchor import is_self_anchor_bucket
from utils import strip_wikilinks
from word_map import reflection_identity_terms

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
    refresh_bucket_indexes,
    require_dependency,
    require_service,
    service_json,
    valid_memory_id,
)
from .profile_support import (
    build_profile_payload,
    is_profile_fact_bucket as _profile_fact_bucket,
    legacy_profile_key as _profile_key,
    profile_sections as _profile_sections,
)


def _portrait_mutation_response(result: dict[str, Any]) -> Response:
    status = str(result.get("status") or "")
    if status in {"updated", "unchanged"}:
        return JSONResponse(result)
    if status == "conflict":
        return JSONResponse(result, status_code=409)
    if status == "not_found":
        return JSONResponse(result, status_code=404)
    return JSONResponse(result, status_code=400)


def _expected_revision(body: dict[str, Any]) -> int | None:
    raw = body.get("expected_revision")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError, OverflowError):
        return None


async def _portrait_state_direct(
    dependencies: CurrentWebDependencies,
) -> dict[str, Any]:
    engine = require_dependency(dependencies, "portrait_engine")
    manager = require_dependency(dependencies, "bucket_mgr")
    evidence_health: dict[str, Any] = {}
    if callable(getattr(engine, "reconcile_evidence", None)):
        evidence_health = await engine.reconcile_evidence(manager)
    state = engine.load_state()
    handoff: dict[str, Any] = {}
    if callable(getattr(engine, "build_handoff_sections", None)):
        handoff = engine.build_handoff_sections(max_recent_items=3)

    self_anchor_entry: dict[str, Any] = {}
    try:
        anchors = await manager.list_all(include_archive=False)
        anchor = next((item for item in anchors if is_self_anchor_bucket(item)), None)
        if anchor:
            metadata = anchor.get("metadata", {})
            self_anchor_entry = {
                "bucket_id": anchor.get("id", ""),
                "name": metadata.get("name") or "self_anchor",
                "text": strip_wikilinks(anchor.get("content", ""))[:1200],
                "configured": True,
                "updated_at": (
                    metadata.get("updated_at")
                    or metadata.get("last_active")
                    or metadata.get("created", "")
                ),
            }
    except Exception:
        self_anchor_entry = {}

    return {
        "state_path": str(getattr(engine, "state_path", "")),
        "enabled": bool(getattr(engine, "enabled", True)),
        "auto_enabled": bool(getattr(engine, "auto_enabled", True)),
        "auto_initial_enabled": bool(getattr(engine, "auto_initial_enabled", False)),
        "daily_enabled": bool(getattr(engine, "daily_enabled", True)),
        "generator_ready": bool(getattr(engine, "client", None)),
        "generator_model": str(getattr(engine, "model", "") or ""),
        "generator_source": str(getattr(engine, "model_source", "dehydration") or "dehydration"),
        "updated_at": state.get("updated_at", ""),
        "last_run_date": state.get("last_run_date", ""),
        "portrait": state.get("portrait", {}),
        "recent_activities": state.get("recent_activities", []),
        "recent_timeline": state.get("recent_timeline", []),
        "current_focus_items": (
            engine.current_focus_items(max_items=8)
            if callable(getattr(engine, "current_focus_items", None))
            else state.get("recent_activities", [])
        ),
        "current_focus": str(handoff.get("current_focus") or ""),
        "stable_candidates": state.get("stable_candidates", []),
        "profile_fact_candidates": state.get("profile_fact_candidates", []),
        "generation_status": (
            {
                scope: engine.scope_generation_status(scope)
                for scope in ("user", "persona", "relationship")
            }
            if callable(getattr(engine, "scope_generation_status", None))
            else {}
        ),
        "evidence_health": evidence_health,
        "self_anchor_entry": self_anchor_entry,
    }


async def _profile_payload(
    dependencies: CurrentWebDependencies,
    bucket: dict[str, Any],
) -> dict[str, Any]:
    manager = require_dependency(dependencies, "bucket_mgr")
    return await build_profile_payload(
        bucket,
        get_bucket=manager.get,
        edge_store=dependencies.memory_edge_store,
    )

async def _profile_facts_direct(
    dependencies: CurrentWebDependencies,
) -> dict[str, Any]:
    manager = require_dependency(dependencies, "bucket_mgr")
    buckets = await manager.list_all(include_archive=True)
    facts = [
        await _profile_payload(dependencies, bucket)
        for bucket in buckets
        if _profile_fact_bucket(bucket)
    ]
    facts.sort(
        key=lambda item: (
            item.get("state") == "active",
            str(item.get("updated_at") or item.get("last_active") or item.get("created") or ""),
        ),
        reverse=True,
    )
    return {"count": len(facts), "facts": facts}


def _profile_content(body: dict[str, Any], sections: dict[str, str]) -> str:
    fact = str(body.get("fact", sections.get("fact", "")) or "").strip()
    evidence = str(
        body.get("evidence_context", sections.get("evidence_context", "")) or ""
    ).strip()
    reflection = str(body.get("reflection", sections.get("reflection", "")) or "").strip()
    followup = str(body.get("followup", sections.get("followup", "")) or "").strip()
    parts = [f"### fact\n{fact}"]
    if evidence:
        parts.append(f"### evidence_context\n{evidence}")
    if reflection:
        parts.append(f"### reflection\n{reflection}")
    if followup:
        parts.append(f"### followup\n{followup}")
    return "\n\n".join(parts)


async def _profile_update_direct(
    dependencies: CurrentWebDependencies,
    bucket_id: str,
    body: dict[str, Any],
) -> Response:
    manager = require_dependency(dependencies, "bucket_mgr")
    bucket = await manager.get(bucket_id)
    if not bucket:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not _profile_fact_bucket(bucket):
        return JSONResponse({"error": "not a profile_fact bucket"}, status_code=400)
    action = str(body.get("action") or "").strip().lower()
    if action not in {"confirm", "deprecate", "edit"}:
        return JSONResponse(
            {"error": "action must be confirm, deprecate, or edit"},
            status_code=400,
        )
    metadata = bucket.get("metadata", {})
    updates: dict[str, Any] = {
        "last_active": metadata.get("last_active") or metadata.get("created")
    }
    if action == "confirm":
        updates.update(active=True, deprecated=False, resolved=False, digested=False)
    elif action == "deprecate":
        updates.update(active=False, deprecated=True, resolved=True, digested=True)
    else:
        sections = _profile_sections(
            bucket.get("content", ""), key_normalizer=_profile_key
        )
        fact = str(body.get("fact", sections.get("fact", "")) or "").strip()
        if not fact:
            return JSONResponse({"error": "fact is required"}, status_code=400)
        kind = _profile_key(
            body.get("profile_kind", metadata.get("profile_kind") or "preference"),
            "preference",
        )
        predicate = _profile_key(body.get("predicate", metadata.get("predicate") or ""))
        tags = [
            str(tag).strip()
            for tag in metadata.get("tags", []) or []
            if str(tag).strip() and not str(tag).startswith("profile_")
        ]
        tags.extend(["profile_fact", f"profile_{kind}"])
        if predicate:
            tags.append(f"profile_predicate_{predicate}")
        updates.update(
            content=_profile_content(body, sections),
            name="画像事实：" + fact[:48],
            tags=list(dict.fromkeys(tags)),
            profile_kind=kind,
            subject=_profile_key(
                body.get("subject", metadata.get("subject") or "user"), "user"
            ),
            predicate=predicate,
            object=str(body.get("object", metadata.get("object") or "") or "").strip(),
            confidence=float_between(
                body.get("confidence", metadata.get("confidence")), 0.9, 0.0, 1.0
            ),
            source=metadata.get("source") or "profile_fact",
        )
    if not await manager.update(bucket_id, **updates):
        return JSONResponse({"error": "update failed"}, status_code=500)
    updated = await manager.get(bucket_id)
    if updated is None:
        return JSONResponse({"error": "updated bucket not found"}, status_code=500)
    if action == "edit":
        await queue_embedding(dependencies, bucket_id)
    await refresh_bucket_indexes(dependencies, updated)
    return JSONResponse(
        {"status": action, "id": bucket_id, "fact": await _profile_payload(dependencies, updated)}
    )


async def _profile_delete_direct(
    dependencies: CurrentWebDependencies,
    bucket_id: str,
) -> Response:
    manager = require_dependency(dependencies, "bucket_mgr")
    bucket = await manager.get(bucket_id)
    if not bucket:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not _profile_fact_bucket(bucket):
        return JSONResponse({"error": "not a profile_fact bucket"}, status_code=400)
    metadata = bucket.get("metadata", {})
    if metadata.get("protected") or metadata.get("pinned"):
        return JSONResponse(
            {"error": "protected profile_fact cannot be deleted"},
            status_code=403,
        )
    if not await manager.delete(bucket_id):
        return JSONResponse({"id": bucket_id, "status": "failed"}, status_code=500)
    await cleanup_bucket_indexes(dependencies, bucket_id)
    return JSONResponse({"id": bucket_id, "status": "deleted"})


def _private_word_terms(dependencies: CurrentWebDependencies) -> list[str]:
    terms = set(reflection_identity_terms(dict(dependencies.config)))
    identity_store = dependencies.identity_semantic_store
    if identity_store is not None:
        try:
            terms.update(
                str(alias).strip()
                for node in identity_store.load_private_nodes()
                for alias in node.seed_aliases
                if str(alias).strip()
            )
        except Exception:
            pass
    store = dependencies.word_map_store
    if store is not None and terms:
        store.private_terms |= terms
    return sorted(terms)


def _word_map_payload(
    dependencies: CurrentWebDependencies,
    *,
    nodes_limit: int,
    edges_limit: int,
) -> dict[str, Any]:
    store = require_dependency(dependencies, "word_map_store")
    return {
        "enabled": bool(getattr(store, "enabled", False)),
        "stats": store.stats(),
        "nodes": store.list_nodes(nodes_limit),
        "edges": store.list_edges(edges_limit),
        "private_terms_excluded": _private_word_terms(dependencies),
    }


def _identity_payload(
    dependencies: CurrentWebDependencies,
    limit: int,
) -> dict[str, Any]:
    store = require_dependency(dependencies, "identity_semantic_store")
    aliases = store.list_aliases()
    return {
        "enabled": bool(getattr(store, "enabled", False)),
        "private_configured": bool(getattr(store, "private_config_path", "")),
        "stats": store.stats(),
        "aliases": aliases[:limit],
    }


def register(mcp: Any, dependencies: CurrentWebDependencies) -> None:
    """Register profile and derived-index compatibility routes."""

    @mcp.custom_route("/api/domain-taxonomy", methods=["GET"])
    async def domain_taxonomy(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        return JSONResponse({"domains": domain_options()})

    @mcp.custom_route("/api/portrait-state", methods=["GET"])
    async def portrait_state(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            service = dependencies.services.portrait_state
            payload = (
                await maybe_await(service())
                if callable(service)
                else await _portrait_state_direct(dependencies)
            )
            return service_json(payload)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/portrait-maintain", methods=["POST"])
    async def portrait_maintain(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request, allow_empty=True)
        except ValueError as exc:
            return json_body_error(exc)
        except TypeError:
            body = {}
        scope = str(body.get("scope") or "").strip()
        if scope and scope not in {"user", "persona", "relationship"}:
            return JSONResponse({"error": "invalid scope"}, status_code=400)
        try:
            engine = require_dependency(dependencies, "portrait_engine")
            manager = require_dependency(dependencies, "bucket_mgr")
            decay = dependencies.decay_engine
            if callable(getattr(decay, "ensure_started", None)):
                await maybe_await(decay.ensure_started())
            force = bool_value(body.get("force"), False)
            force_scopes = [scope] if scope else (
                ["user", "persona", "relationship"] if force else None
            )
            result = await engine.maintain_daily(
                manager,
                dependencies.persona_engine,
                force=force,
                force_scopes=force_scopes,
            )
            return JSONResponse(
                result,
                status_code=409 if result.get("status") == "blocked" else 200,
            )
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/portrait-state/items", methods=["DELETE"])
    async def portrait_item_delete(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        if body.get("confirm") != "DELETE":
            return JSONResponse({"error": "confirmation required"}, status_code=400)
        try:
            raw_index = body.get("index")
            index = int(raw_index) if raw_index is not None and str(raw_index) != "" else None
        except (TypeError, ValueError, OverflowError):
            return JSONResponse({"error": "index must be an integer"}, status_code=400)
        try:
            engine = require_dependency(dependencies, "portrait_engine")
            result = engine.delete_state_item(
                area=str(body.get("area") or ""),
                scope=str(body.get("scope") or ""),
                layer=str(body.get("layer") or ""),
                index=index,
                text=str(body.get("text") or ""),
            )
            status = str(result.get("status") or "")
            if status == "deleted":
                return JSONResponse(result)
            if status == "not_found":
                return JSONResponse(result, status_code=404)
            if status == "conflict":
                return JSONResponse(result, status_code=409)
            return JSONResponse(result, status_code=400)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/portrait-state/items", methods=["POST"])
    async def portrait_item_add(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        if str(body.get("area") or "") != "recent_activities":
            return JSONResponse(
                {"error": "only recent_activities can be added manually"},
                status_code=400,
            )
        try:
            engine = require_dependency(dependencies, "portrait_engine")
            return _portrait_mutation_response(
                engine.add_recent_activity(
                    str(body.get("text") or ""),
                    source_date=str(body.get("source_date") or ""),
                )
            )
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/portrait-state/items", methods=["PUT"])
    async def portrait_item_edit(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        try:
            raw_index = body.get("index")
            index = int(raw_index) if raw_index is not None and str(raw_index) != "" else None
        except (TypeError, ValueError, OverflowError):
            return JSONResponse({"error": "index must be an integer"}, status_code=400)
        try:
            engine = require_dependency(dependencies, "portrait_engine")
            return _portrait_mutation_response(
                engine.edit_state_item(
                    area=str(body.get("area") or ""),
                    scope=str(body.get("scope") or ""),
                    layer=str(body.get("layer") or ""),
                    index=index,
                    text=str(body.get("text") or ""),
                    expected_text=str(body.get("expected_text") or ""),
                )
            )
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/portrait-state/stable", methods=["PUT"])
    async def portrait_stable_edit(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        revision = _expected_revision(body)
        if revision is None:
            return JSONResponse({"error": "expected_revision is required"}, status_code=400)
        try:
            engine = require_dependency(dependencies, "portrait_engine")
            locked = bool_value(body.get("locked"), False) if "locked" in body else None
            return _portrait_mutation_response(
                engine.edit_stable(
                    scope=str(body.get("scope") or ""),
                    text=str(body.get("text") or ""),
                    expected_revision=revision,
                    locked=locked,
                )
            )
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/portrait-state/stable/lock", methods=["POST"])
    async def portrait_stable_lock(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        revision = _expected_revision(body)
        if revision is None or "locked" not in body:
            return JSONResponse(
                {"error": "expected_revision and locked are required"},
                status_code=400,
            )
        try:
            engine = require_dependency(dependencies, "portrait_engine")
            return _portrait_mutation_response(
                engine.set_stable_lock(
                    scope=str(body.get("scope") or ""),
                    locked=bool_value(body.get("locked"), False),
                    expected_revision=revision,
                )
            )
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/portrait-state/stable/rollback", methods=["POST"])
    async def portrait_stable_rollback(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        revision = _expected_revision(body)
        raw_target = body.get("target_revision")
        if raw_target is None or str(raw_target).strip() == "":
            target = None
        else:
            try:
                target = int(str(raw_target))
            except (TypeError, ValueError, OverflowError):
                target = None
        if revision is None or target is None:
            return JSONResponse(
                {"error": "expected_revision and target_revision are required"},
                status_code=400,
            )
        try:
            engine = require_dependency(dependencies, "portrait_engine")
            return _portrait_mutation_response(
                engine.rollback_stable(
                    scope=str(body.get("scope") or ""),
                    target_revision=target,
                    expected_revision=revision,
                )
            )
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/portrait-state/reset", methods=["POST"])
    async def portrait_reset(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        if body.get("confirm") != "RESET":
            return JSONResponse({"error": "confirmation required"}, status_code=400)
        try:
            engine = require_dependency(dependencies, "portrait_engine")
            return JSONResponse(engine.reset_state())
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/profile-facts", methods=["GET"])
    async def profile_facts(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            service = dependencies.services.profile_facts
            payload = (
                await maybe_await(service())
                if callable(service)
                else await _profile_facts_direct(dependencies)
            )
            return service_json(payload)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/profile-facts/{bucket_id}", methods=["PATCH"])
    async def profile_fact_update(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        bucket_id = str(request.path_params.get("bucket_id") or "").strip()
        if not valid_memory_id(bucket_id):
            return JSONResponse({"error": "invalid bucket_id"}, status_code=400)
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        try:
            service = dependencies.services.profile_fact_update
            if callable(service):
                return service_json(await maybe_await(service(bucket_id, body)))
            return await _profile_update_direct(dependencies, bucket_id, body)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/profile-facts/{bucket_id}", methods=["DELETE"])
    async def profile_fact_delete(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        bucket_id = str(request.path_params.get("bucket_id") or "").strip()
        if not valid_memory_id(bucket_id):
            return JSONResponse({"error": "invalid bucket_id"}, status_code=400)
        try:
            body = await read_json_object(request, allow_empty=True)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        if body.get("confirm") != "DELETE":
            return JSONResponse({"error": "confirmation required"}, status_code=400)
        try:
            service = dependencies.services.profile_fact_delete
            if callable(service):
                return service_json(await maybe_await(service(bucket_id, body)))
            return await _profile_delete_direct(dependencies, bucket_id)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/profile-fact-proposals", methods=["POST"])
    async def profile_fact_proposals(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        bucket_id = str(body.get("bucket_id") or body.get("evidence_bucket_id") or "").strip()
        if not valid_memory_id(bucket_id):
            return JSONResponse({"error": "invalid bucket_id"}, status_code=400)
        moment_id = str(body.get("evidence_moment_id") or body.get("moment_id") or "").strip()
        if moment_id and not valid_memory_id(moment_id):
            return JSONResponse({"error": "invalid evidence_moment_id"}, status_code=400)
        try:
            service = require_service(dependencies, "profile_fact_proposals")
            return service_json(await maybe_await(service(body)))
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/profile-fact-proposals/confirm", methods=["POST"])
    async def profile_fact_confirm(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        evidence_id = str(body.get("evidence_bucket_id") or "").strip()
        if not valid_memory_id(evidence_id):
            return JSONResponse({"error": "invalid evidence_bucket_id"}, status_code=400)
        try:
            service = require_service(dependencies, "profile_fact_confirm")
            return service_json(await maybe_await(service(body)))
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/anchor-proposals", methods=["POST"])
    async def anchor_proposals(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        if not valid_memory_id(body.get("bucket_id")):
            return JSONResponse({"error": "invalid bucket_id"}, status_code=400)
        try:
            service = require_service(dependencies, "anchor_proposals")
            return service_json(await maybe_await(service(body)))
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/anchor-proposals/confirm", methods=["POST"])
    async def anchor_confirm(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        bucket_id = str(body.get("bucket_id") or "").strip()
        if not valid_memory_id(bucket_id):
            return JSONResponse({"error": "invalid bucket_id"}, status_code=400)
        try:
            service = require_service(dependencies, "anchor_confirm")
            return service_json(await maybe_await(service(body)))
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/word-map", methods=["GET"])
    async def word_map(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            return JSONResponse(
                _word_map_payload(
                    dependencies,
                    nodes_limit=int_between(request.query_params.get("nodes"), 50, 1, 500),
                    edges_limit=int_between(request.query_params.get("edges"), 50, 1, 500),
                )
            )
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/word-map/rebuild", methods=["POST"])
    async def word_map_rebuild(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request, allow_empty=True)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        try:
            store = require_dependency(dependencies, "word_map_store")
            manager = require_dependency(dependencies, "bucket_mgr")
            include_archive = bool_value(body.get("include_archive"), False)
            private_terms = _private_word_terms(dependencies)
            buckets = await manager.list_all(include_archive=include_archive)
            buckets = [bucket for bucket in buckets if not is_self_anchor_bucket(bucket)]
            stats = store.rebuild(buckets)
            payload = _word_map_payload(
                dependencies,
                nodes_limit=int_between(body.get("nodes"), 50, 1, 500),
                edges_limit=int_between(body.get("edges"), 50, 1, 500),
            )
            payload.update(
                status="rebuilt",
                bucket_count=len(buckets),
                include_archive=include_archive,
                stats=stats,
                private_terms_excluded=private_terms,
            )
            return JSONResponse(payload)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/word-map/cards", methods=["GET"])
    async def word_map_cards(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        term = str(request.query_params.get("term") or "").strip()
        if not term:
            return JSONResponse({"error": "missing term parameter"}, status_code=400)
        try:
            store = require_dependency(dependencies, "word_map_store")
            limit = int_between(request.query_params.get("limit"), 20, 1, 200)
            return JSONResponse({"term": term, "cards": store.cards_for_term(term, limit)})
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/identity-semantics", methods=["GET"])
    async def identity_semantics(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            limit = int_between(request.query_params.get("limit"), 100, 1, 1000)
            return JSONResponse(_identity_payload(dependencies, limit))
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/identity-semantics/rebuild", methods=["POST"])
    async def identity_semantics_rebuild(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            body = await read_json_object(request, allow_empty=True)
        except (TypeError, ValueError) as exc:
            return json_body_error(exc)
        try:
            store = require_dependency(dependencies, "identity_semantic_store")
            manager = require_dependency(dependencies, "bucket_mgr")
            include_archive = bool_value(body.get("include_archive"), False)
            limit = int_between(body.get("limit"), 100, 1, 1000)
            buckets = await manager.list_all(include_archive=include_archive)
            stats = store.rebuild_alias_index(buckets)
            payload = _identity_payload(dependencies, limit)
            payload.update(
                status="rebuilt",
                bucket_count=len(buckets),
                include_archive=include_archive,
                stats=stats,
            )
            return JSONResponse(payload)
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/persona", methods=["GET"])
    async def persona(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            engine = require_dependency(dependencies, "persona_engine")
            return JSONResponse(
                engine.get_dashboard_payload(
                    session_id=str(request.query_params.get("session_id") or "").strip() or None,
                    events_limit=int_between(request.query_params.get("events_limit"), 20, 1, 100),
                    sessions_limit=int_between(
                        request.query_params.get("sessions_limit"), 20, 1, 100
                    ),
                )
            )
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/dreams", methods=["GET"])
    async def dreams(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            engine = require_dependency(dependencies, "dream_engine")
            limit = int_between(request.query_params.get("limit"), 30, 1, 100)
            return JSONResponse(engine.dashboard_payload(limit=limit))
        except Exception as exc:
            return exception_response(exc)

    @mcp.custom_route("/api/dreams/{dream_id}", methods=["GET"])
    async def dream_detail(request: Request) -> Response:
        if error := await dashboard_auth(dependencies, request):
            return error
        try:
            engine = require_dependency(dependencies, "dream_engine")
            record = engine.dashboard_record(request.path_params.get("dream_id", ""))
            if not record:
                return JSONResponse({"error": "dream body unavailable"}, status_code=404)
            return JSONResponse(record)
        except Exception as exc:
            return exception_response(exc)


__all__ = ["register"]
