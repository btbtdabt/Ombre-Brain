import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import frontmatter
import pytest
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from bucket_manager import BucketManager
from dehydrator import Dehydrator
from dream_engine import _clamp
from memory_nodes import _facet_keywords_for_config
from portrait_engine import DailyPortraitMaintainer
from server import _build_remote_transport_app


def _config(tmp_path: Path) -> dict:
    return {
        "buckets_dir": str(tmp_path / "buckets"),
        "state_dir": str(tmp_path / "state"),
        "memory": {"max_results": 5},
    }


def test_move_bucket_preserves_scalar_domain_name(tmp_path: Path) -> None:
    manager = BucketManager(_config(tmp_path))
    for index, (domain, expected) in enumerate(
        (("relationship", "relationship"), (("work", "life"), "work"))
    ):
        source = tmp_path / f"source-{index}.md"
        source.write_text("memory", encoding="utf-8")

        destination = Path(manager._move_bucket(str(source), str(tmp_path / "permanent"), domain))

        assert destination.parent.name == expected
        assert destination.read_text(encoding="utf-8") == "memory"


def test_missing_display_name_does_not_create_none_facet_keyword() -> None:
    keywords = _facet_keywords_for_config({"identity": {"user_aliases": []}})

    assert "None" not in keywords["relation.intimacy"]
    assert "None" not in keywords["topic.love"]


def test_dream_clamp_accepts_float_convertible_values() -> None:
    assert _clamp(Decimal("0.7")) == 0.7


def test_string_activation_counts_remain_mutable(tmp_path: Path) -> None:
    manager = BucketManager(_config(tmp_path))
    source_id = asyncio.run(manager.create(content="source"))
    target_id = asyncio.run(manager.create(content="target"))

    def set_count(bucket_id: str, value: str) -> None:
        bucket_path = manager._find_bucket_file(bucket_id)
        assert bucket_path is not None
        post = frontmatter.load(bucket_path)
        post["activation_count"] = value
        Path(bucket_path).write_text(frontmatter.dumps(post), encoding="utf-8")

    set_count(target_id, "3")
    assert asyncio.run(manager.add_comment(target_id, "comment")) is not None
    target = asyncio.run(manager.get(target_id))
    assert target is not None
    assert target["metadata"]["activation_count"] == 4.0

    set_count(target_id, "2.5")
    asyncio.run(manager.touch(target_id, ripple=False))
    target = asyncio.run(manager.get(target_id))
    assert target is not None
    assert target["metadata"]["activation_count"] == 3.5

    set_count(target_id, "1.5")
    reference_time = manager._parse_iso_datetime(target["metadata"]["created"])
    assert reference_time is not None
    asyncio.run(manager._time_ripple(source_id, reference_time))
    target = asyncio.run(manager.get(target_id))
    assert target is not None
    assert target["metadata"]["activation_count"] == 1.8


def test_optional_llm_clients_fail_with_explicit_runtime_errors(tmp_path: Path) -> None:
    components = (Dehydrator(_config(tmp_path)), DailyPortraitMaintainer(_config(tmp_path)))

    for component in components:
        try:
            component._require_client()
        except RuntimeError:
            continue
        raise AssertionError(f"{type(component).__name__} accepted a missing LLM client")


def test_remote_transport_app_runs_fastmcp_and_ombre_lifespans() -> None:
    events: list[str] = []
    probe = FastMCP("quality-hardening-probe")
    inner_app = probe.streamable_http_app()

    async def ombre_startup() -> None:
        events.append("ombre-start")

    app = _build_remote_transport_app(
        inner_app,
        ombre_startup,
        transport_lifespan=probe.session_manager.run,
    )
    with TestClient(app, base_url="http://localhost:8000") as client:
        response = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
        )
        assert response.status_code == 200
        assert "serverInfo" in response.text
        events.append("serving")

    assert events == ["ombre-start", "serving"]


def test_remote_transport_app_unwinds_transport_when_ombre_startup_fails() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def transport_lifespan():
        events.append("transport-start")
        try:
            yield
        finally:
            events.append("transport-stop")

    async def failing_ombre_startup() -> None:
        events.append("ombre-start")
        raise RuntimeError("startup failed")

    async def health(_request):
        return PlainTextResponse("ok")

    inner_app = Starlette(routes=[Route("/health", health)])
    app = _build_remote_transport_app(
        inner_app,
        failing_ombre_startup,
        transport_lifespan=transport_lifespan,
    )

    with pytest.raises(RuntimeError, match="startup failed"):
        with TestClient(app):
            pass

    assert events == ["transport-start", "ombre-start", "transport-stop"]


def test_remote_transport_app_preserves_fastmcp_sse_routes_and_middleware() -> None:
    events: list[str] = []
    probe = FastMCP("quality-hardening-sse-probe")

    async def ombre_startup() -> None:
        events.append("ombre-start")

    app = _build_remote_transport_app(probe.sse_app(), ombre_startup)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    with TestClient(app, base_url="http://localhost:8000") as client:
        responses = [
            client.post("/messages/", headers={"Origin": "https://client.example"})
            for _ in range(2)
        ]

    assert all(response.status_code == 400 for response in responses)
    assert all(response.text == "Invalid Content-Type header" for response in responses)
    assert all(response.headers["access-control-allow-origin"] == "*" for response in responses)
    assert events == ["ombre-start"]
