import asyncio
import json

import httpx
from starlette.responses import StreamingResponse


def test_gateway_tokens_select_claude_openai_and_gemini_defaults(
    gateway_module,
    monkeypatch,
):
    monkeypatch.setenv("OMBRE_GATEWAY_GEMINI_TOKEN", "gemini-token")
    monkeypatch.delenv("OMBRE_GATEWAY_GEMINI_DEFAULT_MODEL", raising=False)
    service = gateway_module.GatewayService.__new__(gateway_module.GatewayService)
    service.gateway_cfg = {
        "token_routes": [
            {
                "name": "claude-native",
                "token": "native-token",
                "default_model": "claude-opus-4-8-native",
            },
            {
                "name": "claude-openai",
                "token": "openai-token",
                "default_model": "claude-opus-4-8",
            },
        ]
    }
    service.gateway_token = "fallback-token"
    service.gateway_token_routes = service._load_gateway_token_routes()

    assert service._auth_context_from_token("native-token") == {
        "name": "claude-native",
        "default_model": "claude-opus-4-8-native",
    }
    assert service._auth_context_from_token("openai-token") == {
        "name": "claude-openai",
        "default_model": "claude-opus-4-8",
    }
    assert service._auth_context_from_token("gemini-token") == {
        "name": "gemini",
        "default_model": "gemini-3.5-flash",
    }
    assert service._auth_context_from_token("fallback-token") == {
        "name": "default",
        "default_model": "",
    }
    assert service._auth_context_from_token("wrong-token") is None


def test_anthropic_thinking_and_tool_blocks_round_trip_without_loss(gateway_module):
    service = gateway_module.GatewayService.__new__(gateway_module.GatewayService)
    service.gateway_cfg = {}
    thinking = {"type": "enabled", "budget_tokens": 2048}
    assistant_blocks = [
        {
            "type": "thinking",
            "thinking": "I should use the lookup tool.",
            "signature": "signed-reasoning",
        },
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "lookup",
            "input": {"query": "Ombre"},
        },
    ]
    anthropic_payload = {
        "model": "claude-opus-4-8-native",
        "system": [{"type": "text", "text": "System policy"}],
        "messages": [
            {"role": "assistant", "content": assistant_blocks},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [{"type": "text", "text": "lookup result"}],
                    }
                ],
            },
        ],
        "thinking": thinking,
        "tools": [
            {
                "name": "lookup",
                "description": "Look something up",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ],
        "tool_choice": {"type": "tool", "name": "lookup"},
        "max_tokens": 4096,
    }

    openai_payload = service._anthropic_request_to_openai(anthropic_payload)
    native_payload = service._anthropic_payload_for_upstream(
        openai_payload,
        {
            "upstream": {"prompt_cache": ""},
            "upstream_model": "claude-upstream",
        },
    )

    assistant_message = openai_payload["messages"][1]
    assert assistant_message["tool_calls"] == [
        {
            "id": "toolu_1",
            "type": "function",
            "function": {
                "name": "lookup",
                "arguments": '{"query": "Ombre"}',
            },
        }
    ]
    assert assistant_message["reasoning_details"] == [
        {
            "type": "reasoning.text",
            "text": "I should use the lookup tool.",
            "signature": "signed-reasoning",
            "id": None,
            "format": "anthropic-claude-v1",
            "index": 0,
        }
    ]
    assert openai_payload["_ombre_anthropic_thinking"] == thinking
    assert openai_payload["reasoning"] == {"enabled": True, "max_tokens": 2048}

    assert native_payload["model"] == "claude-upstream"
    assert native_payload["system"] == "System policy"
    assert native_payload["thinking"] == thinking
    assert native_payload["messages"] == [
        {"role": "assistant", "content": assistant_blocks},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "lookup result",
                }
            ],
        },
    ]


