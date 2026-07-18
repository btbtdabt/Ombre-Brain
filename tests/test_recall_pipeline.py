from __future__ import annotations

from types import SimpleNamespace

from memory_relevance import MemoryRelevanceOptions
from recall_pipeline import (
    apply_topic_evidence_gate,
    append_lexical_matches,
    append_word_map_matches,
    dehydration_metadata,
    group_moments_by_bucket,
    is_unpinned_anchor_candidate,
    recallable_bucket,
    recall_thresholds,
    seed_diagnostic,
    source_record_fragment_window,
    trim_to_token_budget,
)


def test_shared_topic_gate_and_source_fragment_primitives() -> None:
    gate = {"related_target": {"allowed": True, "reason": "related"}}
    assert apply_topic_evidence_gate(
        gate,
        source_key="related_target",
        injection_key="related_injection",
        would_inject_key="would_inject_related",
        topic_required=True,
        topic_present=False,
    )["related_injection"] == {
        "allowed": False,
        "reason": "query_topic_evidence_missing",
    }

    fragment, before, after = source_record_fragment_window(
        "prefix 012345 target 678901 suffix",
        ["target"],
        10,
    )
    assert fragment == "2345 target 6789"
    assert before is True
    assert after is True


def test_unpinned_anchor_candidate_keeps_anchor_policy_canonical() -> None:
    assert is_unpinned_anchor_candidate({"metadata": {"anchor": True}})
    assert not is_unpinned_anchor_candidate(
        {"metadata": {"anchor": True, "pinned": True}}
    )


def test_moment_grouping_and_token_trimming_are_shared() -> None:
    grouped = group_moments_by_bucket(
        [
            {"bucket_id": "a", "ordinal": 2},
            {"bucket_id": "a", "ordinal": 1},
            {"bucket_id": "", "ordinal": 0},
        ]
    )

    assert [item["ordinal"] for item in grouped["a"]] == [1, 2]
    assert trim_to_token_budget("", 10) == ""
    assert trim_to_token_budget("text", 0) == ""


def test_dehydration_metadata_drops_large_structural_fields() -> None:
    assert dehydration_metadata(
        {"metadata": {"domain": "life", "tags": ["a"], "comments": [{"id": "c1"}]}}
    ) == {"domain": "life"}


def test_recallable_bucket_excludes_self_anchors_and_daily_impressions() -> None:
    assert recallable_bucket({"id": "regular", "metadata": {}})
    assert not recallable_bucket({"id": "anchor", "metadata": {"self_anchor": True}})
    assert not recallable_bucket(
        {
            "id": "daily",
            "metadata": {"type": "feel", "tags": ["daily_impression"]},
        }
    )


def _recallable(_bucket: dict) -> bool:
    return True


def test_recall_thresholds_preserve_explicit_and_vague_profiles() -> None:
    options = MemoryRelevanceOptions()
    config = {
        "explicit_vector_min_score": 0.61,
        "vague_vector_min_score": 0.32,
        "vague_top_k": 73,
    }

    explicit = recall_thresholds(
        "[bucket_id:abc123]",
        3,
        config=config,
        options=options,
        specific_terms=("abc123",),
    )
    vague = recall_thresholds(
        "之前的事情",
        3,
        config=config,
        options=options,
        specific_terms=(),
    )

    assert explicit["profile"] == "explicit"
    assert explicit["vector_min_score"] == 0.61
    assert vague["profile"] == "vague"
    assert vague["vector_min_score"] == 0.32
    assert vague["semantic_top_k"] == 73


def test_lexical_and_word_map_candidates_share_diagnostics_contract() -> None:
    lexical = {
        "id": "lexical-1",
        "content": "Amy likes quiet mornings.",
        "metadata": {"name": "Morning preference", "tags": ["preference"]},
    }
    mapped = {
        "id": "mapped-1",
        "content": "A related routine.",
        "metadata": {"name": "Routine"},
    }
    matches: list[dict] = []
    diagnostics: dict[str, dict] = {}

    terms = append_lexical_matches(
        "quiet mornings",
        matches,
        [lexical, mapped],
        diagnostics,
        specific_terms=("quiet", "mornings"),
        recallable=_recallable,
    )

    store = SimpleNamespace(
        enabled=True,
        hint_buckets_for_terms=lambda _terms, **_kwargs: {
            "bucket_scores": {"mapped-1": 0.82},
            "evidence": {
                "mapped-1": {
                    "direct_terms": ["routine"],
                    "neighbor_terms": ["morning"],
                }
            },
        },
    )
    scores = append_word_map_matches(
        matches,
        [lexical, mapped],
        diagnostics,
        terms=terms,
        store=store,
        gateway_config={
            "word_map_hint_enabled": True,
            "word_map_hint_neighbor_limit": 6,
            "word_map_hint_bucket_limit": 12,
        },
        recallable=_recallable,
    )

    assert terms == ["quiet", "mornings"]
    assert {bucket["id"] for bucket in matches} == {"lexical-1", "mapped-1"}
    assert scores == {"mapped-1": 0.82}
    assert diagnostics["lexical-1"]["sources"] == ["lexical"]
    assert diagnostics["mapped-1"] == {
        "bucket_id": "mapped-1",
        "bucket_name": "Routine",
        "sources": ["word_map"],
        "bucket_search_score": 0.82,
        "keyword_score": 0.82,
        "word_map_score": 0.82,
        "word_map_terms": ["routine"],
        "word_map_neighbor_terms": ["morning"],
    }


def test_seed_diagnostic_merges_sources_without_duplicates() -> None:
    diagnostics: dict[str, dict] = {}
    bucket = {"id": "memory-1", "metadata": {"name": "Memory"}}

    seed_diagnostic(diagnostics, bucket, "keyword", bucket_score=78)
    seed_diagnostic(diagnostics, bucket, "keyword", embedding_score=0.72)
    seed_diagnostic(diagnostics, bucket, "vector", embedding_score=0.73)

    assert diagnostics["memory-1"] == {
        "bucket_id": "memory-1",
        "bucket_name": "Memory",
        "sources": ["keyword", "vector"],
        "bucket_search_score": 78.0,
        "keyword_score": 0.78,
        "embedding_score": 0.73,
    }
