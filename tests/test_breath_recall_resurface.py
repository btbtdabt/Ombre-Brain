from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.current import breath_recall


class _FakePolicy:
    def plan_query(self, _query: str):
        return SimpleNamespace(
            explicit_old_memory=False,
            enforce_topic_evidence=False,
            secondary_direct_limit=lambda _limit: _limit,
            secondary_direct_requires_topic_evidence=False,
        )

    def assess(self, *_args, **_kwargs):
        return SimpleNamespace(admit_direct=True, reason="ok")

    def bucket_has_topic_evidence(self, *_args, **_kwargs):
        return True

    def moment_has_topic_evidence(self, *_args, **_kwargs):
        return True

    def is_auto_query_too_vague(self, _query: str) -> bool:
        return False


@pytest.mark.asyncio
async def test_search_breath_respects_query_resurface_flag(monkeypatch: pytest.MonkeyPatch):
    primary = {
        "id": "bucket-primary",
        "content": "direct result",
        "metadata": {"name": "Primary memory", "type": "dynamic", "domain": ["work"]},
    }
    resurfaced = {
        "id": "bucket-resurface",
        "content": "old low-signal memory",
        "metadata": {"name": "Resurfaced memory", "type": "dynamic", "domain": ["work"]},
    }
    diagnostics = {
        "bucket-primary": {"sources": {"keyword"}},
    }

    async def fake_collect_search_materials(**_kwargs):
        return (
            "lookup query",
            [primary],
            [primary, resurfaced],
            diagnostics,
            {"has_explicit_entity": False},
            [],
            {},
        )

    monkeypatch.setattr(
        breath_recall,
        "_collect_search_materials",
        fake_collect_search_materials,
    )
    monkeypatch.setattr(breath_recall, "_policy", lambda: _FakePolicy())
    monkeypatch.setattr(
        breath_recall,
        "_direct_moments_for_bucket",
        lambda bucket, _query: [{"bucket_id": bucket["id"], "moment_id": f"{bucket['id']}:m1"}],
    )
    monkeypatch.setattr(
        breath_recall,
        "_representative_moment",
        lambda moments: moments[0] if moments else None,
    )
    async def fake_format_direct_bucket(bucket, *_args, **_kwargs):
        return f"[bucket_id:{bucket['id']}] direct"

    monkeypatch.setattr(
        breath_recall,
        "_format_direct_bucket",
        fake_format_direct_bucket,
    )
    async def fake_dream_overlay(**_kwargs):
        return ""

    monkeypatch.setattr(breath_recall, "_dream_overlay", fake_dream_overlay)
    monkeypatch.setattr(breath_recall, "_write_diagnostics", lambda **_kwargs: None)
    monkeypatch.setattr(breath_recall, "_warning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(breath_recall.random, "random", lambda: 0.0)
    monkeypatch.setattr(
        breath_recall.rt,
        "bucket_mgr",
        SimpleNamespace(touch=lambda _bucket_id: None),
        raising=False,
    )
    monkeypatch.setattr(
        breath_recall.rt,
        "config",
        {
            "recall": {"query_resurface_enabled": True},
            "gateway": {"word_map_hint_enabled": False},
        },
        raising=False,
    )
    monkeypatch.setattr(
        breath_recall.rt,
        "dehydrator",
        None,
        raising=False,
    )

    enabled = await breath_recall.search_breath(
        query="lookup query",
        max_tokens=4000,
        domain="work",
        valence=-1,
        arousal=-1,
        max_results=3,
        include_related=False,
        related_per_memory=0,
        edge_min_confidence=0.0,
        is_session_start=False,
        debug=False,
        auto_surface=False,
        direct_render_mode="auto",
        retrieval_mode="bucket",
    )

    assert "[surface_type: resurface] [bucket_id:bucket-resurface]" in enabled

    breath_recall.rt.config["recall"]["query_resurface_enabled"] = False

    disabled = await breath_recall.search_breath(
        query="lookup query",
        max_tokens=4000,
        domain="work",
        valence=-1,
        arousal=-1,
        max_results=3,
        include_related=False,
        related_per_memory=0,
        edge_min_confidence=0.0,
        is_session_start=False,
        debug=False,
        auto_surface=False,
        direct_render_mode="auto",
        retrieval_mode="bucket",
    )

    assert "[surface_type: resurface]" not in disabled


@pytest.mark.asyncio
async def test_search_breath_resurface_keeps_requested_domain(monkeypatch: pytest.MonkeyPatch):
    primary = {
        "id": "bucket-primary",
        "content": "direct result",
        "metadata": {"name": "Primary memory", "type": "dynamic", "domain": ["work"]},
    }
    blocked = {
        "id": "bucket-resurface",
        "content": "old low-signal memory",
        "metadata": {"name": "Blocked memory", "type": "dynamic", "domain": ["private"]},
    }

    async def fake_collect_search_materials(**_kwargs):
        return (
            "lookup query",
            [primary],
            [primary, blocked],
            {"bucket-primary": {"sources": {"keyword"}}},
            {"has_explicit_entity": False},
            [],
            {},
        )

    async def fake_format_direct_bucket(bucket, *_args, **_kwargs):
        return f"[bucket_id:{bucket['id']}] direct"

    async def fake_dream_overlay(**_kwargs):
        return ""

    monkeypatch.setattr(breath_recall, "_collect_search_materials", fake_collect_search_materials)
    monkeypatch.setattr(breath_recall, "_policy", lambda: _FakePolicy())
    monkeypatch.setattr(
        breath_recall,
        "_direct_moments_for_bucket",
        lambda bucket, _query: [{"bucket_id": bucket["id"], "moment_id": f"{bucket['id']}:m1"}],
    )
    monkeypatch.setattr(
        breath_recall,
        "_representative_moment",
        lambda moments: moments[0] if moments else None,
    )
    monkeypatch.setattr(breath_recall, "_format_direct_bucket", fake_format_direct_bucket)
    monkeypatch.setattr(breath_recall, "_dream_overlay", fake_dream_overlay)
    monkeypatch.setattr(breath_recall, "_write_diagnostics", lambda **_kwargs: None)
    monkeypatch.setattr(breath_recall, "_warning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(breath_recall.random, "random", lambda: 0.0)
    monkeypatch.setattr(
        breath_recall.rt,
        "bucket_mgr",
        SimpleNamespace(touch=lambda _bucket_id: None),
        raising=False,
    )
    monkeypatch.setattr(
        breath_recall.rt,
        "config",
        {
            "recall": {"query_resurface_enabled": True},
            "gateway": {"word_map_hint_enabled": False},
        },
        raising=False,
    )
    monkeypatch.setattr(breath_recall.rt, "dehydrator", None, raising=False)

    result = await breath_recall.search_breath(
        query="lookup query",
        max_tokens=4000,
        domain="work",
        valence=-1,
        arousal=-1,
        max_results=3,
        include_related=False,
        related_per_memory=0,
        edge_min_confidence=0.0,
        is_session_start=False,
        debug=False,
        auto_surface=False,
        direct_render_mode="auto",
        retrieval_mode="bucket",
    )

    assert "[surface_type: resurface]" not in result


@pytest.mark.parametrize(
    ("max_results", "direct_count", "expected_count"),
    [
        pytest.param(20, 0, 3, id="default-cap"),
        pytest.param(3, 2, 1, id="remaining-slot-cap"),
    ],
)
@pytest.mark.asyncio
async def test_current_resurface_randomly_samples_one_to_three_candidates(
    monkeypatch: pytest.MonkeyPatch,
    max_results: int,
    direct_count: int,
    expected_count: int,
):
    candidates = [
        {
            "id": f"bucket-resurface-{index}",
            "content": f"old low-signal memory {index}",
            "metadata": {
                "name": f"Resurfaced memory {index}",
                "type": "dynamic",
                "domain": ["work"],
            },
        }
        for index in range(5)
    ]
    sample_calls: list[tuple[list[dict], int]] = []

    def fake_sample(population: list[dict], count: int) -> list[dict]:
        sample_calls.append((population, count))
        return population[-count:]

    monkeypatch.setattr(breath_recall, "_resurface_candidates", lambda **_kwargs: candidates)
    monkeypatch.setattr(breath_recall.random, "random", lambda: 0.0)
    monkeypatch.setattr(breath_recall.random, "randint", lambda _start, _stop: 3)
    monkeypatch.setattr(breath_recall.random, "sample", fake_sample)
    monkeypatch.setattr(
        breath_recall.rt,
        "config",
        {"recall": {"query_resurface_enabled": True}},
        raising=False,
    )
    monkeypatch.setattr(breath_recall.rt, "dehydrator", None, raising=False)

    result, _ = await breath_recall._maybe_resurface(
        all_buckets=candidates,
        matched_bucket_ids=set(),
        domain_filter=["work"],
        direct_count=direct_count,
        max_results=max_results,
        max_tokens=4000,
        related_included=False,
    )

    resurfaced_ids = [
        bucket["id"]
        for bucket in candidates
        if f"[bucket_id:{bucket['id']}]" in result
    ]
    assert 1 <= len(resurfaced_ids) <= 3
    assert resurfaced_ids == [bucket["id"] for bucket in candidates[-expected_count:]]
    assert sample_calls == [(candidates, expected_count)]


def test_resurface_candidates_accept_legacy_scalar_domain(
    monkeypatch: pytest.MonkeyPatch,
):
    legacy_bucket = {
        "id": "bucket-legacy-domain",
        "score": 1.0,
        "metadata": {
            "name": "Legacy scalar domain",
            "type": "dynamic",
            "domain": "work",
        },
    }
    monkeypatch.setattr(breath_recall, "_recallable_bucket", lambda _bucket: True)

    candidates = breath_recall._resurface_candidates(
        all_buckets=[legacy_bucket],
        matched_bucket_ids=set(),
        domain_filter=["work"],
    )

    assert candidates == [legacy_bucket]
