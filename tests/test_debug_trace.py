import asyncio
import json

from debug_trace import DebugTraceASGIMiddleware, DebugTraceLogger


def test_debug_trace_redacts_sensitive_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("OMBRE_DEBUG_TRACE", "1")
    tracer = DebugTraceLogger({"state_dir": str(tmp_path)})

    tracer.write(
        "gateway",
        "incoming_request",
        payload={
            "authorization": "Bearer secret-token",
            "api_key": "sk-test-secret",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    path = next((tmp_path / "debug_traces").glob("trace-*.jsonl"))
    record = json.loads(path.read_text(encoding="utf-8").strip())

    assert record["payload"]["authorization"] == "[REDACTED]"
    assert record["payload"]["api_key"] == "[REDACTED]"
    assert record["payload"]["messages"][0]["content"] == "hello"
    assert "secret-token" not in json.dumps(record)


def test_debug_trace_middleware_captures_mcp_jsonrpc(tmp_path, monkeypatch):
    monkeypatch.setenv("OMBRE_DEBUG_TRACE", "1")
    tracer = DebugTraceLogger({"state_dir": str(tmp_path)})

    async def app(scope, receive, send):
        request = await receive()
        assert json.loads(request["body"].decode("utf-8"))["method"] == "tools/call"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"ok"}]}}',
            }
        )

    middleware = DebugTraceASGIMiddleware(app, tracer, component="mcp")
    sent = []

    async def receive():
        return {
            "type": "http.request",
            "body": b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"breath","arguments":{"query":"x"}}}',
            "more_body": False,
        }

    async def send(message):
        sent.append(message)

    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
            },
            receive,
            send,
        )
    )

    path = next((tmp_path / "debug_traces").glob("trace-*.jsonl"))
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert [record["event"] for record in records] == ["http_request", "http_response"]
    assert records[0]["payload"]["jsonrpc"]["tool"] == "breath"
    assert records[1]["payload"]["status_code"] == 200
    assert sent[-1]["body"].startswith(b'{"jsonrpc"')
