import pytest

import reranker_engine
from reranker_engine import RerankResult, RerankerEngine


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "results": [
                {"index": 0, "relevance_score": "0.4"},
                {"index": 1, "relevance_score": 1.7},
                {"index": 99, "relevance_score": 0.9},
                {"index": "bad", "relevance_score": 0.5},
            ]
        }


class FakeClient:
    calls = []

    def __init__(self, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, endpoint, *, headers, json):
        self.calls.append((endpoint, headers, json, self.timeout))
        return FakeResponse()


@pytest.mark.asyncio
async def test_reranker_posts_documents_and_returns_sorted_bounded_scores(monkeypatch):
    monkeypatch.setattr(reranker_engine.httpx, "AsyncClient", FakeClient)
    engine = RerankerEngine(
        {
            "reranker": {
                "base_url": "https://rerank.example/v1/",
                "api_key": "test-key",
                "model": "test-model",
                "candidate_limit": 500,
                "score_weight": -2,
            }
        }
    )

    results = await engine.rerank("query", ["first", "second"], top_n=99)

    assert results == [RerankResult(index=1, score=1.0), RerankResult(index=0, score=0.4)]
    endpoint, headers, payload, _timeout = FakeClient.calls[-1]
    assert endpoint == "https://rerank.example/v1/rerank"
    assert headers["Authorization"] == "Bearer test-key"
    assert payload["documents"] == ["first", "second"]
    assert payload["top_n"] == 2
    assert engine.candidate_limit == 100
    assert engine.score_weight == 0.0


@pytest.mark.asyncio
async def test_disabled_reranker_returns_no_results_without_http(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("HTTP client should not be constructed")

    monkeypatch.setattr(reranker_engine.httpx, "AsyncClient", fail_if_called)
    engine = RerankerEngine({"reranker": {"enabled": True}})

    assert not engine.enabled
    assert await engine.rerank("query", ["document"]) == []
