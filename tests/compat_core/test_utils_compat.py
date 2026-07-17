from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
import yaml

from utils import (
    LOCAL_TZ,
    atomic_update_yaml,
    bucket_content_for_recall,
    bucket_text_for_embedding,
    local_date_key,
    parse_first_json_value,
    parse_human_date_reference,
    same_path,
    strip_display_temperature_sections,
    strip_human_date_references,
    strip_temperature_meaning_lines,
)


def test_parse_first_json_value_accepts_wrapped_object_and_array() -> None:
    assert parse_first_json_value('prefix ```json\n{"ok":true}\n``` tail') == {
        "ok": True
    }
    assert parse_first_json_value('result: [{"name":"Amy"}] done') == [
        {"name": "Amy"}
    ]


def test_parse_first_json_value_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="no_json"):
        parse_first_json_value("memory_subject: user")


def test_human_date_helpers_preserve_topic_text() -> None:
    reference = datetime(2026, 6, 14, 12, 0, tzinfo=LOCAL_TZ)

    assert parse_human_date_reference("昨晚的海鲜", now=reference) == {
        "date": "2026-06-13",
        "label": "昨晚",
    }
    assert parse_human_date_reference("2026/6/12 的承诺", now=reference) == {
        "date": "2026-06-12",
        "label": "2026/6/12",
    }
    assert strip_human_date_references("昨晚的海鲜") == " 的海鲜"
    assert local_date_key("2026-06-12T23:30:00+00:00") == "2026-06-13"


def test_recall_and_embedding_text_exclude_display_only_sections() -> None:
    bucket = {
        "metadata": {"name": "[[Food|海鲜偏好]]"},
        "content": (
            "[[Amy]] likes seafood.\n\n"
            "### affect_anchor\nCmaj7 -> Am7\n\n"
            "### followup\nBuy dinner tomorrow."
        ),
    }

    assert bucket_content_for_recall(bucket) == "Amy likes seafood."
    assert bucket_text_for_embedding(bucket) == (
        "Title: Food|海鲜偏好\nContent: Amy likes seafood."
    )


def test_display_temperature_cleanup_keeps_factual_text() -> None:
    text = (
        "Dinner happened.\n\n"
        "### favorite_reason\nWarm memory.\n\n"
        "含义：very warm\n"
        "> Cmaj7 -> Am7 | 72 bpm\n"
        "### moment\n"
        "The factual ending remains."
    )

    assert strip_display_temperature_sections(text) == (
        "Dinner happened.\n\n### moment\nThe factual ending remains."
    )
    assert strip_temperature_meaning_lines(text).endswith(
        "The factual ending remains."
    )
    assert "72 bpm" not in strip_temperature_meaning_lines(text)


def test_atomic_update_yaml_serializes_read_modify_write(tmp_path) -> None:
    path = tmp_path / "state.yaml"
    path.write_text("count: 0\nkept: true\n", encoding="utf-8")

    def increment(_: int) -> None:
        def mutate(config: dict) -> None:
            config["count"] = int(config.get("count", 0)) + 1

        atomic_update_yaml(path, mutate)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(increment, range(32)))

    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "count": 32,
        "kept": True,
    }


def test_same_path_normalizes_equivalent_paths(tmp_path) -> None:
    direct = tmp_path / "vault" / "memory.md"
    dotted = tmp_path / "vault" / "." / "memory.md"

    assert same_path(str(direct), str(dotted)) is True
