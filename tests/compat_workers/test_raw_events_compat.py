from datetime import datetime

from raw_events import RawEventStore, strip_raw_client_context


def _config(tmp_path):
    return {
        "buckets_dir": str(tmp_path / "buckets"),
        "state_dir": str(tmp_path / "state"),
    }


def test_raw_event_store_keeps_only_original_user_and_assistant_turns(tmp_path):
    store = RawEventStore(_config(tmp_path))

    result = store.ingest(
        [
            {
                "source_event_id": "u1",
                "role": "user",
                "text": "This original sentence must be retained.",
                "created_at": "2026-06-22T10:00:00+08:00",
                "conversation_id": "c1",
            },
            {"source_event_id": "tool1", "role": "tool", "text": "tool result"},
            {"source_event_id": "system1", "role": "system", "text": "system prompt"},
            {
                "source_event_id": "inj1",
                "role": "assistant",
                "text": "Live private context for the current turn. Use it quietly when relevant.",
            },
        ],
        source="script",
    )

    assert result["inserted"] == 1
    assert result["rejected"] == 3
    duplicate = store.ingest(
        [
            {
                "source_event_id": "u1",
                "role": "user",
                "text": "This original sentence must be retained.",
                "created_at": "2026-06-22T10:00:00+08:00",
                "conversation_id": "c1",
            }
        ],
        source="script",
    )
    assert duplicate["duplicate"] == 1
    assert store.search("original", source="script")["items"][0]["role"] == "user"


def test_raw_event_store_strips_client_context_and_persists_sqlite_format(tmp_path):
    config = _config(tmp_path)
    store = RawEventStore(config)
    raw = (
        "User text "
        '<attachment id="message_insert_extra_bundle_1" filename="Time:11:07" '
        'type="text/plain">current time 2026-06-22</attachment>'
        " ending"
    )

    result = store.ingest(
        [{"source_event_id": "u-context", "role": "user", "text": raw}],
        source="gateway",
    )

    assert result["inserted"] == 1
    assert strip_raw_client_context(raw) == "User text ending"
    reopened = RawEventStore(config)
    stored = reopened.search("User text", source="gateway")["items"][0]
    assert stored["text"] == "User text ending"
    assert "attachment" not in stored["text"]


def test_raw_event_store_filters_date_and_session_without_losing_raw_payload(tmp_path):
    store = RawEventStore(_config(tmp_path))
    store.ingest(
        [
            {
                "source_event_id": "a1",
                "role": "assistant",
                "text": "first retained turn",
                "created_at": "2026-07-01T10:00:00+08:00",
                "session_id": "window-a",
                "metadata": {"provider": "anthropic"},
            },
            {
                "source_event_id": "b1",
                "role": "assistant",
                "text": "second retained turn",
                "created_at": "2026-07-02T10:00:00+08:00",
                "session_id": "window-b",
            },
        ],
        source="gateway",
    )

    rows = store.list_events_between(
        start_at=datetime.fromisoformat("2026-07-01T00:00:00+08:00"),
        end_at=datetime.fromisoformat("2026-07-01T23:59:59+08:00"),
        source="gateway",
        session_id="window-a",
    )

    assert [row["source_event_id"] for row in rows] == ["a1"]
    assert rows[0]["metadata"] == {"provider": "anthropic"}
