from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable


_SENSITIVE_KEY_RE = re.compile(
    r"(authorization|api[-_]?key|x[-_]?api[-_]?key|token|secret|password|passwd|cookie)",
    re.IGNORECASE,
)
_SECRET_TEXT_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9._-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-ant-[A-Za-z0-9._-]{8,}", re.IGNORECASE),
    re.compile(r"\bAIza[A-Za-z0-9._-]{20,}", re.IGNORECASE),
)


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _int_value(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _resolve_state_dir(config: dict[str, Any]) -> Path:
    state_dir = str(config.get("state_dir") or "").strip()
    if state_dir:
        return Path(state_dir).expanduser().resolve()
    buckets_dir = str(config.get("buckets_dir") or "buckets").strip()
    return (Path(buckets_dir).expanduser().resolve().parent / "state").resolve()


def _redact_text(text: str) -> str:
    redacted = text
    for pattern in _SECRET_TEXT_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        redacted,
    )
    return redacted


class DebugTraceLogger:
    """Opt-in JSONL trace writer for sensitive prompt/tool debugging."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        trace_cfg = self.config.get("debug_trace", {})
        self.trace_cfg = trace_cfg if isinstance(trace_cfg, dict) else {}
        env_enabled = os.environ.get("OMBRE_DEBUG_TRACE")
        self.enabled = _bool_value(env_enabled, _bool_value(self.trace_cfg.get("enabled"), False))
        self.gateway_payloads = _bool_value(
            os.environ.get("OMBRE_DEBUG_TRACE_GATEWAY"),
            _bool_value(self.trace_cfg.get("gateway_payloads"), True),
        )
        self.mcp_http = _bool_value(
            os.environ.get("OMBRE_DEBUG_TRACE_MCP"),
            _bool_value(self.trace_cfg.get("mcp_http"), True),
        )
        self.max_value_chars = _int_value(
            os.environ.get("OMBRE_DEBUG_TRACE_MAX_VALUE_CHARS") or self.trace_cfg.get("max_value_chars"),
            12000,
            200,
            200000,
        )
        self.max_body_chars = _int_value(
            os.environ.get("OMBRE_DEBUG_TRACE_MAX_BODY_CHARS") or self.trace_cfg.get("max_body_chars"),
            20000,
            500,
            500000,
        )
        self.max_files = _int_value(
            os.environ.get("OMBRE_DEBUG_TRACE_MAX_FILES") or self.trace_cfg.get("max_files"),
            7,
            1,
            60,
        )
        trace_dir = (
            os.environ.get("OMBRE_DEBUG_TRACE_DIR")
            or str(self.trace_cfg.get("dir") or "").strip()
            or str(_resolve_state_dir(self.config) / "debug_traces")
        )
        self.trace_dir = Path(trace_dir).expanduser().resolve()
        self._lock = threading.Lock()

    def enabled_for(self, component: str) -> bool:
        component_key = str(component or "").strip().lower()
        if component_key == "gateway":
            env_value = os.environ.get("OMBRE_DEBUG_TRACE_GATEWAY")
            if env_value is not None:
                return _bool_value(env_value, False)
            return bool(self.enabled and self.gateway_payloads)
        if component_key in {"mcp", "mcp_http"}:
            env_value = os.environ.get("OMBRE_DEBUG_TRACE_MCP")
            if env_value is not None:
                return _bool_value(env_value, False)
            return bool(self.enabled and self.mcp_http)
        return bool(self.enabled)

    def write(self, component: str, event: str, payload: Any = None, **metadata: Any) -> None:
        if not self.enabled_for(component):
            return
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "component": component,
            "event": event,
        }
        for key, value in metadata.items():
            if value is not None:
                record[key] = value
        if payload is not None:
            record["payload"] = payload
        safe_record = self.sanitize(record)
        line = json.dumps(safe_record, ensure_ascii=False, default=str)
        with self._lock:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            self._prune_old_files()
            path = self.trace_dir / f"trace-{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def sanitize(self, value: Any) -> Any:
        return self._sanitize_value(value, depth=0)

    def _sanitize_value(self, value: Any, *, depth: int) -> Any:
        if depth > 12:
            return "[DEPTH_LIMIT]"
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for raw_key, raw_value in value.items():
                key = str(raw_key)
                if _SENSITIVE_KEY_RE.search(key):
                    result[key] = "[REDACTED]"
                else:
                    result[key] = self._sanitize_value(raw_value, depth=depth + 1)
            return result
        if isinstance(value, list):
            return [self._sanitize_value(item, depth=depth + 1) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize_value(item, depth=depth + 1) for item in value]
        if isinstance(value, bytes):
            return self._clip(_redact_text(value.decode("utf-8", errors="replace")), self.max_body_chars)
        if isinstance(value, str):
            limit = self.max_body_chars if depth <= 2 else self.max_value_chars
            return self._clip(_redact_text(value), limit)
        return value

    def body_payload(self, body: bytes, content_type: str = "") -> Any:
        text = self._clip(body.decode("utf-8", errors="replace"), self.max_body_chars)
        if "json" in str(content_type or "").lower():
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text, "json_error": "invalid_json"}
        return text

    def response_payload(self, status_code: int, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        content_type = headers.get("content-type") or headers.get("Content-Type") or ""
        return {
            "status_code": status_code,
            "headers": headers,
            "body": self.body_payload(body, content_type),
        }

    def _clip(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"...[truncated {len(text) - limit} chars]"

    def _prune_old_files(self) -> None:
        files = sorted(self.trace_dir.glob("trace-*.jsonl"), key=lambda item: item.name, reverse=True)
        for old_file in files[self.max_files :]:
            try:
                old_file.unlink()
            except OSError:
                pass


def asgi_headers_to_dict(raw_headers: list[tuple[bytes, bytes]] | tuple[tuple[bytes, bytes], ...]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key_bytes, value_bytes in raw_headers or []:
        key = key_bytes.decode("latin-1", errors="replace").lower()
        value = value_bytes.decode("latin-1", errors="replace")
        if key in headers:
            headers[key] = f"{headers[key]}, {value}"
        else:
            headers[key] = value
    return headers


def headers_to_dict(headers: Any) -> dict[str, str]:
    try:
        return {str(key).lower(): str(value) for key, value in headers.items()}
    except Exception:
        return {}


class DebugTraceASGIMiddleware:
    """Capture raw MCP HTTP JSON-RPC request/response pairs without changing tool signatures."""

    def __init__(
        self,
        app: Callable[[dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[None]],
        trace_logger: DebugTraceLogger,
        component: str = "mcp",
    ):
        self.app = app
        self.trace_logger = trace_logger
        self.component = component

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http" or not self.trace_logger.enabled_for(self.component):
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "").upper()
        path = str(scope.get("path") or "")
        if method != "POST" or not self._is_mcp_path(path):
            await self.app(scope, receive, send)
            return

        request_messages: list[dict[str, Any]] = []
        request_body = bytearray()
        while True:
            message = await receive()
            request_messages.append(message)
            if message.get("type") == "http.request":
                request_body.extend(message.get("body") or b"")
                if not message.get("more_body", False):
                    break
            else:
                break

        request_index = 0

        async def replay_receive() -> dict[str, Any]:
            nonlocal request_index
            if request_index < len(request_messages):
                message = request_messages[request_index]
                request_index += 1
                return message
            return await receive()

        status_code = 0
        response_headers: dict[str, str] = {}
        response_body = bytearray()

        async def trace_send(message: dict[str, Any]) -> None:
            nonlocal status_code, response_headers
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status") or 0)
                response_headers = asgi_headers_to_dict(message.get("headers") or [])
            elif message.get("type") == "http.response.body":
                chunk = message.get("body") or b""
                if chunk and len(response_body) < self.trace_logger.max_body_chars:
                    remaining = self.trace_logger.max_body_chars - len(response_body)
                    response_body.extend(chunk[:remaining])
            await send(message)

        request_headers = asgi_headers_to_dict(scope.get("headers") or [])
        request_payload = self.trace_logger.body_payload(bytes(request_body), request_headers.get("content-type", ""))
        trace_id = self._trace_id(request_payload)
        self.trace_logger.write(
            self.component,
            "http_request",
            payload={
                "method": method,
                "path": path,
                "query_string": (scope.get("query_string") or b"").decode("latin-1", errors="replace"),
                "headers": request_headers,
                "body": request_payload,
                "jsonrpc": self._jsonrpc_summary(request_payload),
            },
            trace_id=trace_id,
        )

        try:
            await self.app(scope, replay_receive, trace_send)
        except Exception as exc:
            self.trace_logger.write(
                self.component,
                "http_error",
                payload={"error_type": type(exc).__name__, "error": str(exc)},
                trace_id=trace_id,
            )
            raise
        finally:
            self.trace_logger.write(
                self.component,
                "http_response",
                payload={
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "headers": response_headers,
                    "body": self.trace_logger.body_payload(
                        bytes(response_body),
                        response_headers.get("content-type", ""),
                    ),
                    "jsonrpc": self._jsonrpc_summary(
                        self.trace_logger.body_payload(bytes(response_body), response_headers.get("content-type", ""))
                    ),
                },
                trace_id=trace_id,
            )

    @staticmethod
    def _is_mcp_path(path: str) -> bool:
        normalized = (path or "/").rstrip("/") or "/"
        return normalized in {"/", "/mcp", "/messages"} or normalized.endswith("/mcp") or normalized.endswith("/messages")

    @staticmethod
    def _trace_id(payload: Any) -> str:
        if isinstance(payload, dict):
            request_id = payload.get("id")
            if request_id is not None:
                return f"jsonrpc:{request_id}"
        return ""

    @staticmethod
    def _jsonrpc_summary(payload: Any) -> Any:
        if isinstance(payload, list):
            return [DebugTraceASGIMiddleware._jsonrpc_summary(item) for item in payload]
        if not isinstance(payload, dict):
            return {}
        params = payload.get("params")
        tool_name = ""
        if isinstance(params, dict):
            tool_name = str(params.get("name") or "")
        return {
            "id": payload.get("id"),
            "method": payload.get("method"),
            "tool": tool_name,
        }
