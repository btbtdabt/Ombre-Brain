import asyncio
import json
from copy import deepcopy
from types import MethodType

import httpx
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
    service.prepare_kwargs = []
    service.upstream_trace_ids = {}
    service.upstream_turns = {}
    service.debug_trace = TraceRecorder()

    async def warm_recall_runtime(self):
        return None

    async def close(self):
        return None

    async def prepare_payload(self, payload, session_id, **kwargs):
        self.prepare_kwargs.append(kwargs)
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
        self.upstream_turns["openai"] = {
            "recalled_ids": args[2],
            "user_message": args[3],
        }
        return await stream_response(self, "openai")

    async def stream_anthropic(self, *args, **kwargs):
        self.upstream_trace_ids["anthropic"] = kwargs.get("trace_id")
        self.upstream_turns["anthropic"] = {
            "recalled_ids": args[2],
            "user_message": args[3],
        }
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
    assert [kwargs["manage_turn_snapshot"] for kwargs in service.prepare_kwargs] == [
        True,
        True,
    ]


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


def test_gateway_diagnostic_probe_suppresses_turn_and_persona_inputs_for_streams(
    gateway_module,
):
    service = _gateway_stream_service(gateway_module)
    app = gateway_module.create_gateway_app(config={}, service=service)
    headers = {
        "Authorization": "Bearer gateway-token",
        "X-Ombre-Diagnostic-Probe": "production-alignment",
        "X-Ombre-Session-Id": "production-alignment-test",
    }

    with TestClient(app) as client:
        openai = client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-opus-4-8",
                "messages": [{"role": "user", "content": "alignment probe"}],
                "stream": True,
            },
            headers=headers,
        )
        anthropic = client.post(
            "/v1/messages",
            json={
                "model": "claude-opus-4-8-native",
                "messages": [{"role": "user", "content": "alignment probe"}],
                "max_tokens": 64,
                "stream": True,
            },
            headers=headers,
        )

    assert openai.status_code == 200
    assert anthropic.status_code == 200
    assert service.upstream_turns == {
        "openai": {"recalled_ids": None, "user_message": ""},
        "anthropic": {"recalled_ids": None, "user_message": ""},
    }


def _gateway_non_stream_service(gateway_module):
    service = gateway_module.GatewayService.__new__(gateway_module.GatewayService)
    service.gateway_token = "gateway-token"
    service.gateway_token_routes = []
    service.gateway_cfg = {}
    service.default_session_id = "compat-session"
    service.upstream_default_model = "claude-opus-4-8"
    service.pending_tool_reasoning = {}
    service.debug_trace = TraceRecorder()
    service.recorded_turns = []
    service.persona_inputs = []

    async def warm_recall_runtime(self):
        return None

    async def close(self):
        return None

    async def prepare_payload(self, payload, session_id, **kwargs):
        return payload, ["memory-1"], {}

    def resolve_upstream(self, model):
        protocol = "anthropic" if str(model).endswith("-native") else "openai"
        return {
            "public_model": model,
            "upstream_model": model,
            "upstream": {"protocol": protocol},
        }

    def uses_anthropic(self, upstream):
        return upstream.get("protocol") == "anthropic"

    async def forward_openai(self, payload, **kwargs):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}}
                ]
            },
        )

    async def forward_anthropic(self, payload, route, **kwargs):
        return httpx.Response(
            200,
            json={
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
            },
        )

    async def retry_memory_detail(self, **kwargs):
        return kwargs["upstream_response"], None

    async def record_success(self, session_id, recalled_ids, injection_debug, **kwargs):
        self.recorded_turns.append(
            {
                "session_id": session_id,
                "recalled_ids": recalled_ids,
                "user_message": kwargs.get("user_message"),
            }
        )

    async def update_persona(self, session_id, user_message, assistant_message, recalled_ids):
        self.persona_inputs.append(
            {
                "session_id": session_id,
                "user_message": user_message,
                "recalled_ids": recalled_ids,
            }
        )

    service.warm_recall_runtime = MethodType(warm_recall_runtime, service)
    service.close = MethodType(close, service)
    service.prepare_payload = MethodType(prepare_payload, service)
    service._resolve_upstream_for_model = MethodType(resolve_upstream, service)
    service._upstream_uses_anthropic_protocol = MethodType(uses_anthropic, service)
    service._forward_upstream = MethodType(forward_openai, service)
    service._forward_anthropic_upstream = MethodType(forward_anthropic, service)
    service._maybe_retry_with_memory_detail = MethodType(retry_memory_detail, service)
    service._record_successful_round = MethodType(record_success, service)
    service._update_persona_after_assistant_message = MethodType(update_persona, service)
    return service


