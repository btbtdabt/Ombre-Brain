from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _alignment_module() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "check_production_alignment.py"
    spec = importlib.util.spec_from_file_location("check_production_alignment", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_alignment_http_helper_forwards_explicit_probe_headers(monkeypatch) -> None:
    module = _alignment_module()
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout):
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    status, payload = module.read_json(
        "https://gateway.example/v1/chat/completions",
        token="secret",
        method="POST",
        body={"messages": []},
        extra_headers={
            "X-Ombre-Diagnostic-Probe": "production-alignment",
            "X-Ombre-Session-Id": "production-alignment-test",
        },
    )

    assert status == 200
    assert payload == {}
    assert captured["headers"]["x-ombre-diagnostic-probe"] == "production-alignment"
    assert captured["headers"]["x-ombre-session-id"] == "production-alignment-test"
    assert captured["headers"]["authorization"] == "Bearer secret"
