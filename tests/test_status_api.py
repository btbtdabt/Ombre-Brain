from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from web import meta


class FakeMCP:
    def __init__(self) -> None:
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(fn):
            for method in methods:
                self.routes[(method, path)] = fn
            return fn

        return decorator


class FakeBucketManager:
    async def get_stats(self) -> dict[str, int]:
        return {
            "permanent_count": 2,
            "dynamic_count": 3,
            "feel_count": 4,
            "plan_count": 5,
            "letter_count": 6,
            "archive_count": 7,
        }


@pytest.mark.asyncio
async def test_status_reports_every_active_bucket_category(monkeypatch) -> None:
    mcp = FakeMCP()
    monkeypatch.setattr(meta.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(meta.sh, "bucket_mgr", FakeBucketManager())
    monkeypatch.setattr(meta.sh, "decay_engine", SimpleNamespace(is_running=True))
    monkeypatch.setattr(meta.sh, "embedding_engine", SimpleNamespace(enabled=True))
    monkeypatch.setattr(meta.sh, "version", "test-version")
    meta.register(mcp)

    response = await mcp.routes[("GET", "/api/status")](object())
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["buckets"] == {
        "permanent": 2,
        "dynamic": 3,
        "feel": 4,
        "plan": 5,
        "letter": 6,
        "archive": 7,
        "total": 20,
    }
