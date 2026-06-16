import pytest


class DummyDehydrator:
    async def analyze(self, content: str):
        return {
            "domain": ["auto_domain"],
            "valence": 0.6,
            "arousal": 0.4,
            "tags": ["auto_tag"],
            "suggested_name": "auto_name",
        }


class DisabledEmbeddingEngine:
    enabled = False


@pytest.mark.asyncio
async def test_hold_accepts_explicit_domain_on_first_write(monkeypatch, bucket_mgr, decay_eng):
    import server

    async def passthrough_moment(content, tags=None):
        return content

    monkeypatch.setattr(server, "bucket_mgr", bucket_mgr)
    monkeypatch.setattr(server, "decay_engine", decay_eng)
    monkeypatch.setattr(server, "dehydrator", DummyDehydrator())
    monkeypatch.setattr(server, "embedding_engine", DisabledEmbeddingEngine())
    monkeypatch.setattr(server, "_auto_generate_write_moment_if_needed", passthrough_moment)
    monkeypatch.setattr(server, "_queue_memory_enrichment", lambda *args, **kwargs: None)

    result = await server.hold(
        content="小雨和 Haven 约定把这条放进固定工程域。",
        tags="manual_domain",
        title="fixed_domain_test",
        domain="project, relationship",
    )

    buckets = await bucket_mgr.list_all()
    created = next(bucket for bucket in buckets if bucket["metadata"].get("name") == "fixed_domain_test")

    assert created["metadata"]["domain"] == ["project", "relationship"]
    assert "project,relationship" in result
