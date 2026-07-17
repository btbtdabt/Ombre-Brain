from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from starlette.requests import Request


class RecordingMCP:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Callable[..., Any]] = {}

    def custom_route(self, path: str, methods: list[str]):
        def decorator(handler):
            for method in methods:
                key = (method.upper(), path)
                if key in self.routes:
                    raise AssertionError(f"duplicate route registration: {key}")
                self.routes[key] = handler
            return handler

        return decorator


def request_for(
    method: str,
    path: str,
    *,
    json_body: Any = None,
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
    path_params: dict[str, str] | None = None,
    query_string: str = "",
) -> Request:
    if raw_body is None:
        raw_body = b"" if json_body is None else json.dumps(json_body).encode("utf-8")
    normalized_headers = {
        key.lower(): value for key, value in (headers or {}).items()
    }
    if json_body is not None:
        normalized_headers.setdefault("content-type", "application/json")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_string.encode("ascii"),
        "headers": [
            (key.encode("latin-1"), value.encode("latin-1"))
            for key, value in normalized_headers.items()
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "path_params": path_params or {},
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw_body, "more_body": False}

    return Request(scope, receive)


def response_json(response) -> Any:
    return json.loads(response.body.decode("utf-8"))
