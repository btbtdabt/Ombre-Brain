"""Bounded, paginated light-bucket listing regressions."""

from __future__ import annotations

import asyncio
import builtins
import json
import threading
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import frontmatter
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

import bucket_manager as bucket_manager_module
import web.buckets as buckets_web
from bucket_manager import BucketManager
from tools.current import memory as current_memory_tool
from web.current_contract import CurrentWebDependencies
from web.current_memory import register as register_memory_routes


class RecordingMCP:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Callable[..., Any]] = {}

    def custom_route(self, path: str, methods: list[str]):
        def decorator(handler):
            for method in methods:
                self.routes[(method.upper(), path)] = handler
            return handler

        return decorator


def request_for(method: str, path: str, *, query_string: str = "") -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_string.encode("ascii"),
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "path_params": {},
    }
    return Request(scope)


def response_json(response) -> Any:
    return json.loads(response.body.decode("utf-8"))


def _manager(tmp_path: Path) -> BucketManager:
    base = tmp_path / "vault"
    for name in ("permanent", "dynamic", "archive", "feel", "plans"):
        (base / name).mkdir(parents=True, exist_ok=True)
    return BucketManager(
        {
            "buckets_dir": str(base),
            "matching": {"fuzzy_threshold": 50, "max_results": 10},
            "embedding": {"enabled": False},
        }
    )


def _write_bucket(
    manager: BucketManager,
    bucket_id: str,
    *,
    store: str = "dynamic",
    body: str = "body",
    **metadata,
) -> Path:
    directory = Path(manager.base_dir) / store / "tests"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{bucket_id}.md"
    path.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                body,
                id=bucket_id,
                name=metadata.pop("name", bucket_id),
                **metadata,
            )
        ),
        encoding="utf-8",
    )
    return path


def _score(metadata: dict) -> float:
    return float(metadata.get("test_score", 0.0))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sort_mode", "expected"),
    [
        ("score", ["later", "tie-a"]),
        ("created_desc", ["later", "tie-a"]),
        ("created_asc", ["old", "earlier"]),
    ],
)
async def test_light_listing_sorts_before_paginating_and_keeps_unknown_times_last(
    tmp_path: Path,
    sort_mode: str,
    expected: list[str],
) -> None:
    manager = _manager(tmp_path)
    _write_bucket(
        manager,
        "missing",
        created="not-a-time",
        test_score=1,
    )
    _write_bucket(
        manager,
        "old",
        created="2026-01-01T00:00:00Z",
        test_score=2,
    )
    _write_bucket(
        manager,
        "earlier",
        created="2026-01-01T08:30:00+08:00",
        test_score=3,
    )
    _write_bucket(
        manager,
        "tie-z",
        created="2026-01-01T00:45:00Z",
        test_score=9,
    )
    _write_bucket(
        manager,
        "tie-a",
        created="2026-01-01T00:45:00Z",
        test_score=9,
    )
    _write_bucket(
        manager,
        "later",
        created="2026-01-01T01:00:00Z",
        test_score=10,
    )

    page, count = await manager.list_light(
        limit=2,
        offset=0,
        sort=sort_mode,
        score_calculator=_score,
    )

    assert count == 6
    assert [item["id"] for item in page] == expected


