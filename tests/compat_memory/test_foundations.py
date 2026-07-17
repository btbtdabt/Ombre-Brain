import pytest

import query_understanding
from favorite_tags import (
    ai_favorite_tag,
    favorite_memory_aliases,
    favorite_policy_tags,
    is_favorite_memory_tag,
)
from identity import generic_identity_names, identity_names, render_identity_template
from query_prompts import QUERY_PLANNER_SYSTEM_PROMPT
from query_terms import date_recall_shell_terms, identity_address_terms
from query_understanding import query_intent_rules, query_intent_terms, query_intent_value
from recall_eval import RECALL_EVAL_BLOCKED_SECTIONS, RECALL_EVAL_DEFAULT_CASES
from self_anchor import is_self_anchor_bucket, is_self_anchor_metadata


def test_identity_and_favorite_helpers_preserve_configured_names_and_aliases():
    names = identity_names(
        {
            "identity": {
                "ai_name": "Echo",
                "user_name": "Mira",
                "human_name": "米拉",
                "user_aliases": "宝宝, 亲爱的",
            }
        }
    )

    assert names["user_display_name"] == "米拉"
    assert names["user_aliases"] == ["宝宝", "亲爱的"]
    assert names["relationship_terms"] == ["Echo", "Mira", "米拉", "宝宝", "亲爱的"]
    assert render_identity_template("{ai_name} remembers {user_display_name}", names) == "Echo remembers 米拉"
    assert generic_identity_names()["ai_name"] == "AI"

    assert ai_favorite_tag("Echo Name") == "echo_name_favorite"
    assert "echo_favorite" in favorite_memory_aliases("Echo")
    assert is_favorite_memory_tag("echo_favorite", "Echo")
    assert not is_favorite_memory_tag("flavor_tender", "Echo")
    assert favorite_policy_tags(["echo_favorite", "flavor_tender", "ordinary"], "Echo") == [
        "echo_favorite",
        "flavor_tender",
    ]


def test_identity_query_terms_include_current_names_without_forcing_legacy_aliases():
    identity = {
        "ai_name": "Echo",
        "user_name": "Mira",
        "user_display_name": "米拉",
        "user_aliases": ["宝宝"],
    }

    current = identity_address_terms(identity)
    legacy = identity_address_terms(identity, include_legacy_ai=True)

    assert {"Echo", "Mira", "米拉", "宝宝"}.issubset(current)
    assert "haven" not in current
    assert "haven" in legacy
    assert set(current).issubset(date_recall_shell_terms(identity))


def test_query_intent_catalog_falls_back_to_the_historical_catalog():
    assert "名字" in query_intent_terms("identity_name.intent_markers")
    assert "还记得" in query_intent_terms("memory_sentinel.explicit_recall_markers")
    assert query_intent_rules("identity_name.search_term_rules")
    assert query_intent_value("missing.path", "fallback") == "fallback"


@pytest.mark.parametrize("payload", [b"{", b"\xff", b"[]"])
def test_query_intent_catalog_falls_back_when_the_packaged_resource_is_invalid(
    tmp_path,
    monkeypatch,
    payload,
):
    resource = tmp_path / "query_intents.json"
    resource.write_bytes(payload)
    monkeypatch.setattr(query_understanding, "DEFAULT_QUERY_INTENTS_PATH", resource)
    query_understanding.query_intent_lexicon.cache_clear()

    try:
        assert "名字" in query_understanding.query_intent_terms("identity_name.intent_markers")
    finally:
        query_understanding.query_intent_lexicon.cache_clear()


def test_query_prompt_and_recall_eval_constants_keep_the_historical_contract():
    assert "Return only strict JSON" in QUERY_PLANNER_SYSTEM_PROMPT
    assert '"should_search": true' in QUERY_PLANNER_SYSTEM_PROMPT
    assert {case["id"] for case in RECALL_EVAL_DEFAULT_CASES} == {
        "light_checkin_no_memory",
        "cuddle_no_memory",
        "laugh_no_memory",
        "ack_no_memory",
        "ping_no_memory",
    }
    assert "Recalled Memory" in RECALL_EVAL_BLOCKED_SECTIONS
    assert "Just Now Chat Context" in RECALL_EVAL_BLOCKED_SECTIONS


def test_self_anchor_requires_explicit_exact_markers():
    assert is_self_anchor_metadata({"self_anchor": True})
    assert is_self_anchor_metadata({"tags": ["self_anchor"]})
    assert is_self_anchor_metadata({"domain": "自我"})
    assert is_self_anchor_metadata({"kind": "first-person-anchor"})
    assert is_self_anchor_bucket({"metadata": {"anchor_kind": "first_person_anchor"}})

    assert not is_self_anchor_metadata({"tags": ["self_identity", "communication_anchor"]})
    assert not is_self_anchor_metadata({"name": "自我表达方式"})
    assert not is_self_anchor_bucket({"metadata": {"tags": ["self_understanding"]}})
