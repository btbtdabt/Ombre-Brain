from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import frontmatter
import pytest
import yaml

from gateway_state import GatewayStateStore
from persona_engine import PersonaStateEngine
from raw_events import RawEventStore
from scripts.cleanup_known_test_pollution import (
    LEGACY_TEST_BUCKETS,
    CleanupPlan,
    TestTurn as CleanupTestTurn,
    apply_bucket_cleanup,
    apply_gateway_cleanup,
    apply_persona_cleanup,
    apply_raw_cleanup,
    discover_gateway_turns,
    discover_persona_targets,
    discover_raw_events,
    is_historical_probe_text,
    scrub_portrait_state,
)


TARGET_TIME = "2026-08-01T12:00:00+00:00"


def _insert_gateway_round(
    connection: sqlite3.Connection,
    *,
    profile_id: str,
    session_id: str,
    round_id: int,
    user_text: str,
) -> None:
    connection.execute(
        "INSERT INTO request_rounds VALUES (?, ?, ?)",
        (session_id, round_id, TARGET_TIME),
    )
    connection.execute(
        "INSERT INTO injected_buckets VALUES (?, ?, ?, ?)",
        (session_id, round_id, f"bucket-{round_id}", TARGET_TIME),
    )
    connection.execute(
        "INSERT INTO injection_debug (session_id, round_id, created_at, payload_json) "
        "VALUES (?, ?, ?, '{}')",
        (session_id, round_id, TARGET_TIME),
    )
    connection.execute(
        "INSERT INTO recent_context_injections VALUES (?, ?, ?)",
        (session_id, round_id, TARGET_TIME),
    )
    connection.execute(
        """
        INSERT INTO conversation_turns
        (profile_id, session_id, round_id, created_at, user_text, assistant_text)
        VALUES (?, ?, ?, ?, ?, 'ok')
        """,
        (profile_id, session_id, round_id, TARGET_TIME, user_text),
    )
    connection.execute(
        """
        INSERT INTO upstream_usage
        (session_id, round_id, created_at, usage_json)
        VALUES (?, ?, ?, '{}')
        """,
        (session_id, round_id, TARGET_TIME),
    )


def test_probe_recognition_is_closed_and_exact() -> None:
    assert is_historical_probe_text("alignment check")
    assert is_historical_probe_text("alignment check 42: reply ok only")
    assert is_historical_probe_text("opus5 budget probe 7: reply ok only")
    assert not is_historical_probe_text("please run an alignment check")
    assert not is_historical_probe_text("reply ok only after reading this real request")


def test_gateway_cleanup_keeps_main_round_fences_and_real_turns(tmp_path: Path) -> None:
    db_path = tmp_path / "gateway_state.db"
    GatewayStateStore(str(db_path))
    with sqlite3.connect(db_path) as connection:
        _insert_gateway_round(
            connection,
            profile_id="amy",
            session_id="main",
            round_id=1,
            user_text="a real message",
        )
        _insert_gateway_round(
            connection,
            profile_id="amy",
            session_id="main",
            round_id=2,
            user_text="alignment check 2: reply ok only",
        )
        _insert_gateway_round(
            connection,
            profile_id="amy",
            session_id="relay-test",
            round_id=1,
            user_text="synthetic relay smoke",
        )

    turns = discover_gateway_turns(db_path)
    assert {(turn.session_id, turn.round_id) for turn in turns} == {
        ("main", 2),
        ("relay-test", 1),
    }
    apply_gateway_cleanup(db_path, turns)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT round_id FROM request_rounds WHERE session_id = 'main' ORDER BY round_id"
        ).fetchall() == [(1,), (2,)]
        assert connection.execute(
            "SELECT user_text FROM conversation_turns ORDER BY id"
        ).fetchall() == [("a real message",)]
        for table in (
            "request_rounds",
            "injected_buckets",
            "injection_debug",
            "recent_context_injections",
            "conversation_turns",
            "upstream_usage",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE session_id = 'relay-test'"
            ).fetchone()[0] == 0