@pytest.mark.asyncio
async def test_light_listing_archive_is_opt_in_and_offset_is_server_side(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    for index in range(4):
        _write_bucket(
            manager,
            f"active-{index}",
            created=f"2026-01-0{index + 1}T00:00:00Z",
        )
    _write_bucket(
        manager,
        "archived",
        store="archive",
        created="2026-01-05T00:00:00Z",
        deleted_at="2026-01-06T00:00:00Z",
    )

    active_page, active_count = await manager.list_light(
        include_archive=False,
        limit=2,
        offset=1,
        sort="created_desc",
    )
    archive_page, archive_count = await manager.list_light(
        include_archive=True,
        limit=1,
        offset=0,
        sort="created_desc",
    )
    archive_only_page, archive_only_count = await manager.list_light(
        include_archive=True,
        archive_only=True,
        limit=1,
        offset=0,
        sort="created_desc",
    )

    assert active_count == 4
    assert [item["id"] for item in active_page] == ["active-2", "active-1"]
    assert archive_count == 5
    assert [item["id"] for item in archive_page] == ["archived"]
    assert archive_only_count == 1
    assert [item["id"] for item in archive_only_page] == ["archived"]


@pytest.mark.asyncio
async def test_light_listing_skips_malformed_and_oversized_frontmatter(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    valid_path = _write_bucket(manager, "valid", created="2026-01-01T00:00:00Z")
    directory = valid_path.parent
    (directory / "malformed.md").write_text(
        "---\nid: malformed\ninvalid: [\n---\nbody",
        encoding="utf-8",
    )
    (directory / "unterminated.md").write_text(
        "---\nid: unterminated\nbody without a closing delimiter",
        encoding="utf-8",
    )
    (directory / "oversized.md").write_text(
        "---\nid: oversized\npadding: " + ("x" * 70_000) + "\n---\nbody",
        encoding="utf-8",
    )

    page, count = await manager.list_light(limit=20, sort="created_desc")

    assert count == 1
    assert [item["id"] for item in page] == ["valid"]


@pytest.mark.asyncio
async def test_light_listing_filters_type_and_requires_every_tag(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _write_bucket(
        manager,
        "daily",
        store="feel",
        type="feel",
        tags=["relationship_weather", "daily_impression", "extra"],
        created="2026-01-03T00:00:00Z",
    )
    _write_bucket(
        manager,
        "missing-tag",
        store="feel",
        type="feel",
        tags=["daily_impression"],
        created="2026-01-02T00:00:00Z",
    )
    _write_bucket(
        manager,
        "wrong-type",
        type="dynamic",
        tags=["relationship_weather", "daily_impression"],
        created="2026-01-01T00:00:00Z",
    )

    page, count = await manager.list_light(
        limit=10,
        sort="created_desc",
        bucket_type="feel",
        required_tags=("relationship_weather", "daily_impression"),
    )

    assert count == 1
    assert [item["id"] for item in page] == ["daily"]


@pytest.mark.asyncio
async def test_light_listing_isolates_per_bucket_score_failures(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _write_bucket(manager, "broken-score", created="2026-01-02T00:00:00Z")
    _write_bucket(manager, "healthy-score", created="2026-01-01T00:00:00Z")

    def flaky_score(metadata: dict) -> float:
        if metadata.get("id") == "broken-score":
            raise OSError("synthetic scoring failure")
        return 7.0

    page, count = await manager.list_light(
        limit=10,
        sort="score",
        score_calculator=flaky_score,
    )

    assert count == 2
    assert [item["id"] for item in page] == ["healthy-score", "broken-score"]
    assert [item["score"] for item in page] == [7.0, 0.0]


@pytest.mark.asyncio
async def test_high_offset_heap_retains_only_compact_sort_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    for index in range(120):
        _write_bucket(
            manager,
            f"large-meta-{index:03d}",
            created=f"2026-03-{(index % 28) + 1:02d}T00:00:00Z",
            padding="x" * 16_000,
        )
    oversized_id_path = Path(manager.base_dir) / "dynamic" / "tests" / "oversized-id.md"
    oversized_id_path.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                "body",
                id="x" * 60_000,
                name="oversized id must not enter the heap",
                created="2026-03-01T00:00:00Z",
            )
        ),
        encoding="utf-8",
    )

    original_nsmallest = bucket_manager_module.heapq.nsmallest
    observed = 0

    def inspect_candidates(n, iterable, *, key):
        nonlocal observed
        candidates = list(iterable)
        observed = len(candidates)
        assert all("metadata" not in candidate for candidate in candidates)
        assert all(len(str(candidate["id"])) <= 128 for candidate in candidates)
        assert all(
            set(candidate)
            <= {
                "id",
                "path",
                "score",
                "created_epoch_ms",
                "last_active_epoch_ms",
            }
            for candidate in candidates
        )
        return original_nsmallest(n, candidates, key=key)

    monkeypatch.setattr(bucket_manager_module.heapq, "nsmallest", inspect_candidates)

    page, count = await manager.list_light(
        limit=3,
        offset=100,
        sort="created_desc",
    )

    assert observed == count == 120
    assert len(page) == 3


class _CountingBinaryFile:
    def __init__(self, handle, path: Path, read_bytes: dict[Path, int]) -> None:
        self._handle = handle
        self._path = path
        self._read_bytes = read_bytes

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *args):
        return self._handle.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self._handle, name)

    def read(self, size: int = -1):
        assert size >= 0, "light listing must never issue an unbounded read"
        data = self._handle.read(size)
        self._read_bytes[self._path] += len(data)
        return data

    def readline(self, size: int = -1):
        assert size >= 0, "light listing must bound every frontmatter line read"
        data = self._handle.readline(size)
        self._read_bytes[self._path] += len(data)
        return data


