import json

from recall_diagnostics import RecallDiagnosticsLogger


def test_recall_diagnostics_writes_bounded_jsonl_events(tmp_path):
    logger = RecallDiagnosticsLogger(
        {
            "state_dir": str(tmp_path),
            "recall_diagnostics": {
                "enabled": "true",
                "max_candidates": 999,
                "max_text_chars": -1,
            },
        }
    )

    logger.write({"query": "猫", "candidates": [{"id": "cat"}]})

    payload = json.loads((tmp_path / "recall_diagnostics.jsonl").read_text(encoding="utf-8"))
    assert payload["schema"] == "ombre.recall_diagnostics.v1"
    assert payload["query"] == "猫"
    assert payload["timestamp"]
    assert logger.max_candidates == 100
    assert logger.max_text_chars == 0


def test_disabled_recall_diagnostics_does_not_create_a_log(tmp_path):
    logger = RecallDiagnosticsLogger({"state_dir": str(tmp_path)})

    logger.write({"query": "ignored"})

    assert not (tmp_path / "recall_diagnostics.jsonl").exists()

