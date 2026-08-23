#!/usr/bin/env python3
"""Remove exact, historical Codex smoke/probe data from an Ombre deployment.

The command is dry-run by default.  It deliberately uses a closed manifest of
session IDs, messages, bucket IDs, and reminder IDs instead of fuzzy matching.
Mixed real sessions keep their ``request_rounds`` rows as ordinal fences.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import frontmatter

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


HISTORICAL_PROBE_CUTOFF = "2026-08-22T00:00:00+00:00"

# These are deployment validation sessions created by Codex, the relay smoke
# suite, or the old dashboard preview.  They are exact historical identifiers,
# not prefixes supplied by the operator.
SYNTHETIC_SESSION_IDS = frozenset(
    {
        "codex-debug-stream-20260613",
        "codex-gateway-openai-1784341060147",
        "codex-gateway-smoke-1784340599872",
        "codex-gateway-smoke-1784340625643",
        "codex-gateway-tool-1784340674915",
        "codex-gateway-tool-1784340692043",
        "codex-persona-live",
        "codex-relay-loop-1784340983614",
        "codex-smoke-20260613125552",
        "codex-smoke-native-inspect",
        "codex-smoke-stream",
        "codex-temperature-compat-smoke",
        "dashboard-preview",
        "opus5-verification-20260806",
        "probe",
        "probe-live-config",
        "probe-max-token",
        "production-alignment-1787522167799086500",
        "production-smoke-1784350706",
        "relay-test",
        "relay-test-gemini",
    }
)

LEGACY_TEST_BUCKETS = {
    "9eded7951adf": "CODEX_SMOKE_20260718020107_4fg3hi_GROW",
    "50659384861b": "CODEX_SMOKE_20260718020107_4fg3hi_PLAN",
    "9d9a75361142": "CODEX_SMOKE_20260718020107_4fg3hi_LETTER",
    "7a52ee7090ab": "CODEX_SMOKE_20260718020107_4fg3hi_PROFILE",
}

KNOWN_TEST_REMINDERS = {
    "44d919b34e784d6b": "CODEX_SMOKE_20260718020107_4fg3hi_REMINDER",
}

PORTRAIT_MARKERS = (
    "alignment check",
    "reply ok only",
    "budget probe",
    "production-alignment",
    "gateway_smoke_20260718020922047_bfu6pf",
    "codex_smoke_20260718020107_4fg3hi",
    "health check coordinator route",
)

KNOWN_TEST_DAILY_SUMMARY_DATES = frozenset(
    {"2026-08-06", "2026-08-15", "2026-08-20"}
)

KNOWN_TEST_HANDOFF_DATES = frozenset(
    {
        "2026-06-14",
        "2026-06-17",
        "2026-07-08",
        "2026-07-09",
        "2026-07-16",
        "2026-07-19",
        "2026-07-21",
        "2026-07-22",
        "2026-08-06",
        "2026-08-15",
        "2026-08-20",
    }
)

PROBE_TEXT_RE = re.compile(
    r"(?:"
    r"alignment check(?:\s+\d+)?:?\s*(?:reply ok only)?"
    r"|reply ok only"
    r"|health check coordinator route:\s*reply ok only"
    r"|opus5 budget probe\s+\d+:\s*reply ok only"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TestTurn:
    row_id: int
    profile_id: str
    session_id: str
    round_id: int
    created_at: str
    user_text: str
    assistant_text: str


@dataclass
class CleanupPlan:
    turns: list[TestTurn] = field(default_factory=list)
    raw_event_ids: list[int] = field(default_factory=list)
    persona_event_ids: list[int] = field(default_factory=list)
    persona_exchange_log_ids: list[int] = field(default_factory=list)
    persona_session_state_rowids: list[int] = field(default_factory=list)
    persona_transient_resets: list[tuple[str, str, str, str]] = field(
        default_factory=list
    )
    portrait_marker_count: int = 0
    reminder_ids: list[str] = field(default_factory=list)
    legacy_bucket_ids: list[str] = field(default_factory=list)

    def report(self) -> dict[str, Any]:
        main_turns = sum(1 for turn in self.turns if turn.session_id == "main")
        synthetic_turns = len(self.turns) - main_turns
        return {
            "gateway_turns": len(self.turns),
            "gateway_main_probe_turns": main_turns,
            "gateway_synthetic_turns": synthetic_turns,
            "raw_events": len(self.raw_event_ids),
            "persona_events": len(self.persona_event_ids),
            "persona_exchange_logs": len(self.persona_exchange_log_ids),
            "persona_session_states": len(self.persona_session_state_rowids),
            "persona_transient_resets": len(self.persona_transient_resets),
            "portrait_markers": self.portrait_marker_count,
            "reminders": len(self.reminder_ids),
            "legacy_bucket_ids": self.legacy_bucket_ids,
        }


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _quick_check(path: Path) -> str:
    with _connect(path, read_only=True) as connection:
        return str(connection.execute("PRAGMA quick_check").fetchone()[0])


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_historical_probe_text(value: str) -> bool:
    """Recognize only the exact old alignment/budget probe command forms."""
    return PROBE_TEXT_RE.fullmatch(str(value or "").strip()) is not None


def _is_before_probe_cutoff(value: str) -> bool:
    parsed = _parse_time(value)
    cutoff = _parse_time(HISTORICAL_PROBE_CUTOFF)
    return bool(parsed and cutoff and parsed < cutoff)


def _known_test_turn(row: sqlite3.Row) -> bool:
    session_id = str(row["session_id"] or "")
    if session_id in SYNTHETIC_SESSION_IDS:
        return True
    return (
        session_id == "main"
        and _is_before_probe_cutoff(str(row["created_at"] or ""))
        and is_historical_probe_text(str(row["user_text"] or ""))
    )


def _validate_gateway_pair_ownership(
    rows: list[sqlite3.Row],
    turns: list[TestTurn],
) -> None:
    """Shared Gateway tables are keyed only by session/round, so reject collisions."""
    target_ids = {turn.row_id for turn in turns}
    target_pairs = _turn_keys(turns)
    collisions = [
        int(row["id"])
        for row in rows
        if (str(row["session_id"] or ""), int(row["round_id"])) in target_pairs
        and int(row["id"]) not in target_ids
    ]
    if collisions:
        raise RuntimeError(
            "Gateway session/round collision with non-test conversation rows: "
            f"{collisions[:10]}"
        )


def discover_gateway_turns(gateway_db: Path) -> list[TestTurn]:
    turns: list[TestTurn] = []
    with _connect(gateway_db, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT id, profile_id, session_id, round_id, created_at,
                   user_text, assistant_text
            FROM conversation_turns
            ORDER BY id
            """
        ).fetchall()
    for row in rows:
        if not _known_test_turn(row):
            continue
        turns.append(
            TestTurn(
                row_id=int(row["id"]),
                profile_id=str(row["profile_id"] or ""),
                session_id=str(row["session_id"] or ""),
                round_id=int(row["round_id"]),
                created_at=str(row["created_at"] or ""),
                user_text=str(row["user_text"] or ""),
                assistant_text=str(row["assistant_text"] or ""),
            )
        )
    _validate_gateway_pair_ownership(rows, turns)
    return turns


