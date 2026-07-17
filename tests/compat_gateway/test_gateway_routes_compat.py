import asyncio
from types import MethodType

from starlette.responses import JSONResponse
from starlette.responses import StreamingResponse
from starlette.testclient import TestClient


class RoutingService:
    def __init__(self):
        self.calls = []
        self.warmed = False
        self.closed = False

    async def warm_recall_runtime(self):
        self.warmed = True

    async def close(self):
        self.closed = True

    async def _response(self, request, route):
        self.calls.append((route, request.url.path, dict(request.path_params)))
        return JSONResponse({"route": route})

    async def handle_chat(self, request):
        return await self._response(request, "openai")

    async def handle_anthropic_messages(self, request):
        return await self._response(request, "anthropic")

    async def handle_gemini_native_model(self, request):
        return await self._response(request, "gemini")


class TraceRecorder:
    def __init__(self):
        self.records = []

    def write(self, component, event, payload=None, **metadata):
        self.records.append(
            {
                "component": component,
                "event": event,
                "payload": payload,
                **metadata,
            }
        )


def test_gateway_app_exposes_openai_anthropic_and_gemini_native_routes(gateway_module):
    service = RoutingService()
    app = gateway_module.create_gateway_app(config={}, service=service)

    with TestClient(app) as client:
        openai = client.post("/v1/chat/completions", json={})
        anthropic = client.post("/v1/messages", json={})
        gemini_v1beta = client.post(
            "/v1beta/models/gemini-3.5-flash:generateContent",
            json={},
        )
        gemini_root = client.post(
            "/models/gemini-3.5-flash:streamGenerateContent",
            json={},
        )

    assert service.warmed is True
    assert service.closed is True
    assert [response.json()["route"] for response in (
        openai,
        anthropic,
        gemini_v1beta,
        gemini_root,
    )] == ["openai", "anthropic", "gemini", "gemini"]
    assert service.calls == [
        ("openai", "/v1/chat/completions", {}),
        ("anthropic", "/v1/messages", {}),
        (
            "gemini",
            "/v1beta/models/gemini-3.5-flash:generateContent",
            {"model": "gemini-3.5-flash:generateContent"},
        ),
        (
            "gemini",
            "/models/gemini-3.5-flash:streamGenerateContent",
            {"model": "gemini-3.5-flash:streamGenerateContent"},
        ),
    ]


def _gateway_stream_service(gateway_module):
    service = gateway_module.GatewayService.__new__(gateway_module.GatewayService)
    service.gateway_token = "gateway-token"
    service.gateway_token_routes = []
    service.gateway_cfg = {}
    service.default_session_id = "compat-session"
    service.upstream_default_model = "claude-opus-4-8-native"
    service.calls = []
    service.upstream_trace_ids = {}
    service.debug_trace = TraceRecorder()

    async def warm_recall_runtime(self):
        return None

    async def close(self):
        return None

    async def prepare_payload(self, payload, session_id, **kwargs):
        await asyncio.sleep(0.02)
        return payload, [], {}

    async def stream_response(self, route):
        self.calls.append(route)
        await asyncio.sleep(0.02)

        async def body():
            await asyncio.sleep(0.02)
            yield f"data: {route}\n\n".encode()

        return StreamingResponse(body(), media_type="text/event-stream")

    async def stream_openai(self, *args, **kwargs):
        self.upstream_trace_ids["openai"] = kwargs.get("trace_id")
        return await stream_response(self, "openai")

    async def stream_anthropic(self, *args, **kwargs):
        self.upstream_trace_ids["anthropic"] = kwargs.get("trace_id")
        return await stream_response(self, "anthropic")

    async def stream_gemini(self, *args, **kwargs):
        self.upstream_trace_ids["gemini"] = kwargs.get("trace_id")
        return await stream_response(self, "gemini")

    service.warm_recall_runtime = MethodType(warm_recall_runtime, service)
    service.close = MethodType(close, service)
    service.prepare_payload = MethodType(prepare_payload, service)
    service._stream_upstream = MethodType(stream_openai, service)
    service._stream_upstream_as_anthropic = MethodType(stream_anthropic, service)
    service._open_gemini_native_stream_response = MethodType(stream_gemini, service)
    return service


def test_gateway_routes_apply_stream_keepalives_for_all_protocols(
    gateway_module,
    monkeypatch,
):
    monkeypatch.setattr(gateway_module, "CHAT_STREAM_KEEPALIVE_SECONDS", 0.005)
    monkeypatch.setattr(gateway_module, "ANTHROPIC_STREAM_KEEPALIVE_SECONDS", 0.005)
    monkeypatch.setattr(gateway_module, "GEMINI_NATIVE_STREAM_KEEPALIVE_SECONDS", 0.005)
    service = _gateway_stream_service(gateway_module)
    app = gateway_module.create_gateway_app(config={}, service=service)
    headers = {"Authorization": "Bearer gateway-token"}

    with TestClient(app) as client:
        openai = client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-opus-4-8",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
            headers=headers,
        )
        anthropic = client.post(
            "/v1/messages",
            json={
                "model": "claude-opus-4-8-native",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 64,
                "stream": True,
            },
            headers=headers,
        )
        gemini = client.post(
            "/v1beta/models/gemini-3.5-flash:streamGenerateContent",
            json={"contents": []},
            headers=headers,
        )

    assert b": ombre-gateway-chat-preparing-wait\n\n" in openai.content
    assert b"data: openai\n\n" in openai.content
    assert b": ombre-gateway-anthropic-preparing-wait\n\n" in anthropic.content
    assert b"data: anthropic\n\n" in anthropic.content
    assert b": ombre-gateway-preparing-wait\n\n" in gemini.content
    assert b"data: gemini\n\n" in gemini.content
    assert service.calls == ["openai", "anthropic", "gemini"]

    records = service.debug_trace.records
    assert [record["event"] for record in records] == [
        "incoming_request",
        "prepared_payload",
        "incoming_request",
        "prepared_payload",
        "incoming_request",
    ]
    assert [record["payload"]["route"] for record in records] == [
        "/v1/chat/completions",
        "/v1/chat/completions",
        "/v1/messages",
        "/v1/messages",
        "/v1beta/models/gemini-3.5-flash:streamGenerateContent",
    ]
    assert all(record.get("trace_id") for record in records)
    assert records[0]["trace_id"] == records[1]["trace_id"]
    assert records[2]["trace_id"] == records[3]["trace_id"]
    assert service.upstream_trace_ids == {
        "openai": records[0]["trace_id"],
        "anthropic": records[2]["trace_id"],
        "gemini": records[4]["trace_id"],
    }


def test_gateway_rejects_invalid_auth_before_starting_stream(gateway_module):
    service = _gateway_stream_service(gateway_module)
    app = gateway_module.create_gateway_app(config={}, service=service)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-opus-4-8",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
            headers={"Authorization": "Bearer wrong-token"},
        )

    assert response.status_code == 401
    assert b"ombre-gateway-chat-start" not in response.content
    assert service.calls == []
    assert service.debug_trace.records == []