def test_gateway_cleanup_rejects_shared_round_collision(tmp_path: Path) -> None:
    db_path = tmp_path / "gateway_state.db"
    GatewayStateStore(str(db_path))
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO request_rounds VALUES ('main', 2, ?)",
            (TARGET_TIME,),
        )
        for profile_id, user_text in (
            ("amy", "alignment check 2: reply ok only"),
            ("other", "a real message on the same shared round"),
        ):
            connection.execute(
                """
                INSERT INTO conversation_turns
                (profile_id, session_id, round_id, created_at, user_text, assistant_text)
                VALUES (?, 'main', 2, ?, ?, 'ok')
                """,
                (profile_id, TARGET_TIME, user_text),
            )

    with pytest.raises(RuntimeError, match="collision"):
        discover_gateway_turns(db_path)


def test_raw_cleanup_removes_paired_events_and_rebuilds_fts(tmp_path: Path) -> None:
    config = {
        "buckets_dir": str(tmp_path / "buckets"),
        "state_dir": str(tmp_path / "state"),
    }
    store = RawEventStore(config)
    metadata = {"profile_id": "amy", "round_id": 2, "model": "test"}
    result = store.ingest(
        [
            {
                "source_event_id": "amy:main:2:user",
                "role": "user",
                "text": "alignment check 2: reply ok only",
                "created_at": TARGET_TIME,
                "session_id": "main",
                "metadata": metadata,
            },
            {
                "source_event_id": "amy:main:2:assistant",
                "role": "assistant",
                "text": "ok",
                "created_at": TARGET_TIME,
                "session_id": "main",
                "metadata": metadata,
            },
            {
                "source_event_id": "amy:main:3:user",
                "role": "user",
                "text": "real retained event",
                "created_at": TARGET_TIME,
                "session_id": "main",
                "metadata": {"profile_id": "amy", "round_id": 3},
            },
        ],
        source="gateway",
    )
    assert result["inserted"] == 3
    turn = CleanupTestTurn(
        row_id=1,
        profile_id="amy",
        session_id="main",
        round_id=2,
        created_at=TARGET_TIME,
        user_text="alignment check 2: reply ok only",
        assistant_text="ok",
    )
    db_path = Path(store.db_path)
    ids = discover_raw_events(db_path, [turn])
    assert len(ids) == 2

    report = apply_raw_cleanup(db_path, ids)
    assert report["raw_events"] == 2
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT text FROM raw_events").fetchall() == [
            ("real retained event",)
        ]
        assert connection.execute("SELECT COUNT(*) FROM raw_events_fts").fetchone()[0] == 1


def test_persona_cleanup_preserves_global_state_and_resets_exact_transient(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "persona_state.db"
    PersonaStateEngine(
        {
            "buckets_dir": str(tmp_path / "buckets"),
            "state_dir": str(tmp_path),
            "persona": {"enabled": False},
        },
        db_path=str(db_path),
    )
    turn = CleanupTestTurn(
        row_id=1,
        profile_id="amy",
        session_id="main",
        round_id=2,
        created_at=TARGET_TIME,
        user_text="alignment check 2: reply ok only",
        assistant_text="ok",
    )
    from scripts.cleanup_known_test_pollution import _exchange_hash

    exchange_hash = _exchange_hash(turn)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO persona_global_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("amy", 0.7, 0.6, 0.5, 0.8, 0.2, 0.9, 0.4, 0.1, 0.95, TARGET_TIME),
        )
        connection.execute(
            """
            INSERT INTO persona_session_state
            (profile_id, session_id, valence, arousal, tenderness, possessiveness,
             longing, security, protective_drive, libido, mood_label,
             session_defensiveness, residue, inner_thought, updated_at)
            VALUES ('amy', 'main', 0.55, 0.31, 0.62, 0.24, 0.34, 0.68,
                    0.52, 0.18, 'neutral', 0.1, 'test residue', 'test thought', ?)
            """,
            (TARGET_TIME,),
        )
        connection.execute(
            """
            INSERT INTO persona_session_state
            (profile_id, session_id, valence, arousal, mood_label,
             session_defensiveness, updated_at)
            VALUES ('amy', 'relay-test', 0.5, 0.3, 'neutral', 0.1, ?)
            """,
            (TARGET_TIME,),
        )
        connection.execute(
            """
            INSERT INTO persona_events
            (profile_id, session_id, message_hash, exchange_hash, user_excerpt,
             assistant_excerpt, mood_label, residue, inner_thought, created_at)
            VALUES ('amy', 'main', 'm1', ?, ?, 'ok', 'neutral',
                    'test residue', 'test thought', ?)
            """,
            (exchange_hash, turn.user_text, TARGET_TIME),
        )
        connection.execute(
            """
            INSERT INTO persona_exchange_log
            (profile_id, session_id, exchange_hash, created_at)
            VALUES ('amy', 'main', ?, ?)
            """,
            (exchange_hash, TARGET_TIME),
        )

    event_ids, exchange_ids, state_rowids, resets = discover_persona_targets(
        db_path, [turn]
    )
    plan = CleanupPlan(
        persona_event_ids=event_ids,
        persona_exchange_log_ids=exchange_ids,
        persona_session_state_rowids=state_rowids,
        persona_transient_resets=resets,
    )
    apply_persona_cleanup(db_path, plan)

    with sqlite3.connect(db_path) as connection:
        global_state = connection.execute(
            "SELECT affinity, trust FROM persona_global_state WHERE profile_id = 'amy'"
        ).fetchone()
        assert global_state == (0.9, 0.95)
        main_state = connection.execute(
            """
            SELECT valence, arousal, mood_label, residue, inner_thought
            FROM persona_session_state WHERE profile_id = 'amy' AND session_id = 'main'
            """
        ).fetchone()
        assert main_state == (0.55, 0.31, "warm_neutral", "", "")
        assert connection.execute(
            "SELECT COUNT(*) FROM persona_session_state WHERE session_id = 'relay-test'"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM persona_events").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM persona_exchange_log").fetchone()[0] == 0


