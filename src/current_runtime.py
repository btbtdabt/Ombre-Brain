"""Assembly for current-production collaborators on the P0 runtime base."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from backup_manager import VaultBackupManager
from darkroom import DarkroomStore
from dream_engine import DreamEngine
from entity_edges import EntityEdgeStore, extract_entity_edges_from_bucket
from gateway_state import GatewayStateStore
from identity import identity_names
from identity_semantics import IdentitySemanticStore
from letter_service import LetterService
from memory_diffusion import (
    diffuse_memory,
    diffusion_options_from_config,
    format_diffusion_trace,
    path_has_caution,
    seed_scores_for_buckets,
)
from memory_edges import MemoryEdgeStore
from memory_layers import (
    bucket_layer_debug,
    bucket_runtime_gate_debug,
    can_moment_be_recall_context,
    moment_layer_debug,
    moment_runtime_gate_debug,
)
from memory_moments import MemoryMomentStore
from memory_nodes import MemoryNodeStore
from memory_relevance import (
    memory_relevance_options_from_config,
    recall_rank,
    recall_search_query,
    relevance_decision,
)
from memory_write_gate import MemoryWriteGate
from persona_engine import PersonaStateEngine
from portrait_engine import DailyPortraitMaintainer
from raw_events import RawEventStore
from recall_diagnostics import RecallDiagnosticsLogger
from recall_pipeline import (
    TASK_ONLY_MOMENT_SECTIONS,
    TEMPERATURE_MOMENT_SECTIONS,
    admit_moments,
    apply_topic_evidence_gate,
    append_lexical_matches,
    append_word_map_matches,
    moment_rerank_document,
    recallable_bucket,
    recall_thresholds,
    safe_float as _safe_float,
    seed_diagnostic,
)
from recall_policy import RecallPolicy
from reflection_engine import ReflectionEngine
from reminder_store import ReminderStore
from reranker_engine import RerankerEngine
from runtime_values import (
    float_between as _float_between,
    metadata_dict as _metadata,
    numeric_int_between as _int_between,
)
from self_anchor import is_self_anchor_bucket
from tools.current import memory as current_memory
from utils import bucket_text_for_embedding, strip_wikilinks
from web.current_contract import CurrentWebDependencies, CurrentWebServices, maybe_await
from web.current_services import CurrentServiceDependencies, build_current_services
from word_map import WordMapStore


PROFILE_FACT_PROPOSAL_PROMPT_TEMPLATE = """你是一个证据化用户画像候选生成器。请只根据给定证据桶提出可能值得长期保存的画像事实。

身份：
- 当前用户：{user_display_name}
- 当前 AI：{ai_name}

边界：
1. 只能提出能被证据直接支持的事实，不要补常识，不要推测。
2. 不要提出 root prompt、pinned、protected、Core Memory 更新。
3. 不要把短期情绪当长期画像，除非证据明确显示稳定偏好、边界、习惯、关系锚点或重要日期。
4. 如果证据不足，返回 []。
5. 只输出 JSON 数组，不要 markdown，不要解释。

每个候选必须包含：
{{
  "fact": "一句可读中文事实",
  "profile_kind": "preference|boundary|habit|identity|relationship_anchor|life_fact|work_state|other",
  "subject": "user|ai|relationship",
  "predicate": "snake_case_or_short_key",
  "object": "事实对象，允许中文",
  "evidence_bucket_id": "必须等于给定 bucket id",
  "evidence_moment_id": "可为空",
  "confidence": 0.0,
  "reason": "为什么这条证据足够支撑"
}}

最多返回 3 条。"""


ANCHOR_PROPOSAL_PROMPT_TEMPLATE = """你是一个长期锚点候选生成器。请判断给定记忆桶是否值得被人工标为 anchor。

身份：
- 当前用户：{user_display_name}
- 当前 AI：{ai_name}

边界：
1. 只能判断这个既有 bucket 是否适合作为长期锚点，不要提出新记忆，不要改写正文。
2. 不要建议 pinned、protected、Core Memory 或 profile_fact 更新。
3. anchor 应该是未来长期会反复帮助理解用户、关系、承诺、重要经历或长期项目的记忆。
4. 不要把今天很强烈但未被时间验证的短期情绪当 anchor。
5. 如果不适合，返回 []。
6. 只输出 JSON 数组，不要 markdown，不要解释。

候选格式：
{{
  "bucket_id": "必须等于给定 bucket id",
  "anchor_kind": "relationship|identity|commitment|life_event|project|preference|other",
  "reason": "为什么它适合成为长期锚点",
  "future_use": "以后什么场景需要它",
  "confidence": 0.0
}}

