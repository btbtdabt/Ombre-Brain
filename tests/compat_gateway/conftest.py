import importlib
import sys
from types import ModuleType

import pytest


class _Dependency:
    enabled = False

    def __init__(self, *args, **kwargs):
        pass


class _RerankResult:
    def __init__(self, index: int = 0, score: float = 0.0):
        self.index = index
        self.score = score


def _empty_terms(*args, **kwargs):
    return ()


def _empty_set(*args, **kwargs):
    return frozenset()


def _false(*args, **kwargs):
    return False


def _none(*args, **kwargs):
    return None


def _module(name: str, **attributes) -> ModuleType:
    module = ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


@pytest.fixture
def gateway_module(monkeypatch):
    dependency_modules = {
        "bucket_manager": _module("bucket_manager", BucketManager=_Dependency),
        "dehydrator": _module("dehydrator", Dehydrator=_Dependency),
        "dream_engine": _module("dream_engine", DreamEngine=_Dependency),
        "embedding_engine": _module("embedding_engine", EmbeddingEngine=_Dependency),
        "favorite_tags": _module(
            "favorite_tags",
            has_favorite_memory_tag=_false,
            is_flavor_tag=_false,
        ),
        "identity": _module(
            "identity",
            identity_names=lambda config: {"ai_name": "Ombre", "user_name": "User"},
        ),
        "memory_diffusion": _module(
            "memory_diffusion",
            diffuse_memory=_empty_terms,
            diffusion_options_from_config=lambda config: {},
            path_has_caution=_false,
            path_has_old_version=_false,
            seed_scores_for_buckets=lambda *args, **kwargs: {},
            should_suppress_context_candidate=_false,
        ),
        "memory_edges": _module("memory_edges", MemoryEdgeStore=_Dependency),
        "entity_edges": _module("entity_edges", EntityEdgeStore=_Dependency),
        "memory_moments": _module(
            "memory_moments",
            MemoryMomentStore=_Dependency,
            parse_bucket_moments=_empty_terms,
            preview_bucket_moment_chunks=_empty_terms,
        ),
        "memory_relevance": _module(
            "memory_relevance",
            active_facets=lambda *args, **kwargs: {},
            content_terms_for_query=_empty_terms,
            emotional_recall_plan=lambda *args, **kwargs: {},
            expanded_terms_for_query=_empty_terms,
            extract_protected_phrases=_empty_terms,
            facets_for_node=lambda *args, **kwargs: {},
            facets_for_text=lambda *args, **kwargs: {},
            memory_relevance_options_from_config=lambda config: {},
            query_has_facet=_false,
            recall_rank=lambda *args, **kwargs: (0, 0.0),
            recall_topic_query=lambda query: query,
            relevance_multiplier=lambda *args, **kwargs: 1.0,
        ),
        "query_prompts": _module(
            "query_prompts",
            QUERY_PLANNER_SYSTEM_PROMPT="",
        ),
        "query_understanding": _module(
            "query_understanding",
            query_intent_rules=lambda *args, **kwargs: {},
            query_intent_term_set=_empty_set,
            query_intent_terms=_empty_terms,
        ),
        "memory_layers": _module(
            "memory_layers",
            CONTEXT_ONLY_SECTIONS={"comment", "followup", "followup_log"},
            LAYER_SOURCE_RECORD="source_record",
            bucket_layer_debug=lambda *args, **kwargs: {},
            bucket_runtime_gate_debug=lambda *args, **kwargs: {},
            can_bucket_be_recent_context=lambda *args, **kwargs: True,
            can_bucket_be_related_target=lambda *args, **kwargs: True,
            can_moment_be_direct_seed=lambda *args, **kwargs: True,
            can_moment_be_recall_context=lambda *args, **kwargs: True,
            can_moment_be_related_target=lambda *args, **kwargs: True,
            infer_bucket_layer=lambda *args, **kwargs: "",
            moment_layer_debug=lambda *args, **kwargs: {},
            moment_runtime_gate_debug=lambda *args, **kwargs: {},
        ),
        "memory_metadata": _module(
            "memory_metadata",
            normalize_domain_key=lambda value: str(value or ""),
            normalize_memory_metadata=lambda value: value if isinstance(value, dict) else {},
        ),
        "query_terms": _module(
            "query_terms",
            CHECKIN_TRAILING_PARTICLES=(),
            DEFAULT_AI_ADDRESS_TERMS=(),
            GENERIC_LEXICAL_STOPWORDS=(),
            LEADING_LOOKUP_ADDRESS_FOLLOWUPS=(),
            LEADING_LOOKUP_REASON_MARKERS=(),
            LOW_SIGNAL_AFFECTION_TERMS=(),
            LOW_SIGNAL_CHECKIN_TERMS=(),
            MEMORY_SENTINEL_RESIDUE_STRIP_TERMS=(),
            QUERY_PLANNER_GENERIC_TERMS=(),
            SOURCE_RECORD_FRAGMENT_TOPIC_STOPWORDS=(),
            date_recall_shell_terms=_empty_terms,
            identity_address_terms=_empty_terms,
        ),
        "recall_eval": _module(
            "recall_eval",
            RECALL_EVAL_BLOCKED_SECTIONS=(),
            RECALL_EVAL_DEFAULT_CASES=(),
        ),
        "recall_policy": _module(
            "recall_policy",
            QueryAnchorPlan=_Dependency,
            RecallPolicy=_Dependency,
            diffusion_seed_topic_term_has_specific_residue=_false,
        ),
        "memory_nodes": _module("memory_nodes", MemoryNodeStore=_Dependency),
        "persona_engine": _module("persona_engine", PersonaStateEngine=_Dependency),
        "persona_event_selection": _module(
            "persona_event_selection",
            format_persona_event_trace_line=lambda *args, **kwargs: "",
            select_persona_events=_empty_terms,
        ),
        "raw_events": _module(
            "raw_events",
            RawEventStore=_Dependency,
            raw_event_text_looks_injected=_false,
            strip_raw_client_context=lambda value: value,
        ),
        "reminder_store": _module("reminder_store", ReminderStore=_Dependency),
        "reranker_engine": _module(
            "reranker_engine",
            RerankerEngine=_Dependency,
            RerankResult=_RerankResult,
        ),
        "self_anchor": _module(
            "self_anchor",
            is_self_anchor_bucket=_false,
            is_self_anchor_metadata=_false,
        ),
        "source_refs": _module("source_refs", source_ref_window=_none),
        "utils": _module(
            "utils",
            count_tokens_approx=lambda text: len(str(text or "")) // 4,
            bucket_content_for_recall=lambda bucket: str(bucket.get("content") or ""),
            bucket_text_for_embedding=lambda bucket: str(bucket.get("content") or ""),
            local_date_key=lambda value: "",
            load_config=lambda: {},
            parse_human_date_reference=_none,
            setup_logging=_none,
            strip_human_date_references=lambda value: value,
            strip_display_temperature_sections=lambda value: value,
            strip_followup_sections=lambda value: value,
            strip_temperature_meaning_lines=lambda value: value,
            strip_wikilinks=lambda value: value,
        ),
        "word_map": _module("word_map", WordMapStore=_Dependency),
    }
    for name, module in dependency_modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    sys.modules.pop("gateway", None)
    module = importlib.import_module("gateway")
    yield module
    sys.modules.pop("gateway", None)

