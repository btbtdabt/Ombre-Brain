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
async def test_current_recall_honors_query_resurface_toggle(monkeypatch: pytest.MonkeyPatch):
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

    async def fake_collect_search_materials(**_kwargs):
        return (
            "lookup query",
            [primary],
            [primary, resurfaced],
            {"bucket-primary": {"sources": {"keyword"}}},
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

    assert "[surface_type: resurface] [bucket_id:bucket-resurface]" in result