@pytest.mark.asyncio
async def test_large_vault_reads_bounded_headers_and_only_page_previews(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    paths = []
    for index in range(40):
        paths.append(
            _write_bucket(
                manager,
                f"memory-{index:02d}",
                body=(f"preview-{index:02d}-" + ("z" * 300_000)),
                created=f"2026-02-{(index % 28) + 1:02d}T{index % 24:02d}:00:00Z",
            )
        )

    async def forbidden_list_all(*_args, **_kwargs):
        raise AssertionError("list_light must not call list_all")

    monkeypatch.setattr(manager, "list_all", forbidden_list_all)
    original_read_text = Path.read_text

    def forbidden_read_text(path: Path, *args, **kwargs):
        if path.suffix == ".md" and Path(manager.base_dir) in path.parents:
            raise AssertionError("light listing must not read a full Markdown body")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbidden_read_text)
    original_open = builtins.open
    read_bytes: dict[Path, int] = defaultdict(int)

    def counting_open(file, mode="r", *args, **kwargs):
        handle = original_open(file, mode, *args, **kwargs)
        path = Path(file)
        if "b" in mode and path.suffix == ".md" and path in paths:
            return _CountingBinaryFile(handle, path, read_bytes)
        return handle

    monkeypatch.setattr(builtins, "open", counting_open)

    page, count = await manager.list_light(
        limit=3,
        offset=5,
        sort="created_desc",
    )

    assert count == 40
    assert len(page) == 3
    assert all(len(item["content"]) <= 200 for item in page)
    assert set(read_bytes) == set(paths)
    assert max(read_bytes.values()) < 8_000
    # Every file contributes a small frontmatter read; only the selected page
    # should incur the bounded body-preview read.
    assert sum(total > 1_000 for total in read_bytes.values()) == 3


@pytest.mark.asyncio
async def test_light_listing_keeps_event_loop_heartbeat_responsive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    _write_bucket(manager, "memory", created="2026-01-01T00:00:00Z")
    original_load_header = manager._load_bucket_light_header
    scan_started = threading.Event()
    release_scan = threading.Event()
    ordering: list[str] = []
    first_call = True

    def slow_load_header(file_path: str):
        nonlocal first_call
        if first_call:
            first_call = False
            ordering.append("scan-start")
            scan_started.set()
            release_scan.wait(timeout=0.5)
            ordering.append("scan-end")
        return original_load_header(file_path)

    async def heartbeat() -> None:
        while not scan_started.is_set():
            await asyncio.sleep(0)
        ordering.append("heartbeat")
        release_scan.set()

    monkeypatch.setattr(manager, "_load_bucket_light_header", slow_load_header)

    await asyncio.gather(
        manager.list_light(limit=1, sort="created_desc"),
        heartbeat(),
    )

    assert ordering.index("heartbeat") < ordering.index("scan-end")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sort": "newest"}, "invalid sort mode"),
        ({"offset": 100_001}, "light-list offset exceeds maximum 100000"),
    ],
)
async def test_light_listing_offload_preserves_validation_errors(
    tmp_path: Path,
    kwargs: dict[str, Any],
    message: str,
) -> None:
    manager = _manager(tmp_path)

    with pytest.raises(ValueError, match=message):
        await manager.list_light(**kwargs)