def test_stream_proxy_emits_keepalives_while_opening_and_between_chunks(gateway_module):
    async def run_stream():
        async def inner_body():
            await asyncio.sleep(0.03)
            yield b'data: {"delta":"ok"}\n\n'

        async def response_factory():
            await asyncio.sleep(0.03)
            return StreamingResponse(inner_body(), media_type="text/event-stream")

        return [
            chunk
            async for chunk in gateway_module._stream_response_with_keepalives(
                response_factory,
                prefix="ombre-gateway-chat",
                interval_seconds=0.005,
            )
        ]

    chunks = asyncio.run(run_stream())

    assert chunks[0] == b": ombre-gateway-chat-start\n\n"
    assert b": ombre-gateway-chat-preparing-wait\n\n" in chunks
    assert b": ombre-gateway-chat-upstream\n\n" in chunks
    assert b": ombre-gateway-chat-upstream-wait\n\n" in chunks
    assert chunks[-1] == b'data: {"delta":"ok"}\n\n'


def test_keepalive_stream_encodes_late_http_errors_after_sse_headers_start(gateway_module):
    async def run_stream():
        async def response_factory():
            return gateway_module.JSONResponse(
                {"error": {"message": "upstream unavailable"}},
                status_code=503,
            )

        return b"".join(
            [
                chunk
                async for chunk in gateway_module._stream_response_with_keepalives(
                    response_factory,
                    prefix="ombre-gateway-chat",
                    interval_seconds=0.005,
                )
            ]
        )

    body = asyncio.run(run_stream())

    assert body.startswith(b": ombre-gateway-chat-start\n\n")
    assert b'"code":503' in body
    assert b"upstream unavailable" in body


def test_stream_protocol_conversion_preserves_reasoning_and_tool_deltas(gateway_module):
    service = gateway_module.GatewayService.__new__(gateway_module.GatewayService)
    openai_chunk = {
        "choices": [
            {
                "delta": {
                    "reasoning_details": [
                        {
                            "type": "reasoning.text",
                            "text": "Use the lookup tool.",
                            "signature": "signed-stream-reasoning",
                            "index": 0,
                        }
                    ],
                    "tool_calls": [
                        {
                            "index": 1,
                            "id": "toolu_stream",
                            "function": {
                                "name": "lookup",
                                "arguments": '{"query":"Ombre"}',
                            },
                        }
                    ],
                }
            }
        ]
    }

    anthropic_events = service._openai_sse_chunk_to_anthropic_events(
        f"data: {json.dumps(openai_chunk)}\n\n".encode()
    )

    assert anthropic_events == [
        {
            "reasoning_detail": {
                "type": "reasoning.text",
                "text": "Use the lookup tool.",
                "signature": "signed-stream-reasoning",
                "index": 0,
            }
        },
        {
            "tool_call": {
                "index": 1,
                "id": "toolu_stream",
                "name": "lookup",
                "arguments": '{"query":"Ombre"}',
            }
        },
    ]

    thinking_chunks = service._openai_chunks_from_anthropic_event(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Inspect state."},
        },
        chunk_id="chatcmpl_compat",
        created=1,
        model="claude-opus-4-8",
    )
    tool_chunks = service._openai_chunks_from_anthropic_event(
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"id":'},
        },
        chunk_id="chatcmpl_compat",
        created=1,
        model="claude-opus-4-8",
    )

    thinking_delta = thinking_chunks[0]["chunk"]["choices"][0]["delta"]
    tool_delta = tool_chunks[0]["chunk"]["choices"][0]["delta"]
    assert thinking_delta["reasoning"] == "Inspect state."
    assert thinking_delta["reasoning_details"][0]["text"] == "Inspect state."
    assert tool_delta["tool_calls"][0] == {
        "index": 1,
        "function": {"arguments": '{"id":'},
    }


