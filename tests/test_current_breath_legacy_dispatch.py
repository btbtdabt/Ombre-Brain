from __future__ import annotations

from typing import Any

import pytest

from tools.current import memory as current_memory


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"tags": "allowed"}, {"tags": "allowed", "catalog": False, "importance_min": -1}),
        ({"catalog": True}, {"tags": "", "catalog": True, "importance_min": -1}),
        ({"importance_min": 9}, {"tags": "", "catalog": False, "importance_min": 9}),
    ],
)
async def test_current_breath_keeps_legacy_dispatch_for_tag_catalog_and_importance_modes(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    captured: dict[str, object] = {}

    async def fake_dispatch(**dispatch_kwargs):
        captured.update(dispatch_kwargs)
        return "legacy-dispatch"

    async def fake_ensure_decay_started():
        return None

    monkeypatch.setattr(current_memory, "ensure_decay_started", fake_ensure_decay_started)
    monkeypatch.setattr(current_memory, "p0_breath_dispatch", fake_dispatch)

    result = await current_memory.breath(query="quartz marker", **kwargs)

    assert result == "legacy-dispatch"
    assert captured["query"] == "quartz marker"
    assert captured["tags"] == expected["tags"]
    assert captured["catalog"] is expected["catalog"]
    assert captured["importance_min"] == expected["importance_min"]
