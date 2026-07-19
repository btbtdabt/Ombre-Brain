import json
from collections.abc import Callable
from datetime import datetime

import pytest

import web.buckets as buckets_web


class FakeMCP:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(handler):
            for method in methods:
                self.routes[(method, path)] = handler
            return handler

        return decorator


class ListRequest:
    def __init__(self, sort_mode=None, *, include_archive=None, limit=None):
        self.query_params = {}
        if sort_mode is not None:
            self.query_params["sort"] = sort_mode
        if include_archive is not None:
            self.query_params["include_archive"] = include_archive
        if limit is not None:
            self.query_params["limit"] = limit


class FakeBucketManager:
    def __init__(self, buckets, *, expected_include_archive=True):
        self.buckets = buckets
        self.expected_include_archive = expected_include_archive

    async def list_all(self, *, include_archive=False):
        assert include_archive is self.expected_include_archive
        return list(self.buckets)

    async def list_light(
        self,
        *,
        include_archive=False,
        limit=500,
        offset=0,
        sort="created_desc",
        score_calculator: Callable[[dict], float] | None = None,
        exclude_deleted=False,
    ):
        assert include_archive is self.expected_include_archive
        items = [
            dict(bucket)
            for bucket in self.buckets
            if not exclude_deleted
            or not bucket.get("metadata", {}).get("deleted_at")
        ]
        assert callable(score_calculator)
        for item in items:
            item["score"] = float(score_calculator(item.get("metadata", {})))
        if sort == "score":
            items.sort(key=lambda item: (-item["score"], str(item["id"])))
        else:
            descending = sort == "created_desc"

            def created_key(item):
                timestamp = buckets_web._datetime_epoch_ms(
                    item.get("metadata", {}).get("created")
                )
                if timestamp is None:
                    return (1, 0, str(item["id"]))
                return (
                    0,
                    -timestamp if descending else timestamp,
                    str(item["id"]),
                )

            items.sort(key=created_key)
        return items[offset : offset + limit], len(items)


class FakeDecayEngine:
    def calculate_score(self, metadata):
        return float(metadata.get("test_score", 0.0))


def _bucket(bucket_id, *, created="", last_active="", score=0.0, deleted=False):
    metadata = {
        "name": bucket_id,
        "created": created,
        "last_active": last_active,
        "test_score": score,
    }
    if deleted:
        metadata["deleted_at"] = "2026-01-01T00:00:00Z"
    return {"id": bucket_id, "metadata": metadata, "content": bucket_id}


