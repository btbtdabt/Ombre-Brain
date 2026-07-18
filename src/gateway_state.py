import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from runtime_values import optional_int as _optional_int, parse_comparable_datetime
from sqlite_support import connect_rows


class GatewayStateStore:
    """Persist Gateway round, injection, conversation, and usage state."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return connect_rows(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS request_rounds (
                    session_id TEXT NOT NULL,
                    round_id INTEGER NOT NULL,
                    completed_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, round_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS injected_buckets (
                    session_id TEXT NOT NULL,
                    round_id INTEGER NOT NULL,
                    bucket_id TEXT NOT NULL,
                    injected_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, round_id, bucket_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_injected_lookup
                ON injected_buckets (session_id, bucket_id, injected_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS injection_debug (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    round_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_injection_debug_lookup
                ON injection_debug (session_id, id DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recent_context_injections (
                    session_id TEXT NOT NULL,
                    round_id INTEGER NOT NULL,
                    injected_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, round_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recent_context_lookup
                ON recent_context_injections (session_id, injected_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    round_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    user_text TEXT NOT NULL DEFAULT '',
                    assistant_text TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    client TEXT NOT NULL DEFAULT '',
                    route TEXT NOT NULL DEFAULT '',
                    UNIQUE(profile_id, session_id, round_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_turns_recent
                ON conversation_turns (profile_id, created_at DESC, id DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_turns_session
                ON conversation_turns (profile_id, session_id, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS upstream_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    round_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    route TEXT NOT NULL DEFAULT '',
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    prompt_cache_hit_tokens INTEGER,
                    prompt_cache_miss_tokens INTEGER,
                    cached_tokens INTEGER,
                    cache_read_input_tokens INTEGER,
                    cache_creation_input_tokens INTEGER,
                    usage_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_upstream_usage_lookup
                ON upstream_usage (session_id, id DESC)
                """
            )

    def record_success(
        self,
        session_id: str,
        bucket_ids: list[str],
        completed_at: datetime | None = None,
    ) -> int:
        completed_at = completed_at or datetime.now()
        completed_iso = completed_at.isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT COALESCE(MAX(round_id), 0) AS current_round
                FROM request_rounds
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            next_round = int(row["current_round"]) + 1
            connection.execute(
                """
                INSERT INTO request_rounds (session_id, round_id, completed_at)
                VALUES (?, ?, ?)
                """,
                (session_id, next_round, completed_iso),
            )
            connection.executemany(
                """
                INSERT OR REPLACE INTO injected_buckets
                (session_id, round_id, bucket_id, injected_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (session_id, next_round, bucket_id, completed_iso)
                    for bucket_id in bucket_ids
                ],
            )
        return next_round

    def get_current_round(self, session_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(round_id), 0) AS current_round
                FROM request_rounds
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return int(row["current_round"]) if row else 0

    def get_last_success_at(self, session_id: str) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT completed_at
                FROM request_rounds
                WHERE session_id = ?
                ORDER BY round_id DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return None
        try:
            return datetime.fromisoformat(str(row["completed_at"]))
        except ValueError:
            return None

    def get_recent_bucket_ids(self, session_id: str, recent_rounds: int) -> set[str]:
        if recent_rounds <= 0:
            return set()
        current_round = self.get_current_round(session_id)
        if current_round <= 0:
            return set()
        min_round = max(1, current_round - recent_rounds + 1)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT bucket_id
                FROM injected_buckets
                WHERE session_id = ? AND round_id >= ?
                """,
                (session_id, min_round),
            ).fetchall()
        return {str(row["bucket_id"]) for row in rows}

    def get_last_injected_at(self, session_id: str, bucket_id: str) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT injected_at
                FROM injected_buckets
                WHERE session_id = ? AND bucket_id = ?
                ORDER BY injected_at DESC
                LIMIT 1
                """,
                (session_id, bucket_id),
            ).fetchone()
        if not row:
            return None
        try:
            return datetime.fromisoformat(str(row["injected_at"]))
        except ValueError:
            return None

    def record_recent_context_injection(
        self,
        session_id: str,
        round_id: int,
        injected_at: datetime | None = None,
    ) -> None:
        injected_at = injected_at or datetime.now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO recent_context_injections
                (session_id, round_id, injected_at)
                VALUES (?, ?, ?)
                """,
                (session_id, int(round_id), injected_at.isoformat(timespec="seconds")),
            )

    def get_last_recent_context_at(self, session_id: str) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT injected_at
                FROM recent_context_injections
                WHERE session_id = ?
                ORDER BY injected_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return None
        try:
            return datetime.fromisoformat(str(row["injected_at"]))
        except ValueError:
            return None

    def record_injection_debug(
        self,
        session_id: str,
        round_id: int,
        payload: dict[str, Any],
        *,
        max_entries: int = 80,
    ) -> int:
        created_at = datetime.now().isoformat(timespec="seconds")
        body = json.dumps(payload, ensure_ascii=False)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO injection_debug (session_id, round_id, created_at, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, int(round_id), created_at, body),
            )
            debug_id = int(cursor.lastrowid or 0)
            connection.execute(
                """
                DELETE FROM injection_debug
                WHERE id NOT IN (
                    SELECT id FROM injection_debug ORDER BY id DESC LIMIT ?
                )
                """,
                (max(1, int(max_entries)),),
            )
        return debug_id

    def record_conversation_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        round_id: int,
        user_text: str,
        assistant_text: str = "",
        model: str = "",
        client: str = "",
        route: str = "",
        created_at: datetime | None = None,
        max_entries: int = 500,
    ) -> int:
        created_at = created_at or datetime.now(timezone.utc)
        created_iso = created_at.isoformat(timespec="seconds")
        safe_profile_id = str(profile_id or "default").strip() or "default"
        safe_session_id = str(session_id or "default").strip() or "default"
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR REPLACE INTO conversation_turns
                (profile_id, session_id, round_id, created_at, user_text,
                 assistant_text, model, client, route)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    safe_profile_id,
                    safe_session_id,
                    int(round_id),
                    created_iso,
                    str(user_text or ""),
                    str(assistant_text or ""),
                    str(model or ""),
                    str(client or ""),
                    str(route or ""),
                ),
            )
            if max_entries > 0:
                connection.execute(
                    """
                    DELETE FROM conversation_turns
                    WHERE profile_id = ?
                      AND id NOT IN (
                        SELECT id FROM conversation_turns
                        WHERE profile_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                      )
                    """,
                    (safe_profile_id, safe_profile_id, max(1, int(max_entries))),
                )
            turn_id = int(cursor.lastrowid or 0)
        return turn_id

    def list_recent_conversation_turns(
        self,
        *,
        profile_id: str,
        session_id: str | None = None,
        limit: int = 10,
        hours: float = 6.0,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(50, int(limit or 10)))
        safe_profile_id = str(profile_id or "default").strip() or "default"
        safe_session_id = str(session_id or "").strip()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.0, float(hours or 0)))
        where_clause = "profile_id = ? AND created_at >= ?"
        params: list[Any] = [safe_profile_id, cutoff.isoformat(timespec="seconds")]
        if safe_session_id:
            where_clause += " AND session_id = ?"
            params.append(safe_session_id)
        params.append(safe_limit)
        # where_clause is assembled only from the fixed predicates above; all
        # profile, time, session, and limit values remain bound parameters.
        query = (
            "SELECT id, profile_id, session_id, round_id, created_at, "
            "user_text, assistant_text, model, client, route "
            "FROM conversation_turns "
            f"WHERE {where_clause} "  # nosec B608
            "ORDER BY id DESC LIMIT ?"
        )
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._conversation_row_payload(row) for row in rows]

    def list_conversation_turns_between(
        self,
        *,
        profile_id: str,
        start_at: datetime,
        end_at: datetime,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(80, int(limit or 12)))
        safe_profile_id = str(profile_id or "default").strip() or "default"
        compare_tz = start_at.tzinfo or end_at.tzinfo

        start = start_at
        end = end_at
        if compare_tz is not None:
            start = start.replace(tzinfo=compare_tz) if start.tzinfo is None else start.astimezone(compare_tz)
            end = end.replace(tzinfo=compare_tz) if end.tzinfo is None else end.astimezone(compare_tz)
        elif start.tzinfo is not None:
            start = start.replace(tzinfo=None)
        elif end.tzinfo is not None:
            end = end.replace(tzinfo=None)

        sql_start = (start.replace(tzinfo=None) - timedelta(days=1)).isoformat(timespec="seconds")
        sql_end = (end.replace(tzinfo=None) + timedelta(days=1)).isoformat(timespec="seconds")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, profile_id, session_id, round_id, created_at,
                       user_text, assistant_text, model, client, route
                FROM conversation_turns
                WHERE profile_id = ?
                  AND replace(substr(created_at, 1, 19), ' ', 'T') >= ?
                  AND replace(substr(created_at, 1, 19), ' ', 'T') < ?
                ORDER BY id DESC
                """,
                (safe_profile_id, sql_start, sql_end),
            ).fetchall()

        filtered = []
        for row in rows:
            created = parse_comparable_datetime(row["created_at"], compare_tz)
            if created is None or not (start <= created < end):
                continue
            filtered.append(row)
            if len(filtered) >= safe_limit:
                break
        return [self._conversation_row_payload(row) for row in filtered]

    @staticmethod
    def _conversation_row_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "profile_id": row["profile_id"],
            "session_id": row["session_id"],
            "round_id": row["round_id"],
            "created_at": row["created_at"],
            "user_text": row["user_text"] or "",
            "assistant_text": row["assistant_text"] or "",
            "model": row["model"] or "",
            "client": row["client"] or "",
            "route": row["route"] or "",
        }

    def list_injection_debug(
        self,
        *,
        session_id: str = "",
        limit: int = 20,
        include_context: bool = True,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(100, int(limit)))
        with self._connect() as connection:
            if session_id:
                rows = connection.execute(
                    """
                    SELECT id, session_id, round_id, created_at, payload_json
                    FROM injection_debug
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (session_id, safe_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, session_id, round_id, created_at, payload_json
                    FROM injection_debug
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                payload = {"raw": row["payload_json"]}
            if isinstance(payload, dict) and not include_context:
                payload = dict(payload)
                payload.pop("stable_context", None)
                payload.pop("dynamic_context", None)
            items.append(
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "round_id": row["round_id"],
                    "created_at": row["created_at"],
                    "payload": payload,
                }
            )
        return items

    def record_upstream_usage(
        self,
        *,
        session_id: str,
        round_id: int,
        model: str,
        route: str,
        usage: dict[str, Any],
        max_entries: int = 200,
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        safe_usage = dict(usage or {})
        prompt_tokens = safe_usage.get("prompt_tokens") or safe_usage.get("input_tokens")
        completion_tokens = safe_usage.get("completion_tokens") or safe_usage.get("output_tokens")
        prompt_details = safe_usage.get("prompt_tokens_details")
        cached_tokens = None
        if isinstance(prompt_details, dict):
            cached_tokens = prompt_details.get("cached_tokens")

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO upstream_usage (
                    session_id, round_id, created_at, model, route,
                    prompt_tokens, completion_tokens,
                    prompt_cache_hit_tokens, prompt_cache_miss_tokens,
                    cached_tokens, cache_read_input_tokens,
                    cache_creation_input_tokens, usage_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(session_id or "default"),
                    int(round_id),
                    created_at,
                    str(model or ""),
                    str(route or ""),
                    _optional_int(prompt_tokens),
                    _optional_int(completion_tokens),
                    _optional_int(safe_usage.get("prompt_cache_hit_tokens")),
                    _optional_int(safe_usage.get("prompt_cache_miss_tokens")),
                    _optional_int(cached_tokens),
                    _optional_int(safe_usage.get("cache_read_input_tokens")),
                    _optional_int(safe_usage.get("cache_creation_input_tokens")),
                    json.dumps(safe_usage, ensure_ascii=False),
                ),
            )
            usage_id = int(cursor.lastrowid or 0)
            if max_entries > 0:
                connection.execute(
                    """
                    DELETE FROM upstream_usage
                    WHERE id NOT IN (
                        SELECT id FROM upstream_usage ORDER BY id DESC LIMIT ?
                    )
                    """,
                    (max(1, int(max_entries)),),
                )
        return usage_id

    def list_upstream_usage(
        self,
        *,
        session_id: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(100, int(limit or 20)))
        with self._connect() as connection:
            if session_id:
                rows = connection.execute(
                    """
                    SELECT id, session_id, round_id, created_at, model, route,
                           prompt_tokens, completion_tokens,
                           prompt_cache_hit_tokens, prompt_cache_miss_tokens,
                           cached_tokens, cache_read_input_tokens,
                           cache_creation_input_tokens, usage_json
                    FROM upstream_usage
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (session_id, safe_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, session_id, round_id, created_at, model, route,
                           prompt_tokens, completion_tokens,
                           prompt_cache_hit_tokens, prompt_cache_miss_tokens,
                           cached_tokens, cache_read_input_tokens,
                           cache_creation_input_tokens, usage_json
                    FROM upstream_usage
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                usage = json.loads(row["usage_json"] or "{}")
            except json.JSONDecodeError:
                usage = {}
            items.append(
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "round_id": row["round_id"],
                    "created_at": row["created_at"],
                    "model": row["model"] or "",
                    "route": row["route"] or "",
                    "prompt_tokens": row["prompt_tokens"],
                    "completion_tokens": row["completion_tokens"],
                    "prompt_cache_hit_tokens": row["prompt_cache_hit_tokens"],
                    "prompt_cache_miss_tokens": row["prompt_cache_miss_tokens"],
                    "cached_tokens": row["cached_tokens"],
                    "cache_read_input_tokens": row["cache_read_input_tokens"],
                    "cache_creation_input_tokens": row["cache_creation_input_tokens"],
                    "usage": usage,
                }
            )
        return items

    def get_cooldown_multiplier(
        self,
        session_id: str,
        bucket_id: str,
        cooldown_hours: float,
        cooldown_floor: float,
        now: datetime | None = None,
    ) -> float:
        if cooldown_hours <= 0:
            return 1.0
        now = now or datetime.now()
        last_injected = self.get_last_injected_at(session_id, bucket_id)
        if not last_injected:
            return 1.0
        elapsed_hours = max(0.0, (now - last_injected).total_seconds() / 3600)
        if elapsed_hours >= cooldown_hours:
            return 1.0
        progress = elapsed_hours / cooldown_hours
        return round(cooldown_floor + (1.0 - cooldown_floor) * progress, 4)
