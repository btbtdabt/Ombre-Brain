from typing import Any

from edge_records import load_jsonl_records, upsert_by_confidence
from tools._common import apply_memory_detail_updates, apply_plan_change_log
from tools.hold.metadata import normalize_hold_metadata


def test_edge_upsert_keeps_higher_confidence_record() -> None:
    records = [{"key": "same", "confidence": 0.8}]

    def same(left, right):
        return left["key"] == right["key"]

    upsert_by_confidence(records, {"key": "same", "confidence": 0.4}, same)
    assert records == [{"key": "same", "confidence": 0.8}]

    upsert_by_confidence(records, {"key": "same", "confidence": 0.9}, same)
    assert records == [{"key": "same", "confidence": 0.9}]


def test_edge_jsonl_loader_skips_invalid_records(tmp_path) -> None:
    path = tmp_path / "edges.jsonl"
    path.write_text('{"key":"ok"}\nnot-json\n[]\n', encoding="utf-8")

    records = load_jsonl_records(
        str(path),
        lambda record: record if record.get("key") else None,
    )

    assert records == [{"key": "ok"}]


def test_plan_change_log_is_applied_once_for_shared_write_paths() -> None:
    bucket = {"metadata": {"type": "plan", "status": "open", "change_log": []}}
    updates: dict[str, Any] = {"status": "done", "content": "finished"}

    apply_plan_change_log(bucket, updates)

    assert [entry["action"] for entry in updates["change_log"]] == ["status", "edit"]
    assert updates["change_log"][0]["from"] == "open"
    assert updates["change_log"][0]["to"] == "done"


def test_hold_metadata_normalization_preserves_explicit_values_and_tag_order() -> None:
    metadata = normalize_hold_metadata(
        {
            "domain": ["关系"],
            "valence": 0.2,
            "arousal": 0.4,
            "tags": ["shared", "old"],
            "suggested_name": "title",
        },
        ["shared", "new"],
        0.9,
        -1,
    )

    assert metadata.domains == ["关系"]
    assert metadata.valence == 0.9
    assert metadata.arousal == 0.4
    assert metadata.tags == ["shared", "old", "new"]
    assert metadata.suggested_name == "title"


def test_memory_detail_updates_share_trace_clear_and_append_semantics() -> None:
    updates = {}

    apply_memory_detail_updates(
        updates,
        why_remembered="\\clear",
        meaning_append="  learned  ",
        meaning_replace=None,
        media_append=["photo"],
        media_replace=None,
    )

    assert updates == {
        "why_remembered": "",
        "meaning_append": "learned",
        "media_append": ["photo"],
    }