def _turn_keys(turns: Iterable[TestTurn]) -> set[tuple[str, int]]:
    return {(turn.session_id, turn.round_id) for turn in turns}


def discover_raw_events(raw_db: Path, turns: list[TestTurn]) -> list[int]:
    profile_keys = {
        (turn.profile_id, turn.session_id, turn.round_id)
        for turn in turns
    }
    target_ids: list[int] = []
    with _connect(raw_db, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT id, source, source_event_id, session_id, metadata_json
            FROM raw_events
            ORDER BY id
            """
        ).fetchall()
    for row in rows:
        session_id = str(row["session_id"] or "")
        if session_id in SYNTHETIC_SESSION_IDS:
            target_ids.append(int(row["id"]))
            continue
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        round_value = metadata.get("round_id")
        try:
            round_id = int(str(round_value))
        except (TypeError, ValueError):
            round_id = -1
        profile_id = str(metadata.get("profile_id") or "")
        if (profile_id, session_id, round_id) in profile_keys:
            target_ids.append(int(row["id"]))
    return target_ids


def _exchange_hash(turn: TestTurn) -> str:
    text = "\n".join(
        [
            turn.profile_id,
            turn.session_id,
            turn.user_text.strip(),
            turn.assistant_text,
        ]
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover_persona_targets(
    persona_db: Path,
    turns: list[TestTurn],
) -> tuple[list[int], list[int], list[int], list[tuple[str, str, str, str]]]:
    turn_hashes = {_exchange_hash(turn) for turn in turns}
    event_ids: list[int] = []
    exchange_hashes: set[str] = set()
    session_state_rowids: list[int] = []
    transient_resets: list[tuple[str, str, str, str]] = []

    with _connect(persona_db, read_only=True) as connection:
        events = connection.execute(
            """
            SELECT * FROM persona_events
            ORDER BY created_at, id
            """
        ).fetchall()
        for row in events:
            session_id = str(row["session_id"] or "")
            is_target = session_id in SYNTHETIC_SESSION_IDS or (
                session_id == "main"
                and _is_before_probe_cutoff(str(row["created_at"] or ""))
                and is_historical_probe_text(str(row["user_excerpt"] or ""))
            )
            if not is_target:
                continue
            event_ids.append(int(row["id"]))
            if row["exchange_hash"]:
                exchange_hashes.add(str(row["exchange_hash"]))

        exchange_ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id, session_id, exchange_hash FROM persona_exchange_log"
            ).fetchall()
            if str(row["session_id"] or "") in SYNTHETIC_SESSION_IDS
            or str(row["exchange_hash"] or "") in exchange_hashes
            or str(row["exchange_hash"] or "") in turn_hashes
        ]

        session_rows = connection.execute(
            "SELECT rowid, * FROM persona_session_state"
        ).fetchall()
        session_state_rowids = [
            int(row["rowid"])
            for row in session_rows
            if str(row["session_id"] or "") in SYNTHETIC_SESSION_IDS
        ]

        for main_state in (
            row
            for row in session_rows
            if str(row["session_id"] or "") == "main"
        ):
            profile_id = str(main_state["profile_id"] or "")
            latest_main = connection.execute(
                """
                SELECT * FROM persona_events
                WHERE profile_id = ? AND session_id = 'main'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (profile_id,),
            ).fetchone()
            if not latest_main or int(latest_main["id"]) not in event_ids:
                continue
            residue = str(main_state["residue"] or "")
            inner_thought = str(main_state["inner_thought"] or "")
            if (
                residue == str(latest_main["residue"] or "")
                and inner_thought == str(latest_main["inner_thought"] or "")
            ):
                transient_resets.append(
                    (profile_id, "main", residue, inner_thought)
                )

    return event_ids, exchange_ids, session_state_rowids, transient_resets