async def _list(
    monkeypatch,
    buckets,
    sort_mode=None,
    *,
    include_archive=None,
    limit=None,
    expected_include_archive=True,
):
    manager = FakeBucketManager(
        buckets,
        expected_include_archive=expected_include_archive,
    )
    monkeypatch.setattr(buckets_web.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(buckets_web.sh, "bucket_mgr", manager, raising=False)
    monkeypatch.setattr(
        buckets_web.sh, "decay_engine", FakeDecayEngine(), raising=False
    )
    mcp = FakeMCP()
    buckets_web.register(mcp)

    response = await mcp.routes[("GET", "/api/buckets")](
        ListRequest(
            sort_mode,
            include_archive=include_archive,
            limit=limit,
        )
    )
    payload = json.loads(response.body.decode("utf-8"))
    return response, payload


@pytest.mark.asyncio
async def test_bucket_list_default_keeps_score_order_with_stable_id_ties(monkeypatch):
    response, payload = await _list(
        monkeypatch,
        [
            _bucket("low", score=1),
            _bucket("tie-z", score=5),
            _bucket("tie-a", score=5),
        ],
    )

    assert response.status_code == 200
    assert [item["id"] for item in payload] == ["tie-a", "tie-z", "low"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sort_mode", "expected"),
    [
        (
            "created_desc",
            ["later", "earlier", "old-a", "old-b", "missing-a", "missing-b"],
        ),
        (
            "created_asc",
            ["old-a", "old-b", "earlier", "later", "missing-a", "missing-b"],
        ),
    ],
)
async def test_bucket_list_sorts_real_instants_and_keeps_unknown_times_last(
    monkeypatch, sort_mode, expected
):
    # Lexical order is deliberately misleading here: 01:00Z is later than
    # 08:30+08:00 (00:30Z). The route must compare parsed instants.
    buckets = [
        _bucket("missing-b", created="not-a-time"),
        _bucket("old-b", created="2026-01-01T00:00:00Z"),
        _bucket("later", created="2026-01-01T01:00:00Z"),
        _bucket("missing-a"),
        _bucket("earlier", created="2026-01-01T08:30:00+08:00"),
        _bucket("old-a", created="2026-01-01T00:00:00Z"),
        _bucket("deleted", created="2030-01-01T00:00:00Z", deleted=True),
    ]

    response, payload = await _list(monkeypatch, buckets, sort_mode)

    assert response.status_code == 200
    assert [item["id"] for item in payload] == expected


@pytest.mark.asyncio
async def test_bucket_list_rejects_unknown_sort_mode(monkeypatch):
    response, payload = await _list(monkeypatch, [_bucket("one")], "newest")

    assert response.status_code == 400
    assert payload == {
        "error": "invalid sort mode",
        "allowed": ["created_asc", "created_desc", "score"],
    }


@pytest.mark.asyncio
async def test_bucket_list_returns_server_normalized_display_instants(monkeypatch):
    response, payload = await _list(
        monkeypatch,
        [
            _bucket(
                "timed",
                created="2026-01-01T00:00:01Z",
                last_active="2026-01-01T08:00:02+08:00",
            ),
            _bucket("invalid", created="not-a-time"),
        ],
    )

    assert response.status_code == 200
    by_id = {item["id"]: item for item in payload}
    epoch = round(datetime.fromisoformat("2026-01-01T00:00:00+00:00").timestamp() * 1000)
    assert by_id["timed"]["created_epoch_ms"] == epoch + 1000
    assert by_id["timed"]["last_active_epoch_ms"] == epoch + 2000
    assert by_id["invalid"]["created_epoch_ms"] is None
    assert by_id["invalid"]["last_active_epoch_ms"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query_value", "expected_include_archive"),
    [("0", False), ("1", True), ("false", False), ("true", True)],
)
async def test_bucket_list_honors_explicit_archive_filter(
    monkeypatch,
    query_value,
    expected_include_archive,
):
    response, payload = await _list(
        monkeypatch,
        [_bucket("one")],
        include_archive=query_value,
        expected_include_archive=expected_include_archive,
    )

    assert response.status_code == 200
    assert [item["id"] for item in payload] == ["one"]


@pytest.mark.asyncio
async def test_bucket_list_applies_bounded_optional_limit(monkeypatch):
    response, payload = await _list(
        monkeypatch,
        [_bucket("three", score=3), _bucket("two", score=2), _bucket("one", score=1)],
        limit="2",
    )

    assert response.status_code == 200
    assert set(payload) == {
        "buckets",
        "count",
        "include_archive",
        "limit",
        "offset",
    }
    assert payload["count"] == 3
    assert payload["include_archive"] is True
    assert payload["limit"] == 2
    assert payload["offset"] == 0
    assert [item["id"] for item in payload["buckets"]] == ["three", "two"]


@pytest.mark.asyncio
async def test_bucket_list_rejects_ambiguous_archive_filter(monkeypatch):
    response, payload = await _list(
        monkeypatch,
        [_bucket("one")],
        include_archive="sometimes",
    )

    assert response.status_code == 400
    assert payload == {
        "error": "invalid include_archive",
        "allowed": ["0", "1", "false", "true"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["0", "201", "not-a-number"])
async def test_bucket_list_rejects_out_of_bounds_limit(monkeypatch, limit):
    response, payload = await _list(
        monkeypatch,
        [_bucket("one")],
        limit=limit,
    )

    assert response.status_code == 400
    assert payload == {
        "error": "invalid limit",
        "minimum": 1,
        "maximum": 200,
    }
