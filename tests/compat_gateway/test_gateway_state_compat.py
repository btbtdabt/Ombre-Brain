import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest


def test_gateway_state_store_preserves_production_schema(tmp_path):
    from gateway_state import GatewayStateStore

    db_path = tmp_path / "state" / "gateway.db"
    GatewayStateStore(str(db_path))

    expected_columns = {
        "request_rounds": ["session_id", "round_id", "completed_at"],
        "injected_buckets": [
            "session_id",
            "round_id",
            "bucket_id",
            "injected_at",
        ],
        "injection_debug": [
            "id",
            "session_id",
            "round_id",
            "created_at",
            "payload_json",
        ],
        "recent_context_injections": ["session_id", "round_id", "injected_at"],
        "conversation_turns": [
            "id",
            "profile_id",
            "session_id",
            "round_id",
            "created_at",
            "user_text",
            "assistant_text",
            "model",
            "client",
            "route",
        ],
        "upstream_usage": [
            "id",
            "session_id",
            "round_id",
            "created_at",
            "model",
            "route",
            "prompt_tokens",
            "completion_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
            "cached_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "usage_json",
        ],
    }
    expected_indexes = {
        "idx_injected_lookup",
        "idx_injection_debug_lookup",
        "idx_recent_context_lookup",
        "idx_conversation_turns_recent",
        "idx_conversation_turns_session",
        "idx_upstream_usage_lookup",
    }

    with sqlite3.connect(db_path) as connection:
        for table, columns in expected_columns.items():
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
            assert [row[1] for row in rows] == columns

        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }

    assert expected_indexes <= indexes


def test_gateway_state_rounds_and_cooldowns_survive_reopen(tmp_path):
    from gateway_state import GatewayStateStore

    db_path = tmp_path / "gateway_state.db"
    store = GatewayStateStore(str(db_path))
    origin = datetime(2026, 4, 20, 12, 0, 0)

    assert store.record_success("sess-a", ["bucket-a"], completed_at=origin) == 1
    assert store.record_success("sess-a", ["bucket-b"], completed_at=origin) == 2

    reopened = GatewayStateStore(str(db_path))
    assert reopened.get_current_round("sess-a") == 2
    assert reopened.get_recent_bucket_ids("sess-a", 1) == {"bucket-b"}
    assert reopened.get_recent_bucket_ids("sess-a", 2) == {"bucket-a", "bucket-b"}
    assert reopened.get_last_success_at("sess-a") == origin

    assert reopened.get_cooldown_multiplier(
        "sess-a",
        "bucket-a",
        6,
        0.3,
        now=origin + timedelta(hours=3),
    ) == pytest.approx(0.65, rel=1e-3)

    reopened.record_recent_context_injection(
        "sess-a",
        2,
        injected_at=origin + timedelta(minutes=5),
    )
    assert reopened.get_last_recent_context_at("sess-a") == origin + timedelta(minutes=5)


def test_gateway_state_allocates_unique_rounds_for_concurrent_successes(tmp_path):
    from gateway_state import GatewayStateStore

    store = GatewayStateStore(str(tmp_path / "gateway_state.db"))
    original_connect = store._connect
    simultaneous_reads = threading.Barrier(2)

    class CoordinatedCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            row = self.cursor.fetchone()
            simultaneous_reads.wait(timeout=5)
            return row

    class CoordinatedConnection:
        def __init__(self):
            self.connection = original_connect()
            self.immediate_transaction = False

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def execute(self, sql, parameters=()):
            normalized = " ".join(sql.split()).upper()
            if normalized == "BEGIN IMMEDIATE":
                self.immediate_transaction = True
            cursor = self.connection.execute(sql, parameters)
            if normalized.startswith("SELECT COALESCE(MAX(ROUND_ID)") and not self.immediate_transaction:
                return CoordinatedCursor(cursor)
            return cursor

        def executemany(self, sql, parameters):
            return self.connection.executemany(sql, parameters)

    store._connect = CoordinatedConnection

    with ThreadPoolExecutor(max_workers=2) as executor:
        rounds = list(
            executor.map(
                lambda bucket_id: store.record_success("sess-race", [bucket_id]),
                ("bucket-a", "bucket-b"),
            )
        )

    store._connect = original_connect
    assert sorted(rounds) == [1, 2]
    assert store.get_current_round("sess-race") == 2


