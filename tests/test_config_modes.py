import pytest

from config_modes import (
    normalize_direct_render_mode,
    normalize_retrieval_mode,
    normalize_thinking_mode,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "auto"),
        (" compact ", "compact"),
        ("full", "full"),
        ("unsupported", "auto"),
    ],
)
def test_normalize_direct_render_mode(value, expected) -> None:
    assert normalize_direct_render_mode(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "graph"),
        ("graph", "graph"),
        ("bucket", "bucket"),
        ("legacy", "bucket"),
        ("unsupported", "graph"),
    ],
)
def test_normalize_retrieval_mode_preserves_legacy_bucket_alias(value, expected) -> None:
    assert normalize_retrieval_mode(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("enabled", "enabled"),
        ("thinking", "enabled"),
        ("non_thinking", "disabled"),
        ("non-thinking", "disabled"),
        ("unsupported", ""),
    ],
)
def test_normalize_thinking_mode_accepts_all_retained_aliases(value, expected) -> None:
    assert normalize_thinking_mode(value) == expected
