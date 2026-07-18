"""Dependency-injected services for current-production compatibility routes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from starlette.responses import JSONResponse, Response

from config_modes import normalize_direct_render_mode
from memory_metadata import normalize_memory_metadata
from runtime_values import (
    age_hours_since,
    float_between as _float_between,
    metadata_view as _metadata,
    numeric_int_between as _int_between,
    utc_now as _utc_now,
    valid_memory_id as _valid_id,
)
from utils import strip_wikilinks

from .current_contract import CurrentWebServices, dependency_error, maybe_await
from .profile_support import (
    build_profile_payload,
    is_profile_fact_bucket as _is_profile_fact_bucket,
    profile_key as _profile_key,
)


ServiceOperation = Callable[..., Any]
Clock = Callable[[], datetime]

PROFILE_FACT_PREFIX = "profile_fact→"
ANCHOR_SUCCESS_PREFIX = "已修改记忆桶"


@dataclass(slots=True)
class CurrentServiceDependencies:
    """Runtime collaborators needed by the high-level compatibility services."""

    config: Mapping[str, Any] = field(default_factory=dict)
    bucket_mgr: Any = None
    memory_edge_store: Any = None
    inspect_diffusion_operation: ServiceOperation | None = None
    inspect_recall_operation: ServiceOperation | None = None
    profile_fact_proposal_model: ServiceOperation | None = None
    profile_fact_writer: ServiceOperation | None = None
    anchor_proposal_model: ServiceOperation | None = None
    # Inject the historical trace(anchor=1) operation or an equivalent guarded writer.
    anchor_writer: ServiceOperation | None = None
    model_name: str = ""
    logger: Any = None
    clock: Clock = _utc_now


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _clip_text(value: Any, max_chars: int) -> str:
    compact = " ".join(strip_wikilinks(str(value or "")).split())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rstrip() + "..."


def _normalize_profile_fact_key(value: Any) -> str:
    return re.sub(
        r"[\s。；;，,、：:\"'“”‘’「」『』]+",
        "",
        str(value or "").lower(),
    )


def _existing_profile_fact_keys(buckets: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for bucket in buckets:
        metadata = _metadata(bucket)
        tags = {str(tag) for tag in metadata.get("tags", []) or []}
        if "profile_fact" not in tags and not metadata.get("profile_kind"):
            continue
        for line in str(bucket.get("content") or "").splitlines():
            text = line.strip()
            if text and not text.startswith("#"):
                keys.add(_normalize_profile_fact_key(text))
                break
    return keys


def _strip_json_wrapper(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return text


def _parse_model_output(raw: Any) -> tuple[Any, dict[str, Any] | None]:
    if isinstance(raw, (list, dict)):
        return raw, None
    try:
        return json.loads(_strip_json_wrapper(str(raw or ""))), None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, {"reason": "invalid json", "raw": _clip_text(raw, 240)}


def _normalize_profile_fact_proposal(
    item: Any,
    *,
    evidence_bucket_id: str,
    evidence_moment_id: str,
    existing_keys: set[str],
) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(item, dict):
        return None, "proposal is not an object"
    fact = str(item.get("fact") or "").strip()
    if not fact:
        return None, "missing fact"
    candidate_evidence = str(item.get("evidence_bucket_id") or "").strip()
    if candidate_evidence != evidence_bucket_id:
        return None, "evidence_bucket_id mismatch"
    candidate_moment = str(item.get("evidence_moment_id") or evidence_moment_id or "").strip()
    if candidate_moment and not _valid_id(candidate_moment):
        return None, "invalid evidence_moment_id"
    if _normalize_profile_fact_key(fact) in existing_keys:
        return None, "duplicate profile fact"
    return (
        {
            "fact": fact,
            "profile_kind": _profile_key(item.get("profile_kind"), "other"),
            "subject": _profile_key(item.get("subject"), "user"),
            "predicate": _profile_key(item.get("predicate"), "related_to"),
            "object": str(item.get("object") or "").strip()[:160],
            "evidence_bucket_id": evidence_bucket_id,
            "evidence_moment_id": candidate_moment,
            "confidence": _float_between(item.get("confidence"), 0.7, 0.0, 1.0),
            "reason": _clip_text(item.get("reason"), 240),
        },
        "",
    )


def _parse_profile_fact_proposals(
    raw: Any,
    *,
    evidence_bucket_id: str,
    evidence_moment_id: str,
    existing_keys: set[str],
    max_proposals: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parsed, parse_error = _parse_model_output(raw)
    if parse_error is not None:
        return [], [parse_error]
    if isinstance(parsed, dict) and isinstance(parsed.get("proposals"), list):
        parsed = parsed["proposals"]
    if not isinstance(parsed, list):
        return [], [{"reason": "json root is not a list"}]

    proposals: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    limit = _int_between(max_proposals, 3, 1, 3)
    for item in parsed:
        proposal, reason = _normalize_profile_fact_proposal(
            item,
            evidence_bucket_id=evidence_bucket_id,
            evidence_moment_id=evidence_moment_id,
            existing_keys=existing_keys,
        )
        if proposal is None:
            rejected.append(
                {
                    "reason": reason,
                    "proposal": item if isinstance(item, dict) else str(item),
                }
            )
            continue
        key = _normalize_profile_fact_key(proposal["fact"])
        if key in seen:
            rejected.append({"reason": "duplicate in response", "proposal": proposal})
            continue
        seen.add(key)
        proposals.append(proposal)
        if len(proposals) >= limit:
            break
    return proposals, rejected


def _anchor_static_rejection(bucket: dict[str, Any]) -> str:
    metadata = _metadata(bucket)
    if metadata.get("anchor"):
        return "already anchor"
    if metadata.get("pinned") or metadata.get("protected"):
        return "pinned/protected buckets are not anchor proposal targets"
    if _is_profile_fact_bucket(bucket):
        return "profile_fact buckets are not anchor proposal targets"
    if str(metadata.get("type") or "").strip() == "feel":
        return "feel buckets are not anchor proposal targets"
    return ""


def _normalize_anchor_proposal(
    item: Any,
    *,
    bucket_id: str,
) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(item, dict):
        return None, "proposal is not an object"
    if str(item.get("bucket_id") or "").strip() != bucket_id:
        return None, "bucket_id mismatch"
    reason = _clip_text(item.get("reason"), 260)
    if not reason:
        return None, "missing reason"
    return (
        {
            "bucket_id": bucket_id,
            "anchor_kind": _profile_key(item.get("anchor_kind"), "other"),
            "reason": reason,
            "future_use": _clip_text(item.get("future_use"), 220),
            "confidence": _float_between(item.get("confidence"), 0.7, 0.0, 1.0),
        },
        "",
    )


def _parse_anchor_proposals(
    raw: Any,
    *,
    bucket_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parsed, parse_error = _parse_model_output(raw)
    if parse_error is not None:
        return [], [parse_error]
    if isinstance(parsed, dict) and isinstance(parsed.get("proposals"), list):
        parsed = parsed["proposals"]
    if not isinstance(parsed, list):
        return [], [{"reason": "json root is not a list"}]

    proposals: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in parsed:
        proposal, reason = _normalize_anchor_proposal(item, bucket_id=bucket_id)
        if proposal is None:
            rejected.append(
                {
                    "reason": reason,
                    "proposal": item if isinstance(item, dict) else str(item),
                }
            )
            continue
        if proposals:
            rejected.append({"reason": "too many proposals", "proposal": proposal})
            continue
        proposals.append(proposal)
    return proposals, rejected


def _anchor_limits(config: Mapping[str, Any]) -> tuple[int, float]:
    raw = config.get("anchor", {})
    anchor_config = raw if isinstance(raw, Mapping) else {}
    max_count = _int_between(anchor_config.get("max_count"), 12, 1, 200)
    try:
        min_age_hours = float(anchor_config.get("min_age_hours", 24))
    except (TypeError, ValueError, OverflowError):
        min_age_hours = 24.0
    return max_count, max(0.0, min_age_hours)


def _bucket_age_hours(bucket: dict[str, Any], now: datetime) -> float | None:
    return age_hours_since(_metadata(bucket).get("created", ""), now)


def _bucket_summary(bucket: dict[str, Any]) -> dict[str, Any]:
    metadata = _metadata(bucket)
    metadata_view = normalize_memory_metadata(bucket)
    return {
        "id": bucket.get("id", ""),
        "name": metadata.get("name", bucket.get("id", "")),
        "type": metadata.get("type", "dynamic"),
        "domain": metadata.get("domain", []),
        "tags": metadata.get("tags", []),
        "facets": metadata.get("facets", []),
        "metadata_view": metadata_view,
        **metadata_view,
        "importance": metadata.get("importance", 5),
        "valence": metadata.get("valence", 0.5),
        "arousal": metadata.get("arousal", 0.5),
        "confidence": metadata.get("confidence", 0.5),
        "pinned": metadata.get("pinned", False),
        "protected": metadata.get("protected", False),
        "anchor": metadata.get("anchor", False),
        "resolved": metadata.get("resolved", False),
        "digested": metadata.get("digested", False),
        "created": metadata.get("created", ""),
        "updated_at": metadata.get("updated_at", ""),
        "last_active": metadata.get("last_active", ""),
        "content_preview": strip_wikilinks(str(bucket.get("content") or ""))[:200],
    }


@dataclass(slots=True)
class CurrentServiceAdapters:
    """Bound callbacks accepted by :class:`CurrentWebServices`."""

    dependencies: CurrentServiceDependencies

    def as_services(self) -> CurrentWebServices:
        return CurrentWebServices(
            inspect_diffusion=self.inspect_diffusion,
            inspect_recall=self.inspect_recall,
            profile_fact_proposals=self.profile_fact_proposals,
            profile_fact_confirm=self.profile_fact_confirm,
            anchor_proposals=self.anchor_proposals,
            anchor_confirm=self.anchor_confirm,
        )

    def _manager_method(self, name: str) -> ServiceOperation | Response:
        manager = self.dependencies.bucket_mgr
        if manager is None:
            return dependency_error("bucket_mgr")
        operation = getattr(manager, name, None)
        if not callable(operation):
            return dependency_error(f"bucket_mgr.{name}")
        return operation

    def _model_name(self, operation: ServiceOperation | None) -> str:
        return str(self.dependencies.model_name or getattr(operation, "model", "") or "")

    def _log_model_failure(self, label: str, exc: Exception) -> None:
        warning = getattr(self.dependencies.logger, "warning", None)
        if callable(warning):
            warning("%s failed: %s", label, exc, exc_info=True)

    async def _all_buckets(self) -> list[dict[str, Any]] | Response:
        list_all = self._manager_method("list_all")
        if isinstance(list_all, Response):
            return list_all
        buckets = await maybe_await(list_all(include_archive=True))
        if not isinstance(buckets, list):
            return dependency_error("bucket_mgr.list_all")
        return [bucket for bucket in buckets if isinstance(bucket, dict)]

    async def _anchor_gate(
        self,
        bucket_id: str,
        bucket: dict[str, Any],
    ) -> tuple[bool, str] | Response:
        max_count, min_age_hours = _anchor_limits(self.dependencies.config)
        age_hours = _bucket_age_hours(bucket, self.dependencies.clock())
        if age_hours is not None and age_hours < min_age_hours:
            return (
                False,
                f"这条记忆还太新，anchor 至少等待 {min_age_hours:g} 小时后再标记。",
            )
        all_buckets = await self._all_buckets()
        if isinstance(all_buckets, Response):
            return all_buckets
        anchor_count = sum(1 for item in all_buckets if item.get("id") != bucket_id and _metadata(item).get("anchor"))
        if anchor_count >= max_count:
            return False, f"anchor 名额已满（{max_count} 条）。请先取消一条旧 anchor。"
        return True, ""

    async def _profile_payload(self, bucket: dict[str, Any]) -> dict[str, Any] | Response:
        get_bucket = self._manager_method("get")
        if isinstance(get_bucket, Response):
            return get_bucket
        return await build_profile_payload(
            bucket,
            get_bucket=get_bucket,
            edge_store=self.dependencies.memory_edge_store,
        )

    async def inspect_diffusion(
        self,
        *,
        query: str,
        max_seeds: int = 3,
        max_hits: int = 5,
        edge_min_confidence: float = 0.55,
    ) -> Any:
        query = str(query or "").strip()
        if not query:
            return {"status": "error", "error": "query_required"}
        operation = self.dependencies.inspect_diffusion_operation
        if not callable(operation):
            return dependency_error("inspect_diffusion_operation")
        return await maybe_await(
            operation(
                query=query,
                max_seeds=_int_between(max_seeds, 3, 1, 20),
                max_hits=_int_between(max_hits, 5, 0, 20),
                edge_min_confidence=_float_between(
                    edge_min_confidence,
                    0.55,
                    0.0,
                    1.0,
                ),
            )
        )

    async def inspect_recall(
        self,
        *,
        query: str,
        max_candidates: int = 20,
        max_results: int = 3,
        max_tokens: int = 800,
        direct_render_mode: str = "auto",
        domain: str = "",
        valence: float | None = None,
        arousal: float | None = None,
    ) -> Any:
        query = str(query or "").strip()
        if not query:
            return {"status": "error", "error": "query_required"}
        operation = self.dependencies.inspect_recall_operation
        if not callable(operation):
            return dependency_error("inspect_recall_operation")
        mode = normalize_direct_render_mode(direct_render_mode)
        q_valence = valence if isinstance(valence, (int, float)) and 0 <= valence <= 1 else None
        q_arousal = arousal if isinstance(arousal, (int, float)) and 0 <= arousal <= 1 else None
        return await maybe_await(
            operation(
                query=query,
                max_candidates=_int_between(max_candidates, 20, 1, 100),
                max_results=_int_between(max_results, 3, 1, 20),
                max_tokens=_int_between(max_tokens, 800, 1, 20000),
                direct_render_mode=mode,
                domain=str(domain or ""),
                valence=q_valence,
                arousal=q_arousal,
            )
        )

    async def profile_fact_proposals(self, body: Mapping[str, Any]) -> Any:
        if not isinstance(body, Mapping):
            return _error("json body must be an object", 400)
        bucket_id = str(body.get("bucket_id") or body.get("evidence_bucket_id") or "").strip()
        if not _valid_id(bucket_id):
            return _error("invalid bucket_id", 400)
        evidence_moment_id = str(body.get("evidence_moment_id") or body.get("moment_id") or "").strip()
        if evidence_moment_id and not _valid_id(evidence_moment_id):
            return _error("invalid evidence_moment_id", 400)

        get_bucket = self._manager_method("get")
        if isinstance(get_bucket, Response):
            return get_bucket
        bucket = await maybe_await(get_bucket(bucket_id))
        if not isinstance(bucket, dict):
            return _error("not found", 404)
        if _is_profile_fact_bucket(bucket):
            return _error("profile_fact bucket cannot be evidence for proposal", 400)
        model = self.dependencies.profile_fact_proposal_model
        max_proposals = _int_between(body.get("max_proposals"), 3, 1, 3)
        try:
            all_buckets = await self._all_buckets()
            if isinstance(all_buckets, Response):
                return all_buckets
            if not callable(model):
                return dependency_error("profile_fact_proposal_model")
            raw = await maybe_await(
                model(
                    bucket=bucket,
                    evidence_moment_id=evidence_moment_id,
                    max_proposals=max_proposals,
                )
            )
            proposals, rejected = _parse_profile_fact_proposals(
                raw,
                evidence_bucket_id=bucket_id,
                evidence_moment_id=evidence_moment_id,
                existing_keys=_existing_profile_fact_keys(all_buckets),
                max_proposals=max_proposals,
            )
        except RuntimeError as exc:
            return _error(str(exc), 503)
        except Exception as exc:
            self._log_model_failure("profile fact proposal", exc)
            return _error(f"proposal failed: {type(exc).__name__}", 502)

        metadata = _metadata(bucket)
        return {
            "status": "ok",
            "evidence": {
                "bucket_id": bucket_id,
                "moment_id": evidence_moment_id,
                "name": metadata.get("name", bucket_id),
            },
            "proposals": proposals,
            "rejected": rejected,
            "model": self._model_name(model),
        }

    async def profile_fact_confirm(self, body: Mapping[str, Any]) -> Any:
        if not isinstance(body, Mapping):
            return _error("json body must be an object", 400)
        evidence_bucket_id = str(body.get("evidence_bucket_id") or "").strip()
        if not _valid_id(evidence_bucket_id):
            return _error("invalid evidence_bucket_id", 400)
        evidence_moment_id = str(body.get("evidence_moment_id") or "").strip()
        if evidence_moment_id and not _valid_id(evidence_moment_id):
            return _error("invalid evidence_moment_id", 400)

        get_bucket = self._manager_method("get")
        if isinstance(get_bucket, Response):
            return get_bucket
        evidence_bucket = await maybe_await(get_bucket(evidence_bucket_id))
        if not isinstance(evidence_bucket, dict):
            return _error("evidence bucket not found", 404)
        all_buckets = await self._all_buckets()
        if isinstance(all_buckets, Response):
            return all_buckets
        proposal, reason = _normalize_profile_fact_proposal(
            body,
            evidence_bucket_id=evidence_bucket_id,
            evidence_moment_id=evidence_moment_id,
            existing_keys=_existing_profile_fact_keys(all_buckets),
        )
        if proposal is None:
            return _error(reason or "invalid proposal", 400)
        writer = self.dependencies.profile_fact_writer
        if not callable(writer):
            return dependency_error("profile_fact_writer")

        result = await maybe_await(
            writer(
                fact=proposal["fact"],
                evidence_bucket_id=proposal["evidence_bucket_id"],
                profile_kind=proposal["profile_kind"],
                subject=proposal["subject"],
                predicate=proposal["predicate"],
                object_value=proposal["object"],
                evidence_moment_id=proposal["evidence_moment_id"],
                evidence_context=proposal["reason"],
                reflection="",
                confidence=proposal["confidence"],
            )
        )
        if not isinstance(result, str) or not result.startswith(PROFILE_FACT_PREFIX):
            return _error(str(result), 400)
        profile_id = result.split(PROFILE_FACT_PREFIX, 1)[1].split(" ", 1)[0]
        created = await maybe_await(get_bucket(profile_id))
        if not isinstance(created, dict):
            return _error("created profile fact not found", 500)
        payload = await self._profile_payload(created)
        if isinstance(payload, Response):
            return payload
        return {
            "status": "created",
            "id": profile_id,
            "result": result,
            "fact": payload,
        }

    async def anchor_proposals(self, body: Mapping[str, Any]) -> Any:
        if not isinstance(body, Mapping):
            return _error("json body must be an object", 400)
        bucket_id = str(body.get("bucket_id") or "").strip()
        if not _valid_id(bucket_id):
            return _error("invalid bucket_id", 400)
        get_bucket = self._manager_method("get")
        if isinstance(get_bucket, Response):
            return get_bucket
        bucket = await maybe_await(get_bucket(bucket_id))
        if not isinstance(bucket, dict):
            return _error("not found", 404)

        model = self.dependencies.anchor_proposal_model
        rejected: list[dict[str, Any]] = []
        static_reason = _anchor_static_rejection(bucket)
        if static_reason:
            rejected.append({"reason": static_reason, "bucket_id": bucket_id})
            return {
                "status": "ok",
                "bucket": _bucket_summary(bucket),
                "proposals": [],
                "rejected": rejected,
                "model": self._model_name(model),
            }
        gate = await self._anchor_gate(bucket_id, bucket)
        if isinstance(gate, Response):
            return gate
        allowed, gate_message = gate
        if not allowed:
            rejected.append({"reason": gate_message, "bucket_id": bucket_id})
            return {
                "status": "ok",
                "bucket": _bucket_summary(bucket),
                "proposals": [],
                "rejected": rejected,
                "model": self._model_name(model),
            }
        if not callable(model):
            return dependency_error("anchor_proposal_model")

        try:
            raw = await maybe_await(model(bucket=bucket))
            proposals, rejected = _parse_anchor_proposals(raw, bucket_id=bucket_id)
        except RuntimeError as exc:
            return _error(str(exc), 503)
        except Exception as exc:
            self._log_model_failure("anchor proposal", exc)
            return _error(f"proposal failed: {type(exc).__name__}", 502)
        summary = _bucket_summary(bucket)
        summary["name"] = _metadata(bucket).get("name", bucket_id)
        return {
            "status": "ok",
            "bucket": summary,
            "proposals": proposals,
            "rejected": rejected,
            "model": self._model_name(model),
        }

    async def anchor_confirm(self, body: Mapping[str, Any]) -> Any:
        if not isinstance(body, Mapping):
            return _error("json body must be an object", 400)
        bucket_id = str(body.get("bucket_id") or "").strip()
        if not _valid_id(bucket_id):
            return _error("invalid bucket_id", 400)
        get_bucket = self._manager_method("get")
        if isinstance(get_bucket, Response):
            return get_bucket
        bucket = await maybe_await(get_bucket(bucket_id))
        if not isinstance(bucket, dict):
            return _error("not found", 404)
        if _metadata(bucket).get("anchor"):
            return {
                "status": "already_anchor",
                "id": bucket_id,
                "bucket": _bucket_summary(bucket),
            }
        static_reason = _anchor_static_rejection(bucket)
        if static_reason:
            return _error(static_reason, 400)
        proposal, reason = _normalize_anchor_proposal(body, bucket_id=bucket_id)
        if proposal is None:
            return _error(reason or "invalid proposal", 400)
        gate = await self._anchor_gate(bucket_id, bucket)
        if isinstance(gate, Response):
            return gate
        allowed, gate_message = gate
        if not allowed:
            return _error(gate_message, 400)
        writer = self.dependencies.anchor_writer
        if not callable(writer):
            return dependency_error("anchor_writer")

        result = await maybe_await(writer(bucket_id=bucket_id, anchor=1))
        if not isinstance(result, str) or not result.startswith(ANCHOR_SUCCESS_PREFIX):
            return _error(str(result), 400)
        updated = await maybe_await(get_bucket(bucket_id))
        return {
            "status": "anchored",
            "id": bucket_id,
            "result": result,
            "proposal": proposal,
            "bucket": _bucket_summary(updated if isinstance(updated, dict) else bucket),
        }


def build_current_services(
    dependencies: CurrentServiceDependencies,
) -> CurrentWebServices:
    """Bind explicit runtime collaborators into the compatibility service contract."""

    return CurrentServiceAdapters(dependencies).as_services()


create_current_services = build_current_services


__all__ = [
    "CurrentServiceAdapters",
    "CurrentServiceDependencies",
    "build_current_services",
    "create_current_services",
]