def test_portrait_cleanup_preserves_real_segment_in_mixed_handoff() -> None:
    state = {
        "updated_at": "2026-08-20T00:00:00+00:00",
        "portrait": {
            "relationship": {
                "recent_buffer": [
                    {
                        "text": "alignment check residue",
                        "created_at": "2026-07-18T08:55:41+00:00",
                    },
                    {
                        "text": "real relationship observation",
                        "created_at": "2026-07-18T09:00:00+00:00",
                    },
                ]
            }
        },
        "daily_summaries": {"2026-08-20": "alignment check only"},
        "handoff_recent_summaries": {
            "2026-06-14": (
                "alignment check 1: reply ok only；real health conversation"
                "。关系天气：steady"
            )
        },
        "recent_activities": [],
        "recent_timeline": [],
    }

    cleaned, removed = scrub_portrait_state(state)
    assert removed == 3
    assert cleaned["portrait"]["relationship"]["recent_buffer"] == [
        {
            "text": "real relationship observation",
            "created_at": "2026-07-18T09:00:00+00:00",
        }
    ]
    assert "2026-08-20" not in cleaned["daily_summaries"]
    assert cleaned["handoff_recent_summaries"]["2026-06-14"] == (
        "real health conversation。关系天气：steady"
    )


def test_legacy_bucket_cleanup_uses_hard_delete_and_removes_projections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMBRE_EMBED_API_KEY", raising=False)
    buckets_dir = tmp_path / "buckets"
    state_dir = tmp_path / "state"
    archive_dir = buckets_dir / "archive"
    tombstone_dir = buckets_dir / ".tombstones"
    archive_dir.mkdir(parents=True)
    tombstone_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    bucket_id = next(iter(LEGACY_TEST_BUCKETS))
    marker = LEGACY_TEST_BUCKETS[bucket_id]
    post = frontmatter.Post(
        f"legacy synthetic body {marker}",
        id=bucket_id,
        name=f"legacy synthetic {marker}",
        type="dynamic",
        domain=["test"],
        tags=["test"],
        source_tool="grow",
    )
    bucket_path = archive_dir / f"legacy_{bucket_id}.md"
    bucket_path.write_text(frontmatter.dumps(post), encoding="utf-8")
    (tombstone_dir / f"{bucket_id}.json").write_text(
        json.dumps({"id": bucket_id}), encoding="utf-8"
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "buckets_dir": str(buckets_dir),
                "state_dir": str(state_dir),
                "embedding": {"enabled": True, "api_key": ""},
                "identity_semantics": {"enabled": False},
                "word_map": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )

    report = asyncio.run(
        apply_bucket_cleanup(
            buckets_dir,
            state_dir,
            config_path,
            [bucket_id],
        )
    )

    assert report["deleted"] == [bucket_id]
    assert not bucket_path.exists()
    assert not (tombstone_dir / f"{bucket_id}.json").exists()
    with sqlite3.connect(
        buckets_dir / "_ledger" / "projections" / "trace_catalog.sqlite3"
    ) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM traces WHERE trace_id = ?", (bucket_id,)
        ).fetchone()[0] == 0
