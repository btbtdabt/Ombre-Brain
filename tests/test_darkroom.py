import pytest

from darkroom import DarkroomStore


def _store(tmp_path):
    return DarkroomStore(
        {
            "state_dir": str(tmp_path / "state"),
            "buckets_dir": str(tmp_path / "buckets"),
            "identity": {"ai_name": "Haven"},
        }
    )


def test_darkroom_enter_does_not_echo_note(tmp_path):
    store = _store(tmp_path)
    secret = "这是一句还没显影的暗房正文"

    result = store.enter(secret, completeness=0.4, mood="quiet", tags="暗房,未完成")

    assert result["status"] == "entered"
    assert result["visible_note"] == "Haven 进入了暗房。"
    assert secret not in str(result)
    assert result["completeness"] == {"previous": None, "current": 0.4}
    assert result["tags"] == ["暗房", "未完成"]


def test_darkroom_door_uses_configured_ai_name(tmp_path):
    store = DarkroomStore(
        {
            "state_dir": str(tmp_path / "state"),
            "buckets_dir": str(tmp_path / "buckets"),
            "identity": {"ai_name": "Ombre"},
        }
    )

    result = store.enter("名字也不该泄正文", completeness=0.3)
    status = store.status()

    assert result["visible_note"] == "Ombre 进入了暗房。"
    assert "钥匙只给 Ombre" in status["door"]
    assert "Haven" not in result["visible_note"]
    assert "Haven" not in status["door"]


def test_darkroom_status_is_door_only(tmp_path):
    store = _store(tmp_path)
    secret = "不能出现在门口状态里的句子"
    first = store.enter(secret, completeness=0.2)
    second = store.enter("第二条也不该回显", completeness=0.6, mood="developing")

    status = store.status()

    assert status["status"] == "ok"
    assert status["count"] == 2
    assert status["last_entry_id"] == second["entry_id"]
    assert status["previous_completeness"] == 0.2
    assert status["last_completeness"] == 0.6
    assert first["entry_id"] != second["entry_id"]
    assert secret not in str(status)


def test_darkroom_continue_anchor_stays_private(tmp_path):
    store = _store(tmp_path)
    old_secret = "上一条暗房里不该出门的句子"
    store.enter(old_secret, completeness=0.2)

    result = store.enter("新的暗房正文", mode="continue", completeness=0.5)

    assert result["mode"] == "continue"
    assert result["continuation_anchor_entries"] == 1
    assert old_secret not in str(result)
    assert old_secret not in str(store.status())


def test_darkroom_single_mode_has_no_continuation_anchor(tmp_path):
    store = _store(tmp_path)
    store.enter("上一条暗房正文", completeness=0.2)

    result = store.enter("单独写一条", mode="single", completeness=0.5)

    assert result["mode"] == "single"
    assert result["continuation_anchor_entries"] == 0


def test_darkroom_release_explicitly_returns_content(tmp_path):
    store = _store(tmp_path)
    secret = "这句显影以后可以被带出来"
    store.enter(secret, completeness=1.0, tags="ready")

    released = store.release("latest", reason="小雨 asked")

    assert released["status"] == "released"
    assert released["content"] == secret
    assert released["tags"] == ["ready"]
    assert store.status()["released_count"] == 1


def test_darkroom_status_defaults_to_active_entries(tmp_path):
    store = _store(tmp_path)
    active = store.enter("active door note", completeness=0.3)
    archived = store.enter("archived door note", completeness=0.9, visibility="archived")
    retracted = store.enter("retracted door note", completeness=1.0, visibility="retracted")

    status = store.status()

    assert active["visibility"] == "active"
    assert archived["visibility"] == "archived"
    assert retracted["visibility"] == "retracted"
    assert status["count"] == 1
    assert status["last_entry_id"] == active["entry_id"]
    assert status["last_completeness"] == 0.3
    assert "archived door note" not in str(status)
    assert "retracted door note" not in str(status)


def test_darkroom_release_latest_skips_archived_and_retracted(tmp_path):
    store = _store(tmp_path)
    active_secret = "active release note"
    store.enter(active_secret, completeness=0.7, tags="ready")
    store.enter("archived release note", completeness=1.0, visibility="archived")
    store.enter("retracted release note", completeness=1.0, visibility="retracted")

    released = store.release("latest", reason="release latest active")

    assert released["status"] == "released"
    assert released["content"] == active_secret
    assert store.status()["released_count"] == 1


def test_darkroom_legacy_entries_without_visibility_are_active(tmp_path):
    store = _store(tmp_path)
    legacy = {
        "id": "dr_legacy",
        "created_at": "2026-06-10T12:00:00+08:00",
        "note": "legacy active note",
        "mode": "continue",
        "completeness": 0.8,
        "previous_entry_id": "",
        "previous_completeness": None,
        "continuation_anchor": {},
        "mood": "old",
        "tags": ["legacy"],
        "source": "test",
    }
    store._append_jsonl_unlocked(store.entries_path, legacy)

    status = store.status()
    released = store.release("latest", reason="legacy active")

    assert status["count"] == 1
    assert status["last_entry_id"] == "dr_legacy"
    assert released["content"] == "legacy active note"


def test_darkroom_rejects_empty_note(tmp_path):
    store = _store(tmp_path)

    with pytest.raises(ValueError, match="note is empty"):
        store.enter("  ")
