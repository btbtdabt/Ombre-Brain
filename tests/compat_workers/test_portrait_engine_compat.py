import json
from datetime import datetime, timezone

import pytest

from portrait_engine import DailyPortraitMaintainer


def _engine(worker_config, tmp_path, **overrides):
    return DailyPortraitMaintainer(
        {
            **worker_config,
            "portrait": {
                "enabled": True,
                "state_path": str(tmp_path / "state" / "portrait_state.json"),
                **overrides,
            },
        }
    )


def test_portrait_prompt_and_json_contract_are_evidence_bound(worker_config, tmp_path):
    engine = _engine(worker_config, tmp_path)

    prompt = engine._prompt()
    parsed = engine._parse_json_object('```json\n{"recent_activity": []}\n```\ntail')

    assert "证据化记忆状态整理器" in prompt
    assert "中立、平实、具体" in prompt
    assert "这不是文学分析或关系评语" in prompt
    assert "输出前逐条自检" in prompt
    assert "bucket_id、日期、文件路径" in prompt
    assert parsed == {"recent_activity": []}
    assert engine._completion_options(
        max_tokens=300,
        temperature=0.1,
        json_response=True,
    )["response_format"] == {
        "type": "json_object"
    }


def test_recent_timeline_upsert_is_stable_and_persistent(worker_config, tmp_path):
    engine = _engine(worker_config, tmp_path)
    first = {
        "timeline_id": "daily_activity_summary:2026-07-04",
        "source": "daily_activity_summary",
        "scope": "doing",
        "text": "Continue the handoff timeline work.",
        "source_date": "2026-07-04",
        "source_dates": ["2026-07-04"],
        "timestamp": "2026-07-04T20:00:00+08:00",
        "confidence": 0.6,
        "source_turn_ids": [7],
        "source_event_ids": [101],
        "evidence": [{"session_id": "daily-chat"}],
    }
    second = {
        **first,
        "text": "Finished wiring the handoff timeline summary.",
        "timestamp": "2026-07-04T21:00:00+08:00",
        "source_turn_ids": [8],
        "source_event_ids": [102, 103],
    }

    engine.upsert_recent_timeline_item(first, "2026-07-04")
    engine.upsert_recent_timeline_item(second, "2026-07-04")

    reopened = _engine(worker_config, tmp_path)
    rows = reopened.load_state()["recent_timeline"]
    assert len(rows) == 1
    assert rows[0]["text"] == "Finished wiring the handoff timeline summary."
    assert rows[0]["source_turn_ids"] == [7, 8]
    assert rows[0]["source_event_ids"] == [101, 102, 103]
    assert rows[0]["count"] == 1
    assert reopened.has_recent_timeline_item(
        date_key="2026-07-04",
        source="daily_activity_summary",
        timeline_id="daily_activity_summary:2026-07-04",
    )


def test_recent_activity_and_handoff_sections_preserve_user_context(worker_config, tmp_path):
    engine = _engine(worker_config, tmp_path)

    result = engine.add_recent_activity(
        "Amy is validating the P0 worker migration.",
        source_date="2026-07-16",
    )
    sections = engine.build_handoff_sections(
        max_recent_items=3,
        now=datetime(2026, 7, 22, 12, tzinfo=timezone.utc),
    )

    assert result["status"] == "updated"
    assert "Amy is validating the P0 worker migration" in sections["current_focus"]
    assert "2026-07-16" in sections["current_focus"]


def test_portrait_state_merges_legacy_shape_without_losing_unknown_evidence(
    worker_config, tmp_path
):
    engine = _engine(worker_config, tmp_path)
    path = tmp_path / "state" / "portrait_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "last_run": "2026-07-01T00:00:00+00:00",
                "portrait": {
                    "user": {
                        "staging_pool": [
                            {
                                "text": "A retained observation.",
                                "evidence": [{"bucket_id": "memory-1"}],
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    state = engine.load_state()

    staging = state["portrait"]["user"]["staging_pool"]
    assert staging[0]["text"] == "A retained observation."
    assert staging[0]["evidence"] == [{"bucket_id": "memory-1"}]
    assert "persona" in state["portrait"]
    assert "recent_timeline" in state


def test_portrait_delete_and_dismissal_survive_reload(worker_config, tmp_path):
    engine = _engine(worker_config, tmp_path)
    engine.add_recent_activity("temporary activity", source_date="2026-07-16")
    state = engine.load_state()
    text = state["recent_activities"][0]["text"]

    result = engine.delete_state_item(
        area="recent_activities",
        text=text,
    )

    assert result["status"] == "deleted"
    reopened = _engine(worker_config, tmp_path).load_state()
    assert reopened["recent_activities"] == []
    assert reopened["dismissed_items"]


@pytest.mark.asyncio
async def test_initial_daily_portrait_requires_explicit_force(worker_config, tmp_path, bucket_mgr):
    engine = _engine(worker_config, tmp_path, auto_initial=False)

    skipped = await engine.maintain_daily(bucket_mgr, force=False)
    forced = await engine.maintain_daily(bucket_mgr, force=True)

    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "initial_requires_manual"
    assert forced["status"] == "skipped"
    assert forced["reason"] == "generator_unavailable"
    assert engine.load_state()["runs"] == []


def test_stable_scope_lock_uses_revision_guard(worker_config, tmp_path):
    engine = _engine(worker_config, tmp_path)
    state = engine.load_state()
    revision = state["portrait"]["user"]["stable_revision"]

    locked = engine.set_stable_lock("user", True, expected_revision=revision)

    assert locked["status"] == "updated"
    assert engine.load_state()["portrait"]["user"]["stable_locked"] is True
    conflict = engine.set_stable_lock("user", False, expected_revision=revision + 1)
    assert conflict["status"] == "conflict"