def test_gateway_diagnostic_probe_suppresses_non_stream_persistence_for_both_protocols(
    gateway_module,
):
    service = _gateway_non_stream_service(gateway_module)
    app = gateway_module.create_gateway_app(config={}, service=service)
    headers = {
        "Authorization": "Bearer gateway-token",
        "X-Ombre-Diagnostic-Probe": "production-alignment",
        "X-Ombre-Session-Id": "production-alignment-test",
    }

    with TestClient(app) as client:
        openai = client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-opus-4-8",
                "messages": [{"role": "user", "content": "alignment probe"}],
                "stream": False,
            },
            headers=headers,
        )
        anthropic = client.post(
            "/v1/messages",
            json={
                "model": "claude-opus-4-8-native",
                "messages": [{"role": "user", "content": "alignment probe"}],
                "max_tokens": 64,
                "stream": False,
            },
            headers=headers,
        )

    assert openai.status_code == 200
    assert anthropic.status_code == 200
    expected = [
        {
            "session_id": "production-alignment-test",
            "recalled_ids": None,
            "user_message": "",
        },
        {
            "session_id": "production-alignment-test",
            "recalled_ids": None,
            "user_message": "",
        },
    ]
    assert service.recorded_turns == expected
    assert service.persona_inputs == [
        {
            "session_id": "production-alignment-test",
            "recalled_ids": [],
            "user_message": "",
        },
        {
            "session_id": "production-alignment-test",
            "recalled_ids": [],
            "user_message": "",
        },
    ]


def test_gateway_diagnostic_marker_requires_an_isolated_probe_session(gateway_module):
    service = _gateway_non_stream_service(gateway_module)
    app = gateway_module.create_gateway_app(config={}, service=service)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-opus-4-8",
                "messages": [{"role": "user", "content": "ordinary turn"}],
                "stream": False,
            },
            headers={
                "Authorization": "Bearer gateway-token",
                "X-Ombre-Diagnostic-Probe": "production-alignment",
                "X-Ombre-Session-Id": "main",
            },
        )

    assert response.status_code == 200
    assert service.recorded_turns == [
        {
            "session_id": "main",
            "recalled_ids": ["memory-1"],
            "user_message": "ordinary turn",
        }
    ]
    assert service.persona_inputs == [
        {
            "session_id": "main",
            "recalled_ids": ["memory-1"],
            "user_message": "ordinary turn",
        }
    ]


def _turn_snapshot_service(gateway_module):
    service = gateway_module.GatewayService.__new__(gateway_module.GatewayService)
    service.pending_turn_injections = {}
    service.turn_injection_snapshot_ttl_seconds = 3600.0
    service.turn_injection_snapshot_max_per_session = 4
    return service


def test_gateway_turn_snapshot_reuses_one_injection_until_final_response(
    gateway_module,
):
    service = _turn_snapshot_service(gateway_module)
    source_messages = [
        {"role": "system", "content": "chat application prompt"},
        {"role": "user", "content": "查一下旧约定"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "breath",
                "parameters": {"type": "object"},
            },
        }
    ]
    prepared_payload = {
        "model": "claude-opus-4-8",
        "messages": service._inject_context_messages(
            source_messages,
            "stable recalled context",
            "live recalled context",
        ),
        "tools": tools,
    }

    snapshot_key = service._remember_turn_injection_snapshot(
        "session-a",
        source_messages,
        prepared_payload,
        stable_context="stable recalled context",
        dynamic_context="live recalled context",
    )
    continuation_messages = [
        *deepcopy(source_messages),
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "breath", "arguments": '{"query":"旧约定"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "约定内容"},
    ]

    found_key, snapshot = service._find_turn_injection_snapshot(
        "session-a",
        continuation_messages,
        {
            "model": "claude-opus-4-8",
            "messages": continuation_messages,
            "tools": tools,
        },
    )

    assert found_key == snapshot_key
    assert snapshot is not None
    forwarded_messages = [
        *deepcopy(snapshot["prepared_messages"]),
        *deepcopy(continuation_messages[len(source_messages):]),
    ]
    serialized = json.dumps(forwarded_messages, ensure_ascii=False)
    assert serialized.count("stable recalled context") == 1
    assert serialized.count("live recalled context") == 1
    assert "call-1" in serialized
    assert "约定内容" in serialized

    injection_debug = {
        "turn_injection_snapshot": {
            "snapshot_key": snapshot_key,
        }
    }
    service._update_turn_injection_snapshot_after_assistant(
        "session-a",
        injection_debug,
        continuation_messages[-2],
    )
    assert snapshot_key in service.pending_turn_injections["session-a"]

    service._update_turn_injection_snapshot_after_assistant(
        "session-a",
        injection_debug,
        {"role": "assistant", "content": "这是最后回复。"},
    )
    assert "session-a" not in service.pending_turn_injections
    assert injection_debug["turn_injection_snapshot"]["cleared_after_response"] is True


def test_gateway_turn_snapshot_requires_matching_model_and_tool_contract(
    gateway_module,
):
    service = _turn_snapshot_service(gateway_module)
    source_messages = [{"role": "user", "content": "查记忆"}]
    tools = [{"type": "function", "function": {"name": "breath"}}]
    prepared_payload = {
        "model": "claude-opus-4-8",
        "messages": service._inject_context_messages(
            source_messages,
            "stable",
            "dynamic",
        ),
        "tools": tools,
    }
    service._remember_turn_injection_snapshot(
        "session-contract",
        source_messages,
        prepared_payload,
        stable_context="stable",
        dynamic_context="dynamic",
    )
    continuation_messages = [
        *source_messages,
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
    ]

    _key, snapshot = service._find_turn_injection_snapshot(
        "session-contract",
        continuation_messages,
        {
            "model": "different-model",
            "messages": continuation_messages,
            "tools": tools,
        },
    )

    assert snapshot is None
