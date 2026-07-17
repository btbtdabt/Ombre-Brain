from __future__ import annotations

import pytest

from dehydrator import (
    ANALYZE_PROMPT,
    DIGEST_PROMPT_TEMPLATE,
    DIRECT_BUCKET_CAPSULE_PROMPT,
    Dehydrator,
)


def _config(tmp_path, **dehydration):
    buckets_dir = tmp_path / "buckets"
    buckets_dir.mkdir(parents=True)
    return {
        "buckets_dir": str(buckets_dir),
        "dehydration": {"api_key": "", **dehydration},
        "identity": {
            "ai_name": "Ombre",
            "user_display_name": "Amy",
            "user_aliases": ["A"],
        },
    }


def test_prompts_keep_the_strict_production_json_contract():
    assert "输出格式（必须按照此格式输出）" in ANALYZE_PROMPT
    assert "输出必须是一个合法 JSON object。" in ANALYZE_PROMPT
    assert "输出格式（必须按照此格式输出）" in DIGEST_PROMPT_TEMPLATE
    assert "输出必须是一个合法 JSON array。" in DIGEST_PROMPT_TEMPLATE


def test_missing_openai_client_fails_explicitly(tmp_path):
    dehydrator = Dehydrator(_config(tmp_path))

    with pytest.raises(RuntimeError, match="API"):
        dehydrator._require_client()


def test_analysis_parsing_normalizes_domains_classification_and_reserved_tags(tmp_path):
    dehydrator = Dehydrator(_config(tmp_path))

    parsed = dehydrator._parse_analysis(
        "prefix\n"
        '{"domain":["饮食"],"valence":0.8,"arousal":0.4,'
        '"tags":["海鲜","self_anchor","海鲜"],"suggested_name":"偏好",'
        '"memory_subject":"user","memory_layer":"stable_boundary"}'
        "\ntrailing explanation"
    )

    assert parsed["domain"] == ["life"]
    assert parsed["tags"] == ["海鲜"]
    assert parsed["memory_subject"] == "user"
    assert parsed["memory_layer"] == "stable_boundary"
    assert parsed["memory_classification_source"] == "model"


def test_cache_isolated_by_processing_purpose_and_invalidated_together(tmp_path):
    dehydrator = Dehydrator(_config(tmp_path))
    dehydrator._set_cached_summary("same", "summary", purpose="dehydrate")
    dehydrator._set_cached_summary("same", "capsule", purpose="direct_capsule")

    assert dehydrator._get_cached_summary("same", purpose="dehydrate") == "summary"
    assert dehydrator._get_cached_summary("same", purpose="direct_capsule") == "capsule"

    dehydrator.invalidate_cache("same")
    assert dehydrator._get_cached_summary("same", purpose="dehydrate") is None
    assert dehydrator._get_cached_summary("same", purpose="direct_capsule") is None


def test_gemini_thinking_budget_changes_cache_identity(tmp_path):
    first = Dehydrator(
        _config(
            tmp_path / "first",
            api_format="gemini",
            model="gemini-test",
            thinking_budget=0,
        )
    )
    second = Dehydrator(
        _config(
            tmp_path / "second",
            api_format="gemini",
            model="gemini-test",
            thinking_budget=128,
        )
    )

    assert first._cache_key("same") != second._cache_key("same")


@pytest.mark.asyncio
async def test_direct_capsule_uses_shared_provider_path_and_identity_safe_prompt(
    tmp_path, monkeypatch
):
    dehydrator = Dehydrator(_config(tmp_path, api_key="configured", api_format="gemini"))
    calls = []

    async def fake_chat(system, user, **options):
        calls.append((system, user, options))
        return "high-density capsule"

    monkeypatch.setattr(dehydrator, "_chat", fake_chat)

    result = await dehydrator.dehydrate_direct_capsule("long source memory")

    assert result == "high-density capsule"
    assert calls == [(DIRECT_BUCKET_CAPSULE_PROMPT, "long source memory", {})]
    assert await dehydrator.dehydrate_direct_capsule("long source memory") == result
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_generate_moment_keeps_short_body_and_native_provider_behavior(
    tmp_path, monkeypatch
):
    unavailable = Dehydrator(_config(tmp_path / "unavailable"))
    assert await unavailable.generate_moment("too short") == ""

    configured = Dehydrator(
        _config(tmp_path / "configured", api_key="configured", api_format="anthropic")
    )
    calls = []

    async def fake_chat(system, user, **options):
        calls.append((system, user, options))
        return '"Amy完成了处理迁移。"'

    monkeypatch.setattr(configured, "_chat", fake_chat)

    assert await configured.generate_moment("This source body is long enough.") == "Amy完成了处理迁移。"
    assert calls[0][2] == {"max_tokens": 64, "temperature": 0.0}