最多返回 1 条。"""


def _active_facet_values(facets: Mapping[str, Any] | None) -> bool:
    for value in (facets or {}).values():
        if isinstance(value, Mapping):
            if any((_safe_float(item, 0.0) or 0.0) > 0 for item in value.values()):
                return True
        elif (_safe_float(value, 0.0) or 0.0) > 0:
            return True
    return False


@dataclass(slots=True)
class RuntimeCollaborators:
    """Own the singleton current-only collaborators attached to one P0 runtime."""

    config: dict[str, Any]
    bucket_mgr: Any
    dehydrator: Any
    decay_engine: Any
    embedding_engine: Any
    embedding_outbox: Any
    import_engine: Any
    logger: Any

    backup_manager: VaultBackupManager = field(init=False)
    reranker_engine: RerankerEngine = field(init=False)
    recall_diagnostics: RecallDiagnosticsLogger = field(init=False)
    persona_engine: PersonaStateEngine = field(init=False)
    memory_edge_store: MemoryEdgeStore = field(init=False)
    entity_edge_store: EntityEdgeStore = field(init=False)
    memory_node_store: MemoryNodeStore = field(init=False)
    memory_moment_store: MemoryMomentStore = field(init=False)
    memory_write_gate: MemoryWriteGate = field(init=False)
    reflection_engine: ReflectionEngine = field(init=False)
    portrait_engine: DailyPortraitMaintainer = field(init=False)
    dream_engine: DreamEngine = field(init=False)
    identity_semantic_store: IdentitySemanticStore = field(init=False)
    word_map_store: WordMapStore = field(init=False)
    darkroom_store: DarkroomStore = field(init=False)
    gateway_state_store: GatewayStateStore = field(init=False)
    raw_event_store: RawEventStore = field(init=False)
    reminder_store: ReminderStore = field(init=False)
    letter_service: LetterService = field(init=False)
    recall_policy: RecallPolicy = field(init=False)
    service_dependencies: CurrentServiceDependencies = field(init=False, repr=False)
    web_services: CurrentWebServices = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.backup_manager = VaultBackupManager(
            self.config,
            self.bucket_mgr,
            self.embedding_engine,
        )
        self.reranker_engine = RerankerEngine(self.config)
        self.recall_diagnostics = RecallDiagnosticsLogger(self.config)
        self.persona_engine = PersonaStateEngine(self.config)
        self.memory_edge_store = MemoryEdgeStore(self.config)
        self.entity_edge_store = EntityEdgeStore(self.config)
        self.memory_node_store = MemoryNodeStore(self.config)
        self.memory_moment_store = MemoryMomentStore(self.config)
        self.memory_write_gate = MemoryWriteGate(self.config)
        self.reflection_engine = ReflectionEngine(self.config)
        self.portrait_engine = DailyPortraitMaintainer(self.config)
        self.dream_engine = DreamEngine(self.config)
        self.identity_semantic_store = IdentitySemanticStore(self.config)
        self.word_map_store = WordMapStore(self.config)
        self.darkroom_store = DarkroomStore(self.config)
        gateway_db = os.path.join(self.config["buckets_dir"], "gateway_state.db")
        self.gateway_state_store = GatewayStateStore(gateway_db)
        self.raw_event_store = RawEventStore(self.config)
        self.reminder_store = ReminderStore(self.config)
        self.letter_service = LetterService(
            self.config,
            self.bucket_mgr,
            self.embedding_engine,
        )
        self.recall_policy = self._build_recall_policy()

        self.service_dependencies = CurrentServiceDependencies(
            config=self.config,
            bucket_mgr=self.bucket_mgr,
            memory_edge_store=self.memory_edge_store,
            inspect_diffusion_operation=self.inspect_diffusion,
            inspect_recall_operation=self.inspect_recall,
            profile_fact_proposal_model=self.profile_fact_proposal_model,
            profile_fact_writer=current_memory.profile_fact,
            anchor_proposal_model=self.anchor_proposal_model,
            anchor_writer=current_memory.trace,
            model_name=str(getattr(self.dehydrator, "model", "") or ""),
            logger=self.logger,
        )
        self.web_services = build_current_services(self.service_dependencies)
        self.web_services.refresh_restore_indexes = self.refresh_restore_indexes

    def _build_recall_policy(self) -> RecallPolicy:
        threshold_config = self.config.get("recall_thresholds", {})
        thresholds = threshold_config if isinstance(threshold_config, Mapping) else {}
        semantic_threshold = _float_between(
            thresholds.get("explicit_admission_semantic_score"),
            0.72,
            0.0,
            1.0,
        )
        rerank_threshold = _float_between(
            thresholds.get("explicit_admission_rerank_score"),
            0.65,
            0.0,
            1.0,
        )
        ai_name = identity_names(self.config).get("ai_name")
        return RecallPolicy(
            memory_relevance_options_from_config(self.config),
            semantic_threshold=semantic_threshold,
            rerank_threshold=rerank_threshold,
            ai_reaction_names=[ai_name] if isinstance(ai_name, str) and ai_name else [],
        )

    def tool_runtime_kwargs(self) -> dict[str, Any]:
        """Return the references consumed by :mod:`tools._runtime`."""
        return {
            "config": self.config,
            "bucket_mgr": self.bucket_mgr,
            "dehydrator": self.dehydrator,
            "decay_engine": self.decay_engine,
            "embedding_engine": self.embedding_engine,
            "embedding_outbox": self.embedding_outbox,
            "import_engine": self.import_engine,
            "logger": self.logger,
            "reminder_store": self.reminder_store,
            "letter_service": self.letter_service,
            "darkroom_store": self.darkroom_store,
            "memory_edge_store": self.memory_edge_store,
            "memory_moment_store": self.memory_moment_store,
            "memory_node_store": self.memory_node_store,
            "entity_edge_store": self.entity_edge_store,
            "memory_write_gate": self.memory_write_gate,
            "recall_policy": self.recall_policy,
            "reranker_engine": self.reranker_engine,
            "recall_diagnostics": self.recall_diagnostics,
            "persona_engine": self.persona_engine,
            "portrait_engine": self.portrait_engine,
            "dream_engine": self.dream_engine,
            "raw_event_store": self.raw_event_store,
            "gateway_state_store": self.gateway_state_store,
            "identity_semantic_store": self.identity_semantic_store,
            "word_map_store": self.word_map_store,
            "reflection_engine": self.reflection_engine,
            "queue_embedding_refresh": self.queue_embedding_refresh,
            "refresh_bucket_indexes": self.refresh_bucket_indexes,
        }

    def web_runtime_kwargs(self) -> dict[str, Any]:
        """Return the references accepted by ``CurrentWebDependencies``."""
        return {
            "config": self.config,
            "bucket_mgr": self.bucket_mgr,
            "decay_engine": self.decay_engine,
            "embedding_engine": self.embedding_engine,
            "embedding_outbox": self.embedding_outbox,
            "backup_manager": self.backup_manager,
            "darkroom_store": self.darkroom_store,
            "dream_engine": self.dream_engine,
            "memory_edge_store": self.memory_edge_store,
            "memory_moment_store": self.memory_moment_store,
            "memory_node_store": self.memory_node_store,
            "entity_edge_store": self.entity_edge_store,
            "identity_semantic_store": self.identity_semantic_store,
            "word_map_store": self.word_map_store,
            "raw_event_store": self.raw_event_store,
            "reminder_store": self.reminder_store,
            "reflection_engine": self.reflection_engine,
            "persona_engine": self.persona_engine,
            "portrait_engine": self.portrait_engine,
            "gateway_state_store": self.gateway_state_store,
            "queue_embedding_refresh": self.queue_embedding_refresh,
            "refresh_bucket_indexes": self.refresh_bucket_indexes,
            "logger": self.logger,
            "services": self.web_services,
        }

    def web_dependencies(self, **overrides: Any) -> CurrentWebDependencies:
        kwargs = self.web_runtime_kwargs()
        kwargs.update(overrides)
        return CurrentWebDependencies(**kwargs)

    async def queue_embedding_refresh(self, bucket_id: str) -> bool:
        bucket_id = str(bucket_id or "").strip()
        embedding_config = self.config.get("embedding", {})
        embedding_enabled = not isinstance(embedding_config, Mapping) or bool(
            embedding_config.get("enabled", True)
        )
        if not bucket_id or not embedding_enabled:
            return False
        bucket = await maybe_await(self.bucket_mgr.get(bucket_id))
        if not bucket:
            await maybe_await(self.embedding_outbox.discard(bucket_id))
            return False
        queued = bool(
            await maybe_await(
                self.embedding_outbox.enqueue(
                    bucket_id,
                    bucket_text_for_embedding(bucket),
                )
            )
        )
        if queued:
            await maybe_await(self.embedding_outbox.ensure_started())
        return queued

    def refresh_bucket_indexes(self, bucket: dict[str, Any]) -> None:
        """Refresh every deterministic per-bucket projection or raise."""
        bucket_id = str(bucket.get("id") or "").strip()
        if not bucket_id:
            raise ValueError("bucket id is required for deterministic index refresh")
        self.memory_moment_store.upsert_bucket(bucket)
        self.memory_node_store.upsert_bucket(bucket)
        entity_edges = (
            []
            if is_self_anchor_bucket(bucket)
            else extract_entity_edges_from_bucket(bucket, identity_names(self.config))
        )
        self.entity_edge_store.replace_bucket_edges(bucket_id, entity_edges)

    async def refresh_restore_indexes(self, bucket_ids: list[str]) -> dict[str, Any]:
        """Clear stale deterministic projections and rebuild restored/global indexes."""
        ordered_ids = list(
            dict.fromkeys(str(bucket_id or "").strip() for bucket_id in bucket_ids)
        )
        ordered_ids = [bucket_id for bucket_id in ordered_ids if bucket_id]
        refreshed = 0
        errors: list[str] = []
        for bucket_id in ordered_ids:
            try:
                # Explicit memory edges are source state, not a deterministic projection.
                self.memory_moment_store.delete_bucket(bucket_id)
                self.memory_node_store.delete(bucket_id)
                self.entity_edge_store.delete_for_bucket(bucket_id)
                bucket = await maybe_await(self.bucket_mgr.get(bucket_id))
                if not isinstance(bucket, dict):
                    raise LookupError(f"restored bucket not found: {bucket_id}")
                self.refresh_bucket_indexes(bucket)
                refreshed += 1
            except Exception as exc:
                errors.append(bucket_id)
                self._warning("Restore index refresh failed for %s: %s", bucket_id, exc)

        try:
            buckets = await maybe_await(self.bucket_mgr.list_all(include_archive=True))
            if not isinstance(buckets, list):
                raise TypeError("bucket_mgr.list_all must return a list")
            valid_buckets = [bucket for bucket in buckets if isinstance(bucket, dict)]
            self.identity_semantic_store.rebuild_alias_index(valid_buckets)
            self.word_map_store.rebuild(
                [bucket for bucket in valid_buckets if not is_self_anchor_bucket(bucket)]
            )
        except Exception as exc:
            errors.append("global_indexes")
            self._warning("Restore global index refresh failed: %s", exc)
        return {"refreshed": refreshed, "errors": errors}

    async def profile_fact_proposal_model(
        self,
        *,
        bucket: dict[str, Any],
        evidence_moment_id: str = "",
        max_proposals: int = 3,
    ) -> str:
        client = getattr(self.dehydrator, "client", None)
        if not getattr(self.dehydrator, "api_available", False) or client is None:
            raise RuntimeError("dehydration API is not configured")
        metadata = _metadata(bucket)
        identity = identity_names(self.config)
        prompt = PROFILE_FACT_PROPOSAL_PROMPT_TEMPLATE.format(
            user_display_name=identity.get("user_display_name")
            or identity.get("user_name")
            or "用户",
            ai_name=identity.get("ai_name") or "AI",
        )
        content = strip_wikilinks(str(bucket.get("content") or ""))
        if evidence_moment_id:
            try:
                moments = self.memory_moment_store.upsert_bucket(bucket)
                selected = next(
                    (
                        moment
                        for moment in moments
                        if str(moment.get("moment_id") or "") == evidence_moment_id
                    ),
                    None,
                )
                if selected:
                    content = str(
                        selected.get("source_window")
                        or selected.get("text")
                        or content
                    )
            except Exception as exc:
                self._warning("Profile fact proposal moment lookup failed: %s", exc)
        evidence_payload = {
            "bucket_id": bucket.get("id", ""),
            "bucket_name": metadata.get("name", bucket.get("id", "")),
            "bucket_tags": metadata.get("tags", []),
            "bucket_domain": metadata.get("domain", []),
            "evidence_moment_id": evidence_moment_id,
            "content": content[:5000],
            "max_proposals": max(1, min(3, int(max_proposals))),
        }
        response = await client.chat.completions.create(
            model=self.dehydrator.model,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(evidence_payload, ensure_ascii=False),
                },
            ],
            **self.dehydrator._completion_options(max_tokens=900, temperature=0.0),
        )
        if not response.choices:
            return "[]"
        return response.choices[0].message.content or "[]"

    async def anchor_proposal_model(self, *, bucket: dict[str, Any]) -> str:
        client = getattr(self.dehydrator, "client", None)
        if not getattr(self.dehydrator, "api_available", False) or client is None:
            raise RuntimeError("dehydration API is not configured")
        metadata = _metadata(bucket)
        identity = identity_names(self.config)
        prompt = ANCHOR_PROPOSAL_PROMPT_TEMPLATE.format(
            user_display_name=identity.get("user_display_name")
            or identity.get("user_name")
            or "用户",
            ai_name=identity.get("ai_name") or "AI",
        )
        evidence_payload = {
            "bucket_id": bucket.get("id", ""),
            "bucket_name": metadata.get("name", bucket.get("id", "")),
            "bucket_type": metadata.get("type", ""),
            "bucket_tags": metadata.get("tags", []),
            "bucket_domain": metadata.get("domain", []),
            "importance": metadata.get("importance"),
            "created": metadata.get("created", ""),
            "updated_at": metadata.get("updated_at", ""),
            "last_active": metadata.get("last_active", ""),
            "content": strip_wikilinks(str(bucket.get("content") or ""))[:5000],
        }
        response = await client.chat.completions.create(
            model=self.dehydrator.model,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(evidence_payload, ensure_ascii=False),
                },
            ],
            **self.dehydrator._completion_options(max_tokens=500, temperature=0.0),
        )
        if not response.choices:
            return "[]"
        return response.choices[0].message.content or "[]"

    async def inspect_diffusion(
        self,
        *,
        query: str,
        max_seeds: int = 3,
        max_hits: int = 5,
        edge_min_confidence: float = 0.55,
    ) -> dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            return {"status": "error", "error": "query_required"}
        max_seeds = _int_between(max_seeds, 3, 1, 20)
        max_hits = _int_between(max_hits, 5, 0, 20)
        edge_min_confidence = _float_between(
            edge_min_confidence,
            0.55,
            0.0,
            1.0,
        )
        node_config = self.config.get("node_facets", {})
        node_facets_enabled = not isinstance(node_config, Mapping) or bool(
            node_config.get("enabled", True)
        )
        query_facets: dict[str, Any] = {}
        warnings: list[str] = []
        if node_facets_enabled:
            try:
                query_facets = self.memory_node_store.facets_for_text(query)
            except Exception as exc:
                warnings.append(f"query_facets_failed: {exc}")

        seed_buckets, seed_warnings = await self._diffusion_seeds(query, max_seeds)
        warnings.extend(seed_warnings)
        if not seed_buckets:
            return {
                "status": "ok",
                "query": query,
                "node_facets_enabled": node_facets_enabled,
                "query_facets": query_facets,
                "seeds": [],
                "hits": [],
                "warnings": warnings,
            }
        try:
            all_buckets = await maybe_await(
                self.bucket_mgr.list_all(include_archive=False)
            )
        except Exception as exc:
            all_buckets = []
            warnings.append(f"list_buckets_failed: {exc}")
        bucket_map = {
            str(bucket.get("id")): bucket
            for bucket in all_buckets
            if isinstance(bucket, dict)
            and bucket.get("id")
            and not is_self_anchor_bucket(bucket)
        }
        for seed in seed_buckets:
            seed_id = str(seed.get("id") or "")
            if seed_id and not is_self_anchor_bucket(seed):
                bucket_map.setdefault(seed_id, seed)

        node_salience = None
        node_resonance = None
        if node_facets_enabled:
            try:
                self.memory_node_store.bulk_upsert(list(bucket_map.values()))
                node_salience = self.memory_node_store.node_salience
                if _active_facet_values(query_facets):
                    def resonance(bucket_id: str, bucket: dict[str, Any]) -> float:
                        return self.memory_node_store.node_resonance(
                            bucket_id,
                            query_facets,
                            bucket,
                        )

                    node_resonance = resonance
            except Exception as exc:
                warnings.append(f"node_refresh_failed: {exc}")

        edges = [
            edge
            for edge in self.memory_edge_store.list_edges()
            if (_safe_float(edge.get("confidence"), 0.0) or 0.0)
            >= edge_min_confidence
        ]
        options = replace(diffusion_options_from_config(self.config), top_k=max_hits)
        seed_scores = seed_scores_for_buckets(seed_buckets)
        hits = diffuse_memory(
            seed_scores,
            edges,
            bucket_map,
            options=options,
            exclude_ids={
                str(bucket.get("id"))
                for bucket in seed_buckets
                if bucket.get("id")
            },
            node_salience=node_salience,
            node_resonance=node_resonance,
            query_text=query,
        )
        explicit_lookup = self.recall_policy.plan_query(query).explicit_old_memory
        seeds = [
            {
                "bucket_id": str(bucket.get("id") or ""),
                "name": self._bucket_label(bucket),
                "source": bucket.get("_inspect_source", "keyword"),
                "seed_score": round(
                    float(seed_scores.get(str(bucket.get("id") or ""), 0.0)),
                    4,
                ),
                "layer_debug": bucket_layer_debug(
                    bucket,
                    explicit_lookup=explicit_lookup,
                ),
                "runtime_gate": self._bucket_runtime_gate(
                    query,
                    bucket,
                    explicit_lookup=explicit_lookup,
                ),
                **self._node_values(
                    str(bucket.get("id") or ""),
                    bucket,
                    node_facets_enabled,
                    query_facets,
                ),
            }
            for bucket in seed_buckets
        ]
        hit_rows = []
        for hit in hits:
            bucket = bucket_map.get(hit.bucket_id)
            hit_rows.append(
                {
                    "bucket_id": hit.bucket_id,
                    "name": self._bucket_label(bucket, hit.bucket_id),
                    "score": hit.activation,
                    "layer_debug": bucket_layer_debug(
                        bucket,
                        explicit_lookup=explicit_lookup,
                    ),
                    "runtime_gate": self._bucket_runtime_gate(
                        query,
                        bucket,
                        explicit_lookup=explicit_lookup,
                    ),
                    **self._node_values(
                        hit.bucket_id,
                        bucket,
                        node_facets_enabled,
                        query_facets,
                    ),
                    "path": format_diffusion_trace(
                        hit.best_path,
                        bucket_map,
                        use_labels=True,
                    ),
                    "path_ids": list(hit.best_path.nodes),
                    "caution": path_has_caution(hit.best_path),
                    "paths": [
                        self._diffusion_path(path, bucket_map) for path in hit.paths
                    ],
                }
            )
        return {
            "status": "ok",
            "query": query,
            "node_facets_enabled": node_facets_enabled,
            "options": {
                "max_hops": options.max_hops,
                "top_k": options.top_k,
                "min_activation": options.min_activation,
                "edge_min_confidence": edge_min_confidence,
                "include_incoming": options.include_incoming,
            },
            "query_facets": query_facets,
            "seeds": seeds,
            "hits": hit_rows,
            "warnings": warnings,
        }

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
    ) -> dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            return {"status": "error", "error": "query_required"}
        max_candidates = _int_between(max_candidates, 20, 1, 100)
        max_results = _int_between(max_results, 3, 1, 20)
        max_tokens = _int_between(max_tokens, 800, 1, 20000)
        render_mode = str(direct_render_mode or "auto").strip().lower()
        if render_mode not in {"auto", "compact", "full"}:
            render_mode = "auto"
        query_valence = valence if isinstance(valence, (int, float)) and 0 <= valence <= 1 else None
        query_arousal = arousal if isinstance(arousal, (int, float)) and 0 <= arousal <= 1 else None
        options = self.recall_policy.options
        search_query = recall_search_query(query, options)
        raw_thresholds = self.config.get("recall_thresholds", {})
        thresholds = recall_thresholds(
            query,
            max_results,
            config=(raw_thresholds if isinstance(raw_thresholds, Mapping) else {}),
            options=options,
            specific_terms=self.recall_policy.specific_query_terms(query),
        )
        warnings: list[str] = []
        search_kwargs: dict[str, Any] = {
            "limit": max(max_candidates, max_results, 20),
            "domain_filter": [
                item.strip() for item in str(domain or "").split(",") if item.strip()
            ],
        }
        if query_valence is not None and query_arousal is not None:
            search_kwargs["query_valence"] = query_valence
            search_kwargs["query_arousal"] = query_arousal
        try:
            raw_matches = await maybe_await(
                self.bucket_mgr.search(search_query, **search_kwargs)
            )
        except Exception as exc:
            return {"status": "error", "error": "search_failed", "message": str(exc)}
        matches = [
            dict(bucket)
            for bucket in raw_matches
            if isinstance(bucket, dict) and self._recallable_bucket(bucket)
        ]
        seed_diagnostics: dict[str, dict[str, Any]] = {}
        for bucket in matches:
            seed_diagnostic(
                seed_diagnostics,
                bucket,
                "keyword",
                bucket_score=bucket.get("score"),
            )
        matched_ids = {str(bucket.get("id") or "") for bucket in matches}
        vector_search = getattr(self.embedding_engine, "search_similar", None)
        if callable(vector_search):
            try:
                vector_results = await maybe_await(
                    vector_search(
                        search_query,
                        top_k=int(thresholds["semantic_top_k"]),
                    )
                )
                for bucket_id, score in vector_results:
                    bucket_id = str(bucket_id)
                    if bucket_id in seed_diagnostics:
                        seed_diagnostics[bucket_id]["embedding_score"] = round(
                            float(score),
                            4,
                        )
                    if (
                        bucket_id in matched_ids
                        or float(score) < float(thresholds["vector_min_score"])
                    ):
                        continue
                    bucket = await maybe_await(self.bucket_mgr.get(bucket_id))
                    if not isinstance(bucket, dict) or not self._recallable_bucket(bucket):
                        continue
                    candidate = dict(bucket)
                    candidate["score"] = round(float(score) * 100, 2)
                    candidate["vector_match"] = True
                    matches.append(candidate)
                    matched_ids.add(bucket_id)
                    seed_diagnostic(
                        seed_diagnostics,
                        candidate,
                        "vector",
                        embedding_score=score,
                    )
            except Exception as exc:
                warnings.append(f"vector_search_failed: {exc}")

        query_plan = self.recall_policy.plan_query(query)
        try:
            listed = await maybe_await(
                self.bucket_mgr.list_all(include_archive=query_plan.explicit_old_memory)
            )
            all_buckets = [bucket for bucket in listed if isinstance(bucket, dict)]
        except Exception as exc:
            warnings.append(f"list_buckets_failed: {exc}")
            all_buckets = list(matches)
        lexical_terms = append_lexical_matches(
            query,
            matches,
            all_buckets,
            seed_diagnostics,
            specific_terms=self.recall_policy.specific_query_terms(query),
            recallable=self._recallable_bucket,
        )
        raw_gateway = self.config.get("gateway", {})
        word_map_scores = append_word_map_matches(
            matches,
            all_buckets,
            seed_diagnostics,
            terms=self.recall_policy.specific_query_terms(query),
            store=self.word_map_store,
            gateway_config=(raw_gateway if isinstance(raw_gateway, Mapping) else {}),
            recallable=self._recallable_bucket,
            warning=self._warning,
        )
        recallable = [bucket for bucket in all_buckets if self._recallable_bucket(bucket)]
        self.memory_moment_store.bulk_upsert(recallable)
        valid_bucket_ids = {str(bucket.get("id") or "") for bucket in recallable}
        moments = [
            moment
            for moment in self.memory_moment_store.list_all()
            if str(moment.get("bucket_id") or "") in valid_bucket_ids
            and can_moment_be_recall_context(moment)
            and str(moment.get("section") or "") not in TASK_ONLY_MOMENT_SECTIONS
        ]
        boosts = seed_scores_for_buckets(matches)
        gateway_config = self.config.get("gateway", {})
        gateway = gateway_config if isinstance(gateway_config, Mapping) else {}
        word_map_boost = _float_between(
            gateway.get("word_map_hint_moment_boost"),
            0.25,
            0.0,
            1.0,
        )
        for bucket_id, score in word_map_scores.items():
            boosts[bucket_id] = max(boosts.get(bucket_id, 0.0), score * word_map_boost)
        candidates = self.memory_moment_store.search_moment_items(
            search_query,
            moments,
            limit=max(max_candidates, max_results, 20),
            bucket_boosts=boosts,
            exclude_sections=TASK_ONLY_MOMENT_SECTIONS,
        )
        pre_gate = candidates[:max_candidates]
        gated = []
        for moment in candidates:
            decision = relevance_decision(query, moment, options)
            if decision.multiplier <= 0:
                continue
            item = dict(moment)
            item["score"] = round(
                (_safe_float(moment.get("score"), 0.0) or 0.0)
                * float(decision.multiplier),
                4,
            )
            gated.append(item)
        gated.sort(key=lambda item: recall_rank(query, item, options))
        reranked = await self._rerank_moments(query, gated)
        admitted, suppressed = admit_moments(
            query,
            reranked,
            seed_diagnostics,
            policy=self.recall_policy,
            query_plan=query_plan,
        )
        returned = admitted[:max_results]
        returned_ids = [
            str(moment.get("moment_id") or "")
            for moment in returned
            if moment.get("moment_id")
        ]
        gated_by_id = self._moment_index(gated)
        reranked_by_id = self._moment_index(reranked)
        suppressed_ids = set(self._moment_index(suppressed))
        gated_rank = self._moment_rank(gated)
        reranked_rank = self._moment_rank(reranked)
        returned_set = set(returned_ids)
        debug_candidates = []
        for index, moment in enumerate(pre_gate):
            moment_id = str(moment.get("moment_id") or "")
            bucket_id = str(moment.get("bucket_id") or "")
            relevance = relevance_decision(query, moment, options)
            gated_item = gated_by_id.get(moment_id)
            final = reranked_by_id.get(moment_id) or gated_item or moment
            seed = seed_diagnostics.get(bucket_id, {})
            admission = self.recall_policy.assess(
                query,
                final,
                query_plan=query_plan,
                has_topic_evidence=self.recall_policy.moment_has_topic_evidence(
                    query,
                    final,
                ),
                semantic_score=_safe_float(seed.get("embedding_score")),
                rerank_score=_safe_float(final.get("rerank_score")),
                high_confidence_edge="lexical" in set(seed.get("sources") or []),
                context_only=str(final.get("section") or "")
                in TEMPERATURE_MOMENT_SECTIONS,
            )
            debug_candidates.append(
                {
                    "pre_rank": index,
                    "gate_rank": gated_rank.get(moment_id),
                    "final_rank": reranked_rank.get(moment_id),
                    "bucket_id": bucket_id,
                    "bucket_name": _metadata(moment).get("bucket_name")
                    or bucket_id,
                    "moment_id": moment_id,
                    "section": moment.get("section"),
                    "sources": seed.get("sources", []),
                    "bucket_search_score": seed.get("bucket_search_score"),
                    "keyword_score": seed.get("keyword_score"),
                    "embedding_score": seed.get("embedding_score"),
                    "score_before_gate": _safe_float(moment.get("score")),
                    "score_after_gate": _safe_float(gated_item.get("score"))
                    if gated_item
                    else None,
                    "rerank_score": _safe_float(final.get("rerank_score")),
                    "combined_score": _safe_float(final.get("combined_score")),
                    "intent_rank": recall_rank(query, final, options)[0],
                    "gate": "filtered" if relevance.multiplier <= 0 else "kept",
                    "gate_multiplier": round(float(relevance.multiplier), 4),
                    "gate_reasons": list(relevance.reasons),
                    "admission": "suppressed"
                    if moment_id in suppressed_ids
                    else "admitted"
                    if admission.admit_direct
                    else "suppressed",
                    "admission_reason": admission.reason,
                    "admission_debug": dict(admission.debug),
                    "selected_returned": moment_id in returned_set,
                    "layer_debug": moment_layer_debug(
                        final,
                        explicit_lookup=query_plan.explicit_old_memory,
                    ),
                    "runtime_gate": moment_runtime_gate_debug(
                        final,
                        explicit_lookup=query_plan.explicit_old_memory,
                    ),
                    "annotation_summary": _metadata(moment).get(
                        "annotation_summary"
                    ),
                    "annotation_facets": _metadata(moment).get(
                        "annotation_facets",
                        {},
                    ),
                    "evidence_spans": _metadata(moment).get("evidence_spans", []),
                    "text_preview": " ".join(
                        str(moment.get("text") or "").split()
                    )[:240],
                }
            )
        return {
            "status": "ok",
            "query": query,
            "search_query": search_query,
            "recall_thresholds": {
                **thresholds,
                "max_tokens": max_tokens,
                "direct_render_mode": render_mode,
                "lexical_terms": lexical_terms,
                "word_map_hint_bucket_ids": sorted(word_map_scores),
            },
            "seed_buckets": list(seed_diagnostics.values())[:max_candidates],
            "candidate_count": len(pre_gate),
            "admitted_count": len(admitted),
            "suppressed_count": len(suppressed),
            "returned_moment_ids": returned_ids,
            "candidates": debug_candidates,
            "warnings": warnings,
        }

    async def _diffusion_seeds(
        self,
        query: str,
        max_seeds: int,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        matches: list[dict[str, Any]] = []
        matched_ids: set[str] = set()
        options = self.recall_policy.options

        def add_candidate(bucket: dict[str, Any], source: str, score: float | None = None) -> None:
            bucket_id = str(bucket.get("id") or "")
            if not bucket_id or bucket_id in matched_ids:
                return
            decision = relevance_decision(query, bucket, options)
            if decision.suppress:
                return
            candidate = dict(bucket)
            metadata = _metadata(candidate)
            base_score = _safe_float(score)
            if base_score is None:
                base_score = _safe_float(candidate.get("score"))
            if base_score is None:
                base_score = _safe_float(metadata.get("score"))
            if base_score is None:
                base_score = _safe_float(metadata.get("importance"), 0.0)
            candidate["score"] = round(
                float(base_score or 0.0) * float(decision.multiplier),
                4,
            )
            candidate["_inspect_source"] = source
            candidate["_inspect_relevance_reasons"] = list(decision.reasons)
            candidate["_inspect_relevance_multiplier"] = round(
                float(decision.multiplier),
                4,
            )
            matches.append(candidate)
            matched_ids.add(bucket_id)

        search_limit = max(max_seeds, 20)
        try:
            found = await maybe_await(self.bucket_mgr.search(query, limit=search_limit))
            for bucket in found:
                if not isinstance(bucket, dict) or _metadata(bucket).get("type") == "feel":
                    continue
                add_candidate(bucket, "keyword")
        except Exception as exc:
            warnings.append(f"keyword_search_failed: {exc}")
        vector_search = getattr(self.embedding_engine, "search_similar", None)
        if callable(vector_search):
            try:
                for bucket_id, similarity in await maybe_await(
                    vector_search(query, top_k=search_limit)
                ):
                    bucket_id = str(bucket_id)
                    if bucket_id in matched_ids or float(similarity) <= 0.5:
                        continue
                    bucket = await maybe_await(self.bucket_mgr.get(bucket_id))
                    if not isinstance(bucket, dict) or _metadata(bucket).get("type") == "feel":
                        continue
                    add_candidate(
                        bucket,
                        "vector",
                        round(float(similarity) * 100, 2),
                    )
                    if matches and str(matches[-1].get("id") or "") == bucket_id:
                        matches[-1]["vector_match"] = True
            except Exception as exc:
                warnings.append(f"vector_search_failed: {exc}")
        matches.sort(
            key=lambda bucket: (
                recall_rank(query, bucket, options)[0],
                -(_safe_float(bucket.get("score"), 0.0) or 0.0),
            )
        )
        return matches[:max_seeds], warnings

    def _node_values(
        self,
        bucket_id: str,
        bucket: dict[str, Any] | None,
        enabled: bool,
        query_facets: dict[str, Any],
    ) -> dict[str, Any]:
        if not bucket or not enabled:
            return {"salience": None, "resonance": None, "facets": {}}
        try:
            node = self.memory_node_store.get(bucket_id) or self.memory_node_store.upsert_bucket(
                bucket
            )
            return {
                "salience": round(
                    float(self.memory_node_store.node_salience(bucket_id, bucket)),
                    4,
                ),
                "resonance": round(
                    float(
                        self.memory_node_store.node_resonance(
                            bucket_id,
                            query_facets,
                            bucket,
                        )
                    ),
                    4,
                ),
                "facets": node.get("facets", {}),
            }
        except Exception as exc:
            return {
                "salience": None,
                "resonance": None,
                "facets": {},
                "error": str(exc),
            }

    def _bucket_runtime_gate(
        self,
        query: str,
        bucket: dict[str, Any] | None,
        *,
        explicit_lookup: bool,
    ) -> dict[str, Any]:
        gate = bucket_runtime_gate_debug(bucket, explicit_lookup=explicit_lookup)
        query_plan = self.recall_policy.plan_query(query)
        topic_required = bool(query_plan.enforce_topic_evidence)
        topic_present = (
            self.recall_policy.bucket_has_topic_evidence(query, bucket)
            if topic_required and isinstance(bucket, dict)
            else False
        )
        return apply_topic_evidence_gate(
            gate,
            source_key="related_target",
            injection_key="related_injection",
            would_inject_key="would_inject_related",
            topic_required=topic_required,
            topic_present=topic_present,
        )

    @staticmethod
    def _diffusion_path(path: Any, bucket_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            "score": round(float(path.score), 4),
            "trace": format_diffusion_trace(path, bucket_map, use_labels=True),
            "nodes": list(path.nodes),
            "steps": [
                {
                    "source": step.source,
                    "target": step.target,
                    "direction": step.direction,
                    "relation_type": step.relation_type,
                    "confidence": step.confidence,
                    "reason": step.reason,
                }
                for step in path.steps
            ],
        }

    @staticmethod
    def _bucket_label(
        bucket: dict[str, Any] | None,
        bucket_id: str = "",
    ) -> str:
        if not bucket:
            return bucket_id
        metadata = _metadata(bucket)
        return str(metadata.get("name") or bucket.get("name") or bucket_id or bucket.get("id") or "")

    async def _rerank_moments(
        self,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not candidates or not self.reranker_engine.enabled:
            return candidates
        limit = min(
            len(candidates),
            max(1, int(self.reranker_engine.candidate_limit or 20)),
        )
        head, tail = candidates[:limit], candidates[limit:]
        documents = [moment_rerank_document(moment) for moment in head]
        try:
            results = await maybe_await(
                self.reranker_engine.rerank(
                    query,
                    documents,
                    top_n=len(head),
                )
            )
        except Exception as exc:
            self._warning("Recall debug reranker failed: %s", exc)
            return candidates
        by_index: dict[int, float] = {}
        for result in results or []:
            if isinstance(result, Mapping):
                index, score = result.get("index"), result.get("score")
            else:
                index = getattr(result, "index", None)
                score = getattr(result, "score", None)
            if index is not None and score is not None:
                by_index[int(index)] = float(score)
        weight = _float_between(self.reranker_engine.score_weight, 0.65, 0.0, 1.0)
        reranked = []
        for index, moment in enumerate(head):
            item = dict(moment)
            base_score = _safe_float(item.get("score"), 0.0) or 0.0
            rerank_score = by_index.get(index)
            if rerank_score is None:
                item["rerank_score"] = None
                item["combined_score"] = base_score
            else:
                item["rerank_score"] = round(rerank_score, 4)
                item["combined_score"] = round(
                    base_score * (1.0 - weight) + rerank_score * weight,
                    4,
                )
                item["score"] = item["combined_score"]
            reranked.append(item)
        reranked.sort(
            key=lambda item: (
                recall_rank(query, item, self.recall_policy.options)[0],
                item.get("rerank_score") is None,
                -(_safe_float(item.get("combined_score"), 0.0) or 0.0),
            )
        )
        return reranked + tail

    @staticmethod
    def _moment_index(moments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            str(moment.get("moment_id") or ""): moment
            for moment in moments
            if moment.get("moment_id")
        }

    @staticmethod
    def _moment_rank(moments: list[dict[str, Any]]) -> dict[str, int]:
        return {
            str(moment.get("moment_id") or ""): index
            for index, moment in enumerate(moments)
            if moment.get("moment_id")
        }

    _recallable_bucket = staticmethod(recallable_bucket)

    def _warning(self, message: str, *args: Any) -> None:
        warning = getattr(self.logger, "warning", None)
        if callable(warning):
            warning(message, *args)


__all__ = [
    "ANCHOR_PROPOSAL_PROMPT_TEMPLATE",
    "PROFILE_FACT_PROPOSAL_PROMPT_TEMPLATE",
    "RuntimeCollaborators",
]