def _contains_portrait_marker(value: Any) -> bool:
    text = str(value or "").casefold()
    return any(marker.casefold() in text for marker in PORTRAIT_MARKERS)


def _portrait_marker_values(value: Any) -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for child in value.values():
            hits.extend(_portrait_marker_values(child))
    elif isinstance(value, list):
        for child in value:
            hits.extend(_portrait_marker_values(child))
    elif _contains_portrait_marker(value):
        hits.append(str(value))
    return hits


def _historical_portrait_row(row: dict[str, Any]) -> bool:
    for key in (
        "timestamp",
        "created_at",
        "updated_at",
        "source_date",
        "last_seen_date",
        "first_seen_date",
    ):
        value = str(row.get(key) or "").strip()
        if value and _is_before_probe_cutoff(value):
            return True
    return False


def scrub_portrait_state(state: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Remove exact derived probe rows while preserving mixed real handoff text."""
    cleaned = json.loads(json.dumps(state, ensure_ascii=False))
    removed = 0

    portrait = cleaned.get("portrait")
    if isinstance(portrait, dict):
        for scope_state in portrait.values():
            if not isinstance(scope_state, dict):
                continue
            for layer in ("recent_buffer", "staging_pool"):
                rows = scope_state.get(layer)
                if not isinstance(rows, list):
                    continue
                kept = [
                    row
                    for row in rows
                    if not (
                        isinstance(row, dict)
                        and _contains_portrait_marker(row.get("text"))
                        and _historical_portrait_row(row)
                    )
                ]
                removed += len(rows) - len(kept)
                scope_state[layer] = kept

    summaries = cleaned.get("daily_summaries")
    if isinstance(summaries, dict):
        for date_key in list(summaries):
            if (
                date_key in KNOWN_TEST_DAILY_SUMMARY_DATES
                and _contains_portrait_marker(summaries[date_key])
            ):
                del summaries[date_key]
                removed += 1

    handoff = cleaned.get("handoff_recent_summaries")
    if isinstance(handoff, dict):
        for date_key in list(handoff):
            if date_key not in KNOWN_TEST_HANDOFF_DATES:
                continue
            original = str(handoff[date_key] or "")
            prefix, separator, weather = original.partition("。关系天气：")
            segments = [segment for segment in prefix.split("；") if segment]
            kept = [segment for segment in segments if not _contains_portrait_marker(segment)]
            removed += len(segments) - len(kept)
            rebuilt = "；".join(kept)
            if separator and weather:
                rebuilt = f"{rebuilt}{separator}{weather}" if rebuilt else f"关系天气：{weather}"
            if rebuilt:
                handoff[date_key] = rebuilt
            else:
                del handoff[date_key]

    for area in (
        "recent_activities",
        "recent_timeline",
        "stable_candidates",
        "profile_fact_candidates",
        "skipped",
    ):
        rows = cleaned.get(area)
        if not isinstance(rows, list):
            continue
        kept = []
        for row in rows:
            text = ""
            if isinstance(row, dict):
                text = row.get("text") or row.get("summary") or row.get("reason") or ""
            if (
                isinstance(row, dict)
                and _contains_portrait_marker(text)
                and _historical_portrait_row(row)
            ):
                removed += 1
            else:
                kept.append(row)
        cleaned[area] = kept

    leftovers = _portrait_marker_values(cleaned)
    if leftovers:
        preview = "; ".join(value[:120] for value in leftovers[:3])
        raise RuntimeError(f"unhandled portrait marker remains: {preview}")
    if removed:
        cleaned["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return cleaned, removed


def discover_reminders(reminder_db: Path) -> list[str]:
    found: list[str] = []
    with _connect(reminder_db, read_only=True) as connection:
        for reminder_id, marker in KNOWN_TEST_REMINDERS.items():
            row = connection.execute(
                "SELECT id, title, content FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
            if not row:
                continue
            text = f"{row['title']}\n{row['content']}"
            if marker not in text:
                raise RuntimeError(f"reminder {reminder_id} no longer matches its test marker")
            found.append(reminder_id)
    return found


def discover_legacy_test_buckets(buckets_dir: Path) -> list[str]:
    found: list[str] = []
    files_by_id: dict[str, Path] = {}
    for path in buckets_dir.rglob("*.md"):
        try:
            post = frontmatter.load(path)
        except Exception:
            continue
        bucket_id = str(post.get("id") or "")
        if bucket_id in LEGACY_TEST_BUCKETS:
            files_by_id[bucket_id] = path
    for bucket_id, marker in LEGACY_TEST_BUCKETS.items():
        path = files_by_id.get(bucket_id)
        if not path:
            continue
        post = frontmatter.load(path)
        text = f"{dict(post.metadata)}\n{post.content}"
        if marker not in text and "CODEX_SMOKE_20260718020107_4fg3hi" not in text:
            raise RuntimeError(f"bucket {bucket_id} no longer matches its test marker")
        found.append(bucket_id)
    return found


def build_plan(buckets_dir: Path, state_dir: Path) -> CleanupPlan:
    gateway_db = buckets_dir / "gateway_state.db"
    raw_db = state_dir / "raw_events.sqlite"
    persona_db = state_dir / "persona_state.db"
    portrait_path = state_dir / "portrait_state.json"
    reminder_db = state_dir / "reminders.sqlite"

    turns = discover_gateway_turns(gateway_db)
    raw_ids = discover_raw_events(raw_db, turns)
    event_ids, exchange_ids, state_rowids, transient_resets = discover_persona_targets(
        persona_db,
        turns,
    )
    portrait_markers = 0
    if portrait_path.exists():
        portrait = json.loads(portrait_path.read_text(encoding="utf-8"))
        _, portrait_markers = scrub_portrait_state(portrait)

    return CleanupPlan(
        turns=turns,
        raw_event_ids=raw_ids,
        persona_event_ids=event_ids,
        persona_exchange_log_ids=exchange_ids,
        persona_session_state_rowids=state_rowids,
        persona_transient_resets=transient_resets,
        portrait_marker_count=portrait_markers,
        reminder_ids=discover_reminders(reminder_db),
        legacy_bucket_ids=discover_legacy_test_buckets(buckets_dir),
    )


def _delete_ids(connection: sqlite3.Connection, table: str, ids: Iterable[Any]) -> int:
    values = list(ids)
    if not values:
        return 0
    before = connection.total_changes
    connection.executemany(f"DELETE FROM {table} WHERE id = ?", [(value,) for value in values])
    return connection.total_changes - before


def _delete_rowids(
    connection: sqlite3.Connection,
    table: str,
    rowids: Iterable[int],
) -> int:
    values = list(rowids)
    if not values:
        return 0
    before = connection.total_changes
    connection.executemany(
        f"DELETE FROM {table} WHERE rowid = ?",
        [(value,) for value in values],
    )
    return connection.total_changes - before


def apply_gateway_cleanup(gateway_db: Path, turns: list[TestTurn]) -> dict[str, int]:
    pairs = sorted(_turn_keys(turns))
    synthetic = sorted(SYNTHETIC_SESSION_IDS)
    counts: dict[str, int] = {}
    with _connect(gateway_db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for table in (
            "injected_buckets",
            "injection_debug",
            "recent_context_injections",
            "upstream_usage",
        ):
            before = connection.total_changes
            connection.executemany(
                f"DELETE FROM {table} WHERE session_id = ? AND round_id = ?",
                pairs,
            )
            counts[table] = connection.total_changes - before

        counts["conversation_turns"] = _delete_ids(
            connection,
            "conversation_turns",
            [turn.row_id for turn in turns],
        )

        # Whole synthetic sessions may have request rounds without a stored
        # conversation turn.  Mixed main-session rounds remain as fences.
        for table in (
            "request_rounds",
            "injected_buckets",
            "injection_debug",
            "recent_context_injections",
            "conversation_turns",
            "upstream_usage",
        ):
            before = connection.total_changes
            connection.executemany(
                f"DELETE FROM {table} WHERE session_id = ?",
                [(session_id,) for session_id in synthetic],
            )
            counts[table] = counts.get(table, 0) + connection.total_changes - before
    return counts


def apply_raw_cleanup(raw_db: Path, ids: list[int]) -> dict[str, int]:
    with _connect(raw_db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        deleted = _delete_ids(connection, "raw_events", ids)
        connection.execute("INSERT INTO raw_events_fts(raw_events_fts) VALUES('rebuild')")
        base_count = int(connection.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0])
        fts_count = int(connection.execute("SELECT COUNT(*) FROM raw_events_fts").fetchone()[0])
        if base_count != fts_count:
            raise RuntimeError(
                f"raw FTS rebuild mismatch: raw_events={base_count}, raw_events_fts={fts_count}"
            )
    return {"raw_events": deleted, "raw_events_fts": fts_count}


def _global_persona_rows(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in connection.execute("SELECT * FROM persona_global_state ORDER BY profile_id")]


def apply_persona_cleanup(persona_db: Path, plan: CleanupPlan) -> dict[str, int]:
    with _connect(persona_db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        global_before = _global_persona_rows(connection)
        events = _delete_ids(connection, "persona_events", plan.persona_event_ids)
        exchanges = _delete_ids(
            connection,
            "persona_exchange_log",
            plan.persona_exchange_log_ids,
        )
        states = _delete_rowids(
            connection,
            "persona_session_state",
            plan.persona_session_state_rowids,
        )
        reset = 0
        for profile_id, session_id, residue, inner_thought in plan.persona_transient_resets:
            cursor = connection.execute(
                """
                UPDATE persona_session_state
                SET mood_label = 'warm_neutral', residue = '', inner_thought = ''
                WHERE profile_id = ? AND session_id = ?
                  AND residue = ? AND inner_thought = ?
                """,
                (profile_id, session_id, residue, inner_thought),
            )
            reset += max(0, int(cursor.rowcount))
        if global_before != _global_persona_rows(connection):
            raise RuntimeError("persona_global_state changed during test cleanup")
    return {
        "persona_events": events,
        "persona_exchange_log": exchanges,
        "persona_session_state": states,
        "main_transient_reset": reset,
    }


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_portrait_cleanup(path: Path) -> int:
    if not path.exists():
        return 0
    state = json.loads(path.read_text(encoding="utf-8"))
    cleaned, removed = scrub_portrait_state(state)
    if removed:
        _atomic_write_json(path, cleaned)
    return removed


def apply_reminder_cleanup(path: Path, reminder_ids: list[str]) -> int:
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        return _delete_ids(connection, "reminders", reminder_ids)


def _atomic_write_text(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _legacy_bucket_paths(buckets_dir: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in buckets_dir.rglob("*.md"):
        try:
            post = frontmatter.load(path)
        except Exception:
            continue
        bucket_id = str(post.get("id") or "")
        if bucket_id in LEGACY_TEST_BUCKETS:
            result[bucket_id] = path
    return result


async def apply_bucket_cleanup(
    buckets_dir: Path,
    state_dir: Path,
    config_path: Path,
    bucket_ids: list[str],
) -> dict[str, Any]:
    from bucket_manager import BucketManager
    from embedding_engine import EmbeddingEngine
    from entity_edges import EntityEdgeStore
    from identity_semantics import IdentitySemanticStore
    from memory_edges import MemoryEdgeStore
    from memory_moments import MemoryMomentStore
    from memory_nodes import MemoryNodeStore
    from self_anchor import is_self_anchor_bucket
    from utils import load_config
    from web.current_contract import CurrentWebDependencies, cleanup_bucket_indexes
    from word_map import WordMapStore

    config = load_config(str(config_path))
    config["buckets_dir"] = str(buckets_dir)
    config["state_dir"] = str(state_dir)
    config["reminder_db_path"] = str(state_dir / "reminders.sqlite")
    config.setdefault("embedding", {})["db_path"] = str(buckets_dir / "embeddings.db")

    embedding = EmbeddingEngine(config)
    manager = BucketManager(config, embedding_engine=embedding)
    memory_moments = MemoryMomentStore(config)
    memory_edges = MemoryEdgeStore(config)
    entity_edges = EntityEdgeStore(config)
    memory_nodes = MemoryNodeStore(config)
    identity_semantics = IdentitySemanticStore(config)
    word_map = WordMapStore(config)
    dependencies = CurrentWebDependencies(
        config=config,
        memory_moment_store=memory_moments,
        memory_edge_store=memory_edges,
        entity_edge_store=entity_edges,
        memory_node_store=memory_nodes,
    )
    paths = _legacy_bucket_paths(buckets_dir)
    deleted: list[str] = []
    try:
        for bucket_id in bucket_ids:
            path = paths.get(bucket_id)
            if not path:
                continue
            post = frontmatter.load(path)
            post["provenance"] = {
                "kind": "test",
                "created_by": str(post.get("source_tool") or "legacy_smoke"),
                "erasable": True,
            }
            _atomic_write_text(path, frontmatter.dumps(post))
            result = await manager.hard_delete_test_bucket(
                bucket_id,
                reason="Remove verified historical Codex smoke-test data",
            )
            if not result.get("ok"):
                raise RuntimeError(f"hard delete failed for {bucket_id}: {result}")
            await cleanup_bucket_indexes(dependencies, bucket_id)
            tombstone = buckets_dir / ".tombstones" / f"{bucket_id}.json"
            if tombstone.exists():
                payload = json.loads(tombstone.read_text(encoding="utf-8"))
                if str(payload.get("id") or "") != bucket_id:
                    raise RuntimeError(f"tombstone id mismatch for {bucket_id}")
                tombstone.unlink()
            deleted.append(bucket_id)

        remaining_buckets = await manager.list_all(include_archive=True)
        identity_report = identity_semantics.rebuild_alias_index(remaining_buckets)
        word_map_report = word_map.rebuild(
            [
                bucket
                for bucket in remaining_buckets
                if not is_self_anchor_bucket(bucket)
            ]
        )
        report = manager.ledger_integrity_report(rebuild_projections=True)
    finally:
        await embedding.aclose()
    return {
        "deleted": deleted,
        "ledger_ok": bool(report.get("ok", True)),
        "projection": report.get("sqlite_projection", {}),
        "identity_semantics": identity_report,
        "word_map": word_map_report,
    }


def verify_clean(buckets_dir: Path, state_dir: Path) -> dict[str, Any]:
    plan = build_plan(buckets_dir, state_dir)
    report = plan.report()
    databases = [
        buckets_dir / "gateway_state.db",
        buckets_dir / "embeddings.db",
        buckets_dir / "_ledger" / "projections" / "trace_catalog.sqlite3",
        state_dir / "persona_state.db",
        state_dir / "raw_events.sqlite",
        state_dir / "reminders.sqlite",
    ]
    report["quick_check"] = {
        str(path): _quick_check(path)
        for path in databases
        if path.exists()
    }
    return report


async def run(args: argparse.Namespace) -> int:
    buckets_dir = Path(args.buckets_dir).resolve()
    state_dir = Path(args.state_dir).resolve()
    config_path = Path(args.config).resolve()
    plan = build_plan(buckets_dir, state_dir)
    report: dict[str, Any] = {"mode": "apply" if args.apply else "dry-run", "plan": plan.report()}

    if args.expect_main_turns is not None:
        actual = sum(1 for turn in plan.turns if turn.session_id == "main")
        if actual != args.expect_main_turns:
            raise RuntimeError(
                f"expected {args.expect_main_turns} main probe turns, found {actual}"
            )

    if not args.apply:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.confirm != "CLEAN KNOWN TEST POLLUTION":
        raise RuntimeError("--confirm must equal CLEAN KNOWN TEST POLLUTION")

    report["gateway"] = apply_gateway_cleanup(
        buckets_dir / "gateway_state.db",
        plan.turns,
    )
    report["raw"] = apply_raw_cleanup(
        state_dir / "raw_events.sqlite",
        plan.raw_event_ids,
    )
    report["persona"] = apply_persona_cleanup(
        state_dir / "persona_state.db",
        plan,
    )
    report["portrait"] = {
        "removed": apply_portrait_cleanup(state_dir / "portrait_state.json")
    }
    report["reminders"] = {
        "deleted": apply_reminder_cleanup(
            state_dir / "reminders.sqlite",
            plan.reminder_ids,
        )
    }
    report["buckets"] = await apply_bucket_cleanup(
        buckets_dir,
        state_dir,
        config_path,
        plan.legacy_bucket_ids,
    )
    report["verification"] = verify_clean(buckets_dir, state_dir)
    remaining = report["verification"]
    if any(
        int(remaining.get(key, 0) or 0)
        for key in (
            "gateway_turns",
            "raw_events",
            "persona_events",
            "persona_exchange_logs",
            "persona_session_states",
            "persona_transient_resets",
            "portrait_markers",
            "reminders",
        )
    ) or remaining.get("legacy_bucket_ids"):
        raise RuntimeError(f"cleanup verification still finds targets: {remaining}")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--buckets-dir", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--expect-main-turns", type=int)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