def test_gateway_state_injection_debug_is_bounded_and_can_hide_context(tmp_path):
    from gateway_state import GatewayStateStore

    store = GatewayStateStore(str(tmp_path / "gateway_state.db"))
    store.record_injection_debug(
        "sess-a",
        1,
        {
            "marker": "old",
            "stable_context": "old stable",
            "dynamic_context": "old dynamic",
        },
        max_entries=1,
    )
    debug_id = store.record_injection_debug(
        "sess-a",
        2,
        {
            "marker": "new",
            "stable_context": "new stable",
            "dynamic_context": "new dynamic",
        },
        max_entries=1,
    )

    rows = store.list_injection_debug(
        session_id="sess-a",
        limit=20,
        include_context=False,
    )

    assert [row["id"] for row in rows] == [debug_id]
    assert rows[0]["round_id"] == 2
    assert rows[0]["payload"] == {"marker": "new"}


def test_gateway_state_conversation_turns_round_trip_by_profile_and_date(tmp_path):
    from gateway_state import GatewayStateStore

    store = GatewayStateStore(str(tmp_path / "gateway_state.db"))
    created_at = datetime(2026, 7, 15, 18, 30, tzinfo=timezone.utc)
    store.record_conversation_turn(
        profile_id="primary",
        session_id="sess-a",
        round_id=3,
        user_text="remember this",
        assistant_text="remembered",
        model="claude-opus-4-8-native",
        client="compat-test",
        route="/v1/messages",
        created_at=created_at,
    )

    recent = store.list_recent_conversation_turns(
        profile_id="primary",
        session_id="sess-a",
        limit=5,
        hours=24 * 365,
    )
    ranged = store.list_conversation_turns_between(
        profile_id="primary",
        start_at=created_at - timedelta(minutes=1),
        end_at=created_at + timedelta(minutes=1),
        limit=5,
    )

    assert recent == ranged
    assert recent[0] == {
        "id": recent[0]["id"],
        "profile_id": "primary",
        "session_id": "sess-a",
        "round_id": 3,
        "created_at": "2026-07-15T18:30:00+00:00",
        "user_text": "remember this",
        "assistant_text": "remembered",
        "model": "claude-opus-4-8-native",
        "client": "compat-test",
        "route": "/v1/messages",
    }


def test_gateway_state_upstream_usage_preserves_openai_and_anthropic_tokens(tmp_path):
    from gateway_state import GatewayStateStore

    store = GatewayStateStore(str(tmp_path / "gateway_state.db"))
    usage = {
        "prompt_tokens": 101,
        "completion_tokens": 12,
        "prompt_cache_hit_tokens": 30,
        "prompt_cache_miss_tokens": 71,
        "prompt_tokens_details": {"cached_tokens": 30},
        "cache_read_input_tokens": 22,
        "cache_creation_input_tokens": 9,
    }
    usage_id = store.record_upstream_usage(
        session_id="sess-a",
        round_id=4,
        model="claude-opus-4-8-native",
        route="/v1/messages",
        usage=usage,
    )

    rows = store.list_upstream_usage(session_id="sess-a", limit=5)

    assert rows == [
        {
            "id": usage_id,
            "session_id": "sess-a",
            "round_id": 4,
            "created_at": rows[0]["created_at"],
            "model": "claude-opus-4-8-native",
            "route": "/v1/messages",
            "prompt_tokens": 101,
            "completion_tokens": 12,
            "prompt_cache_hit_tokens": 30,
            "prompt_cache_miss_tokens": 71,
            "cached_tokens": 30,
            "cache_read_input_tokens": 22,
            "cache_creation_input_tokens": 9,
            "usage": usage,
        }
    ]