@pytest.mark.asyncio
async def test_bounded_full_route_does_not_eagerly_read_large_vault_bodies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    for index in range(40):
        _write_bucket(
            manager,
            f"full-{index:02d}",
            body=f"preview-{index:02d}-" + ("x" * 300_000),
            created=f"2026-02-{(index % 28) + 1:02d}T{index % 24:02d}:00:00Z",
            test_score=float(index),
        )

    async def forbidden_list_all(*_args, **_kwargs):
        raise AssertionError("bounded full listing must not call list_all")

    monkeypatch.setattr(manager, "list_all", forbidden_list_all)
    original_read_text = Path.read_text

    def forbidden_read_text(path: Path, *args, **kwargs):
        if path.suffix == ".md" and Path(manager.base_dir) in path.parents:
            raise AssertionError("bounded full listing must not read Markdown bodies")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbidden_read_text)
    monkeypatch.setattr(buckets_web.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(buckets_web.sh, "bucket_mgr", manager, raising=False)
    monkeypatch.setattr(
        buckets_web.sh,
        "decay_engine",
        SimpleNamespace(calculate_score=_score),
        raising=False,
    )
    mcp = RecordingMCP()
    buckets_web.register(mcp)

    response = await mcp.routes[("GET", "/api/buckets")](
        request_for(
            "GET",
            "/api/buckets",
            query_string="include_archive=false&limit=3&sort=created_desc",
        )
    )
    payload = response_json(response)

    assert response.status_code == 200
    assert payload["count"] == 40
    assert len(payload["buckets"]) == 3
    assert all(len(item["content_preview"]) <= 200 for item in payload["buckets"])


@pytest.mark.asyncio
async def test_http_light_route_preserves_parity_fields_sort_auth_and_no_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    _write_bucket(
        manager,
        "rich",
        body="[[Amy|艾米]] likes a bounded preview.",
        created="2026-01-01T00:00:01Z",
        last_active="2026-01-01T08:00:02+08:00",
        valence=0.8,
        arousal=0.7,
        model_valence=0.6,
        test_score=12.5,
        dont_surface=True,
        first_of_kind=True,
        provenance={"kind": "test", "erasable": True},
        source_tool="hold",
        source="conversation",
        activation_count=3,
    )
    _write_bucket(
        manager,
        "other",
        body="other",
        created="2026-01-02T00:00:00Z",
        test_score=1,
    )

    async def forbidden_list_all(*_args, **_kwargs):
        raise AssertionError("HTTP light route must not call list_all")

    monkeypatch.setattr(manager, "list_all", forbidden_list_all)
    auth_calls: list[str] = []

    def auth_guard(request):
        auth_calls.append(request.url.path)
        return None

    dependencies = CurrentWebDependencies(
        config={},
        auth_guard=auth_guard,
        bucket_mgr=manager,
        decay_engine=SimpleNamespace(calculate_score=_score),
    )
    mcp = RecordingMCP()
    register_memory_routes(mcp, dependencies)
    request = request_for(
        "GET",
        "/api/buckets/light",
        query_string=urlencode(
            {
                "include_archive": "false",
                "limit": "1",
                "offset": "0",
                "sort": "score",
            }
        ),
    )

    response = await mcp.routes[("GET", "/api/buckets/light")](request)
    payload = response_json(response)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert auth_calls == ["/api/buckets/light"]
    assert set(payload) == {
        "buckets",
        "count",
        "include_archive",
        "limit",
        "offset",
    }
    assert payload["count"] == 2
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    [item] = payload["buckets"]
    assert item["id"] == item["bucket_id"] == "rich"
    assert item["score"] == 12.5
    assert item["valence"] == 0.8
    assert item["arousal"] == 0.7
    assert item["model_valence"] == 0.6
    assert item["content_preview"] == "Amy|艾米 likes a bounded preview."
    assert item["created"] == "2026-01-01T00:00:01Z"
    assert item["created_epoch_ms"] == 1767225601000
    assert item["last_active_epoch_ms"] == 1767225602000
    assert item["dont_surface"] is True
    assert item["first_of_kind"] is True
    assert item["erasable_test_data"] is True
    assert item["source_tool"] == "hold"
    assert item["source"] == "conversation"


@pytest.mark.asyncio
async def test_http_light_route_can_page_archives_without_active_rows(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    _write_bucket(
        manager,
        "active-newer",
        created="2026-01-02T00:00:00Z",
    )
    _write_bucket(
        manager,
        "archived-older",
        store="archive",
        created="2026-01-01T00:00:00Z",
        deleted_at="2026-01-03T00:00:00Z",
    )
    dependencies = CurrentWebDependencies(
        config={},
        auth_guard=lambda _request: None,
        bucket_mgr=manager,
        decay_engine=SimpleNamespace(calculate_score=_score),
    )
    mcp = RecordingMCP()
    register_memory_routes(mcp, dependencies)
    request = request_for(
        "GET",
        "/api/buckets/light",
        query_string=urlencode(
            {
                "include_archive": "true",
                "archive_only": "true",
                "limit": "100",
                "offset": "0",
                "sort": "created_desc",
            }
        ),
    )

    response = await mcp.routes[("GET", "/api/buckets/light")](request)
    payload = response_json(response)

    assert response.status_code == 200
    assert payload["count"] == 1
    assert [item["id"] for item in payload["buckets"]] == ["archived-older"]


@pytest.mark.asyncio
async def test_http_light_route_rejects_unknown_sort_before_scanning(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    called = False

    async def list_light(*_args, **_kwargs):
        nonlocal called
        called = True
        return [], 0

    manager.list_light = list_light  # type: ignore[method-assign]
    mcp = RecordingMCP()
    register_memory_routes(
        mcp,
        CurrentWebDependencies(
            config={},
            auth_guard=lambda _request: None,
            bucket_mgr=manager,
        ),
    )

    response = await mcp.routes[("GET", "/api/buckets/light")](
        request_for(
            "GET",
            "/api/buckets/light",
            query_string="sort=newest",
        )
    )

    assert response.status_code == 400
    assert response_json(response) == {
        "error": "invalid sort mode",
        "allowed": ["created_asc", "created_desc", "score"],
    }
    assert called is False


@pytest.mark.asyncio
async def test_http_light_route_applies_type_and_all_tag_filters(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    _write_bucket(
        manager,
        "daily",
        store="feel",
        type="feel",
        tags=["relationship_weather", "daily_impression"],
        created="2026-01-02T00:00:00Z",
    )
    _write_bucket(
        manager,
        "other-feel",
        store="feel",
        type="feel",
        tags=["daily_impression"],
        created="2026-01-03T00:00:00Z",
    )
    _write_bucket(
        manager,
        "other-type",
        type="dynamic",
        tags=["relationship_weather", "daily_impression"],
        created="2026-01-04T00:00:00Z",
    )
    mcp = RecordingMCP()
    register_memory_routes(
        mcp,
        CurrentWebDependencies(
            config={},
            auth_guard=lambda _request: None,
            bucket_mgr=manager,
        ),
    )

    response = await mcp.routes[("GET", "/api/buckets/light")](
        request_for(
            "GET",
            "/api/buckets/light",
            query_string=urlencode(
                {
                    "type": "feel",
                    "tags": "relationship_weather,daily_impression",
                    "sort": "created_desc",
                }
            ),
        )
    )
    payload = response_json(response)

    assert response.status_code == 200
    assert payload["count"] == 1
    assert [item["id"] for item in payload["buckets"]] == ["daily"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query_string",
    [
        "type=feel%21",
        "tags=relationship_weather%2C%2Cdaily_impression",
        "tags=" + ("x" * 129),
    ],
)
async def test_http_light_route_rejects_invalid_filters_before_scanning(
    tmp_path: Path,
    query_string: str,
) -> None:
    manager = _manager(tmp_path)
    called = False

    async def list_light(*_args, **_kwargs):
        nonlocal called
        called = True
        return [], 0

    manager.list_light = list_light  # type: ignore[method-assign]
    mcp = RecordingMCP()
    register_memory_routes(
        mcp,
        CurrentWebDependencies(
            config={},
            auth_guard=lambda _request: None,
            bucket_mgr=manager,
        ),
    )

    response = await mcp.routes[("GET", "/api/buckets/light")](
        request_for("GET", "/api/buckets/light", query_string=query_string)
    )

    assert response.status_code == 400
    assert response_json(response)["error"] == "invalid light-list filter"
    assert called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query_string", "expected_error"),
    [
        ("limit=foo", "limit must be an integer"),
        ("offset=abc", "offset must be an integer"),
        ("limit=0", "limit must be between 1 and 2000"),
        ("limit=2001", "limit must be between 1 and 2000"),
        ("offset=-1", "offset must be between 0 and 100000"),
    ],
)
async def test_http_light_route_rejects_invalid_pagination_before_scanning(
    tmp_path: Path,
    query_string: str,
    expected_error: str,
) -> None:
    manager = _manager(tmp_path)
    called = False

    async def list_light(*_args, **_kwargs):
        nonlocal called
        called = True
        return [], 0

    manager.list_light = list_light  # type: ignore[method-assign]
    mcp = RecordingMCP()
    register_memory_routes(
        mcp,
        CurrentWebDependencies(
            config={},
            auth_guard=lambda _request: None,
            bucket_mgr=manager,
        ),
    )

    response = await mcp.routes[("GET", "/api/buckets/light")](
        request_for(
            "GET",
            "/api/buckets/light",
            query_string=query_string,
        )
    )

    assert response.status_code == 400
    assert response_json(response) == {"error": expected_error}
    assert response.headers["cache-control"] == "no-store"
    assert called is False


@pytest.mark.asyncio
async def test_http_light_route_rejects_excessive_offset_before_scanning(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    called = False

    async def list_light(*_args, **_kwargs):
        nonlocal called
        called = True
        return [], 0

    manager.list_light = list_light  # type: ignore[method-assign]
    mcp = RecordingMCP()
    register_memory_routes(
        mcp,
        CurrentWebDependencies(
            config={},
            auth_guard=lambda _request: None,
            bucket_mgr=manager,
        ),
    )

    response = await mcp.routes[("GET", "/api/buckets/light")](
        request_for(
            "GET",
            "/api/buckets/light",
            query_string="offset=100001",
        )
    )

    assert response.status_code == 400
    assert response_json(response) == {
        "error": "offset exceeds maximum",
        "max_offset": 100_000,
    }
    assert called is False


@pytest.mark.asyncio
async def test_http_light_route_auth_rejection_does_not_scan(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    called = False

    async def list_light(*_args, **_kwargs):
        nonlocal called
        called = True
        return [], 0

    manager.list_light = list_light  # type: ignore[method-assign]
    mcp = RecordingMCP()
    register_memory_routes(
        mcp,
        CurrentWebDependencies(
            config={},
            auth_guard=lambda _request: JSONResponse(
                {"error": "unauthorized"}, status_code=401
            ),
            bucket_mgr=manager,
        ),
    )

    response = await mcp.routes[("GET", "/api/buckets/light")](
        request_for("GET", "/api/buckets/light")
    )

    assert response.status_code == 401
    assert response_json(response) == {"error": "unauthorized"}
    assert called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "expected_error"),
    [
        ({"limit": "foo"}, "limit must be an integer"),
        ({"offset": "abc"}, "offset must be an integer"),
        ({"limit": 1.5}, "limit must be an integer"),
        ({"offset": True}, "offset must be an integer"),
        ({"limit": 0}, "limit must be between 1 and 2000"),
        ({"limit": 2001}, "limit must be between 1 and 2000"),
        ({"offset": -1}, "offset must be between 0 and 100000"),
    ],
)
async def test_mcp_light_listing_rejects_invalid_pagination_before_scanning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, Any],
    expected_error: str,
) -> None:
    manager = _manager(tmp_path)
    called = False

    async def list_light(*_args, **_kwargs):
        nonlocal called
        called = True
        return [], 0

    manager.list_light = list_light  # type: ignore[method-assign]
    monkeypatch.setattr(current_memory_tool.rt, "bucket_mgr", manager)

    result = await current_memory_tool.list_buckets_light(**arguments)

    assert result == {"error": expected_error, "buckets": []}
    assert called is False


@pytest.mark.asyncio
async def test_mcp_light_listing_rejects_excessive_offset_before_scanning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    called = False

    async def list_light(*_args, **_kwargs):
        nonlocal called
        called = True
        return [], 0

    manager.list_light = list_light  # type: ignore[method-assign]
    monkeypatch.setattr(current_memory_tool.rt, "bucket_mgr", manager)

    result = await current_memory_tool.list_buckets_light(offset=100_001)

    assert result == {
        "error": "offset exceeds maximum",
        "max_offset": 100_000,
        "buckets": [],
    }
    assert called is False
