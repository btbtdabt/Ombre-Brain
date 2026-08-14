"""Independent snapshot of the public MCP tool contract used by tests."""

from __future__ import annotations


EXPECTED_PARAMETERS = {
    "reminder_create": (
        "title", "content", "next_due_at", "start_at", "end_at",
        "repeat_rule", "interval_rounds", "cooldown_minutes", "daily_limit",
        "max_injections", "channel", "session_id",
    ),
    "reminder_list": ("status", "limit"),
    "reminder_update": (
        "reminder_id", "status", "snooze_minutes", "next_due_at", "title",
        "content", "daily_limit", "max_injections",
    ),
    "breath": (
        "query", "max_tokens", "domain", "date", "valence", "arousal",
        "max_results", "importance_min", "tags", "catalog",
        "include_related", "related_per_memory", "edge_min_confidence",
        "include_core", "core_limit", "is_session_start", "debug", "surface",
        "direct_render_mode", "retrieval_mode", "mode", "session_id",
    ),
    "breath_search": ("query", "domain", "max_results"),
    "breath_advanced": (
        "query", "max_tokens", "domain", "date", "valence", "arousal",
        "max_results", "importance_min", "tags", "catalog",
        "include_related", "related_per_memory", "edge_min_confidence",
        "include_core", "core_limit", "is_session_start", "debug", "surface",
        "direct_render_mode", "retrieval_mode", "mode", "session_id",
    ),
    "read_bucket": ("bucket_id",),
    "list_buckets_light": ("include_archive", "limit", "offset"),
    "letter_write": (
        "author", "content", "user_name", "title", "date", "ai_name",
        "lock_type", "unlock_date",
    ),
    "letter_lock_update": ("letter_id", "lock_type", "unlock_date"),
    "letter_read": ("query", "limit", "author", "date_from", "date_to"),
    "comment_bucket": ("bucket_id", "content", "kind", "valence", "arousal"),
    "delete_bucket_comment": ("bucket_id", "comment_id"),
    "hold": (
        "content", "tags", "importance", "pinned", "feel", "whisper",
        "source_bucket", "valence", "arousal", "title", "date", "domain",
        "media", "why_remembered", "meaning", "test_data", "source_content",
        "source_ranges",
    ),
    "darkroom_enter": (
        "note", "mode", "mood", "tags", "source", "visibility", "lock_for",
        "new_room",
    ),
    "darkroom_rooms": ("limit", "visibility"),
    "darkroom_delete": ("room_id", "confirm"),
    "darkroom_view": ("entry_id",),
    "darkroom_status": (),
    "darkroom_release": ("entry_id", "reason"),
    "grow": ("content", "items", "auto", "source", "title"),
    "source_read": ("bucket_id", "expected_title", "scope", "cursor", "max_tokens"),
    "profile_fact": (
        "fact", "evidence_bucket_id", "profile_kind", "subject", "predicate",
        "object_value", "evidence_moment_id", "evidence_context", "reflection",
        "confidence",
    ),
    "trace": (
        "bucket_id", "name", "domain", "valence", "arousal", "importance",
        "tags", "resolved", "pinned", "protected", "anchor", "digested", "content", "date",
        "status", "weight", "dont_surface", "why_remembered", "meaning_append",
        "meaning_replace", "media_append", "media_replace", "delete",
        "hard_delete", "delete_reason", "restore", "old_str", "new_str",
        "deletion_request_id", "deletion_decision", "deletion_ai_reason",
    ),
    "pulse": ("include_archive",),
    "introspection": ("limit", "offset", "created_date", "created_from", "created_to"),
    "entity_edge_backfill": ("limit", "bucket_id", "query", "dry_run", "include_archive"),
    "dream": ("window_hours",),
    "anchor": ("bucket_id",),
    "release": ("bucket_id",),
    "plan": ("content", "status", "related_bucket", "weight", "why_remembered"),
    "I": ("content", "aspect", "read", "limit", "promote"),
}


EXPECTED_REQUIRED_PARAMETERS = {
    "reminder_create": ("title", "content"),
    "reminder_update": ("reminder_id",),
    "breath_search": ("query",),
    "read_bucket": ("bucket_id",),
    "letter_write": ("author", "content"),
    "letter_lock_update": ("letter_id", "lock_type"),
    "comment_bucket": ("bucket_id", "content"),
    "delete_bucket_comment": ("bucket_id", "comment_id"),
    "hold": ("content",),
    "darkroom_enter": ("note",),
    "darkroom_delete": ("room_id",),
    "source_read": ("bucket_id", "expected_title"),
    "profile_fact": ("fact", "evidence_bucket_id"),
    "trace": ("bucket_id",),
    "anchor": ("bucket_id",),
    "release": ("bucket_id",),
    "plan": ("content",),
}