def test_upstream_request_and_response_share_gateway_trace_id(gateway_module):
    class TraceRecorder:
        def __init__(self):
            self.records = []

        def write(self, component, event, payload=None, **metadata):
            self.records.append((component, event, payload, metadata))

        def response_payload(self, status_code, headers, body):
            return {
                "status_code": status_code,
                "headers": headers,
                "body": json.loads(body),
            }

    class HttpClient:
        async def post(self, url, *, headers, json):
            assert headers["Authorization"] == "Bearer upstream-secret"
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"id": "completion_1"},
            )

    service = gateway_module.GatewayService.__new__(gateway_module.GatewayService)
    service.debug_trace = TraceRecorder()
    service.http_client = HttpClient()
    service._resolve_upstream_for_model = lambda model: {
        "public_model": model,
        "upstream_model": "provider-model",
        "upstream": {"name": "provider", "base_url": "https://provider.invalid/v1"},
    }
    service._payload_for_upstream_model = lambda payload, model: {
        **payload,
        "model": model,
    }
    service._available_upstream_api_keys = lambda upstream: [
        {"label": "primary", "value": "upstream-secret"}
    ]
    service._clear_upstream_key_cooldown = lambda upstream, key: None
    service._cool_down_upstream_key = lambda upstream, key: None
    service._should_retry_upstream_status = lambda status: False

    response = asyncio.run(
        service._forward_upstream(
            {"model": "claude-opus-4-8", "messages": []},
            trace_id="trace-compat",
            gateway_route="/v1/chat/completions",
        )
    )

    assert response.status_code == 200
    request_record, response_record = service.debug_trace.records
    assert request_record[1] == "upstream_request"
    assert request_record[2] == {
        "route": "/v1/chat/completions",
        "stage": "main",
        "upstream": "provider",
        "url": "https://provider.invalid/v1/chat/completions",
        "public_model": "claude-opus-4-8",
        "upstream_model": "provider-model",
        "body": {"model": "provider-model", "messages": []},
    }
    assert "upstream-secret" not in json.dumps(request_record[2])
    assert response_record[1] == "upstream_response"
    assert response_record[2]["status_code"] == 200
    assert request_record[3]["trace_id"] == "trace-compat"
    assert response_record[3]["trace_id"] == "trace-compat"


def test_upstream_stream_tracing_does_not_consume_provider_body(gateway_module):
    class TraceRecorder:
        def __init__(self):
            self.records = []

        def write(self, component, event, payload=None, **metadata):
            self.records.append((event, payload, metadata))

    class HttpClient:
        def build_request(self, method, url, *, headers, json):
            assert headers["Authorization"] == "Bearer upstream-secret"
            return httpx.Request(method, url, headers=headers, json=json)

        async def send(self, request, *, stream):
            assert stream is True
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=httpx.ByteStream(b"data: provider\n\n"),
                request=request,
            )

    service = gateway_module.GatewayService.__new__(gateway_module.GatewayService)
    service.debug_trace = TraceRecorder()
    service.http_client = HttpClient()
    service._payload_for_upstream_model = lambda payload, model: {
        **payload,
        "model": model,
    }
    service._available_upstream_api_keys = lambda upstream: [
        {"label": "primary", "value": "upstream-secret"}
    ]
    service._clear_upstream_key_cooldown = lambda upstream, key: None
    service._cool_down_upstream_key = lambda upstream, key: None
    service._should_retry_upstream_status = lambda status: False
    route = {
        "public_model": "claude-opus-4-8",
        "upstream_model": "provider-model",
        "upstream": {"name": "provider", "base_url": "https://provider.invalid/v1"},
    }

    response = asyncio.run(
        service._open_upstream_stream(
            route,
            {"model": "claude-opus-4-8", "messages": [], "stream": True},
            trace_id="trace-stream",
            gateway_route="/v1/chat/completions",
        )
    )

    assert [record[0] for record in service.debug_trace.records] == [
        "upstream_stream_request",
        "upstream_stream_response",
    ]
    assert all(record[2]["trace_id"] == "trace-stream" for record in service.debug_trace.records)
    assert asyncio.run(response.aread()) == b"data: provider\n\n"
