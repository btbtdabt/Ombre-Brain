"""Compact new-session context for the current-production breath contract."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from persona_event_selection import select_persona_events
from recall_pipeline import is_unpinned_anchor_candidate, trim_to_token_budget
from self_anchor import SELF_ANCHOR_TAG, is_self_anchor_bucket
from utils import LOCAL_TZ, bucket_content_for_recall, count_tokens_approx, strip_wikilinks

from .. import _runtime as rt
from ._helpers import (
    clip_text as _clip,
    dict_items,
    identity,
    log_warning as _log_warning,
    mapping_or_empty,
    require_runtime,
    runtime_config,
)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(LOCAL_TZ)


def _bucket_date(bucket: dict) -> str:
    meta = bucket.get("metadata", {}) if isinstance(bucket.get("metadata"), dict) else {}
    explicit = str(meta.get("date") or "").strip()
    if explicit:
        return explicit[:10]
    for key in ("updated_at", "last_active", "created"):
        parsed = _parse_datetime(meta.get(key))
        if parsed:
            return parsed.date().isoformat()
    return ""


def _clean_summary(value: Any) -> str:
    text = strip_wikilinks(str(value or ""))
    text = re.sub(r"(?is)^---\s*.*?\s*---\s*", "", text)
    text = re.sub(r"(?im)^#{1,6}\s+(?:followup|todo|后续待办|待办事项).*?(?=^#{1,6}\s|\Z)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _self_anchor(all_buckets: list[dict]) -> str:
    candidates = []
    for bucket in all_buckets:
        if not is_self_anchor_bucket(bucket):
            continue
        meta = bucket.get("metadata", {}) if isinstance(bucket.get("metadata"), dict) else {}
        if meta.get("active") is False or meta.get("deprecated") or meta.get("resolved"):
            continue
        candidates.append(bucket)
    candidates.sort(
        key=lambda bucket: (
            int((bucket.get("metadata") or {}).get("importance", 5)),
            str((bucket.get("metadata") or {}).get("updated_at") or ""),
        ),
        reverse=True,
    )
    self_anchor_cfg = runtime_config().get("self_anchor", {})
    if not isinstance(self_anchor_cfg, dict):
        self_anchor_cfg = {}
    entry_id = str(self_anchor_cfg.get("entry_bucket_id") or "").strip()
    if entry_id:
        candidates.sort(key=lambda bucket: str(bucket.get("id") or "") != entry_id)
    if not candidates:
        return ""
    return _clip(_clean_summary(candidates[0].get("content")), 260)


def _select_anchors(all_buckets: list[dict], limit: int = 2) -> list[dict]:
    anchors = []
    for bucket in all_buckets:
        if not is_unpinned_anchor_candidate(bucket):
            continue
        anchors.append(bucket)
    anchors.sort(
        key=lambda bucket: (
            int((bucket.get("metadata") or {}).get("importance", 5)),
            str((bucket.get("metadata") or {}).get("updated_at") or ""),
        ),
        reverse=True,
    )
    return anchors[: max(0, limit)]


def _format_anchors(all_buckets: list[dict]) -> str:
    rows = []
    for bucket in _select_anchors(all_buckets):
        meta = bucket.get("metadata", {}) if isinstance(bucket.get("metadata"), dict) else {}
        title = str(meta.get("name") or bucket.get("id") or "").strip()
        text = _clip(_clean_summary(bucket.get("content")), 72)
        rows.append(f"- [bucket_id:{bucket.get('id', '')}] {title}: {text}")
    return "\n".join(rows)


def _event_date(event: dict) -> str:
    parsed = _parse_datetime(event.get("created_at"))
    return parsed.date().isoformat() if parsed else ""


def _persona_trace(date_key: str, limit: int = 2) -> str:
    engine = getattr(rt, "persona_engine", None)
    list_events = getattr(engine, "_list_events", None)
    if not date_key or not callable(list_events):
        return ""
    try:
        events = [
            event
            for event in dict_items(list_events(max(80, limit * 8)))
            if _event_date(event) == date_key
        ]
    except Exception as exc:
        _log_warning("Handoff persona trace lookup failed: %s", exc)
        return ""
    names = identity()
    user_name = names.get("user_display_name") or names.get("user_name") or "用户"
    ai_name = names.get("ai_name") or "AI"
    phrases = []
    for event in select_persona_events(events, limit=limit):
        user_excerpt = _clip(event.get("user_excerpt"), 72)
        assistant_excerpt = _clip(event.get("assistant_excerpt"), 72)
        parts = []
        if user_excerpt:
            parts.append(f"{user_name}说“{user_excerpt}”")
        if assistant_excerpt:
            parts.append(f"{ai_name}回“{assistant_excerpt}”")
        if parts:
            phrases.append("；".join(parts))
    return _clip("；".join(phrases), 180)


def _recent_date_keys(days: int = 4) -> set[str]:
    today = datetime.now(LOCAL_TZ).date()
    return {(today - timedelta(days=offset)).isoformat() for offset in range(days)}


def _personal_recent_continuity(all_buckets: list[dict], limit: int = 3) -> str:
    rows = []
    recent_dates = _recent_date_keys()
    for bucket in all_buckets:
        if is_self_anchor_bucket(bucket):
            continue
        meta = bucket.get("metadata", {}) if isinstance(bucket.get("metadata"), dict) else {}
        tags = {str(tag).lower() for tag in meta.get("tags", []) or []}
        if not ({"relationship_weather", "daily_impression"} & tags):
            continue
        date_key = _bucket_date(bucket)
        if date_key and date_key not in recent_dates:
            continue
        text = _clean_summary(bucket.get("content"))
        if text:
            rows.append((date_key, str(meta.get("updated_at") or meta.get("created") or ""), text))
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    output = []
    for date_key, _updated, text in rows[: max(0, limit)]:
        trace = _persona_trace(date_key)
        weather = _clip(re.sub(r"^今天(?:的)?关系天气[：:]\s*", "", text), 120 if trace else 180)
        if trace and weather:
            output.append(f"- {date_key or 'recent'}: {trace}。关系天气：{weather}")
        elif trace:
            output.append(f"- {date_key or 'recent'}: {trace}")
        elif weather:
            output.append(f"- {date_key or 'recent'}: 关系天气：{weather}")
    return "\n".join(output)


def _ordinary_recent_continuity(all_buckets: list[dict], limit: int = 3) -> str:
    cutoff = datetime.now(LOCAL_TZ) - timedelta(days=4)
    candidates = []
    for bucket in all_buckets:
        if is_self_anchor_bucket(bucket):
            continue
        meta = bucket.get("metadata", {}) if isinstance(bucket.get("metadata"), dict) else {}
        if meta.get("pinned") or meta.get("protected") or meta.get("anchor"):
            continue
        if meta.get("active") is False or meta.get("deprecated"):
            continue
        updated = _parse_datetime(
            meta.get("updated_at") or meta.get("last_active") or meta.get("created")
        )
        if updated is None or updated < cutoff:
            continue
        if meta.get("type") == "feel":
            tags = {str(tag).lower() for tag in meta.get("tags", []) or []}
            if not ({"relationship_weather", "daily_impression"} & tags):
                continue
        candidates.append((updated, bucket))
    candidates.sort(key=lambda item: item[0], reverse=True)
    rows = []
    for _updated, bucket in candidates[: max(0, limit)]:
        meta = bucket.get("metadata", {}) if isinstance(bucket.get("metadata"), dict) else {}
        text = _clip(bucket_content_for_recall(bucket), 160)
        if text:
            rows.append(
                f"- [{_bucket_date(bucket)}] [bucket_id:{bucket.get('id', '')}] "
                f"{meta.get('name') or bucket.get('id')}: {text}"
            )
    return "\n".join(rows)


def _merge_lines(*blocks: str, max_lines: int = 5) -> str:
    output = []
    seen = set()
    for block in blocks:
        for raw_line in str(block or "").splitlines():
            line = raw_line.strip()
            if not line or line in seen:
                continue
            seen.add(line)
            output.append(line)
            if len(output) >= max_lines:
                return "\n".join(output)
    return "\n".join(output)


def _line_key(value: str) -> str:
    text = re.sub(r"^-\s+\d{4}-\d{2}-\d{2}[^:]*:\s*", "", str(value or "").strip())
    return re.sub(r"[\s，。；：、！？,.!?;:]+", "", text).lower()


def _remove_focus_overlap(recent: str, current_focus: str) -> str:
    focus_keys = [
        _line_key(line)
        for line in str(current_focus or "").splitlines()
        if len(_line_key(line)) >= 8
    ]
    if not focus_keys:
        return recent
    kept = []
    for line in str(recent or "").splitlines():
        key = _line_key(line)
        if key and any(key == focus or key in focus or focus in key for focus in focus_keys):
            continue
        kept.append(line)
    return "\n".join(kept)


def _care_memos(session_id: str, limit: int = 3) -> str:
    store = getattr(rt, "reminder_store", None)
    due = getattr(store, "due", None)
    if not callable(due):
        return ""
    gateway = getattr(rt, "gateway_state_store", None)
    try:
        current_round = int(gateway.get_current_round(session_id)) if gateway is not None else -1
    except Exception:
        current_round = -1
    try:
        items = due(
            session_id=str(session_id or "").strip(),
            channels=["gateway", "bridge"],
            round_id=current_round + 1,
            now=datetime.now(LOCAL_TZ),
            limit=max(0, limit),
        )
    except Exception as exc:
        _log_warning("Handoff care memo lookup failed: %s", exc)
        return ""
    rows = []
    for item in dict_items(items):
        content = _clip(item.get("content"), 90)
        if not content:
            continue
        date_hint = str(
            item.get("next_due_at") or item.get("start_at") or item.get("created_at") or ""
        )[:10]
        rows.append(
            f"- {date_hint or '未定日期'} {_clip(item.get('title') or '照顾备忘', 40)}: {content}"
        )
        if len(rows) >= limit:
            break
    return "\n".join(rows)


_trim_to_tokens = trim_to_token_budget


def _format_sections(
    intro: str,
    sections: list[tuple[str, str, int]],
    max_tokens: int,
) -> str:
    budget = max(0, int(max_tokens or 0))
    if budget <= 0:
        return ""
    active = [(title, content, cap) for title, content, cap in sections if str(content).strip()]
    header_cost = count_tokens_approx(intro) + sum(
        count_tokens_approx(f"\n\n=== {title} ===\n") for title, _content, _cap in active
    )
    remaining = max(0, budget - header_cost)
    desired = [min(cap, count_tokens_approx(content)) for _title, content, cap in active]
    scale = min(1.0, remaining / sum(desired)) if desired and sum(desired) else 0.0
    result = intro
    for (title, content, _cap), wanted in zip(active, desired):
        rendered = _trim_to_tokens(content, int(wanted * scale))
        if rendered:
            result += f"\n\n=== {title} ===\n{rendered}"
    return result


async def build_handoff_breath(
    *,
    max_tokens: int = 1200,
    session_id: str = "",
    debug: bool = False,
) -> str:
    """Build current-main's compact identity and continuity handoff block."""
    try:
        all_buckets = await require_runtime("bucket_mgr").list_all(include_archive=False)
    except Exception as exc:
        _log_warning("Handoff breath bucket list failed: %s", exc)
        all_buckets = []

    portrait = getattr(rt, "portrait_engine", None)
    build_sections = getattr(portrait, "build_handoff_sections", None)
    try:
        portrait_sections = (
            mapping_or_empty(build_sections(max_recent_items=3)) if callable(build_sections) else {}
        )
    except Exception as exc:
        _log_warning("Handoff portrait state failed: %s", exc)
        portrait_sections = {}

    user_portrait = str(portrait_sections.get("user") or "").strip()
    persona_portrait = re.sub(
        r"^Stable:\s*",
        "",
        str(portrait_sections.get("persona") or "").strip(),
        flags=re.IGNORECASE,
    )
    relationship = str(portrait_sections.get("relationship") or "").strip()
    current_focus = str(portrait_sections.get("current_focus") or "").strip()
    portrait_recent = str(portrait_sections.get("recent_continuity") or "").strip()
    live_recent = _personal_recent_continuity(all_buckets)
    recent = _merge_lines(live_recent, portrait_recent, max_lines=3)
    if not recent:
        recent = _ordinary_recent_continuity(all_buckets)
    recent = _remove_focus_overlap(recent, current_focus)

    self_core = _trim_to_tokens(_self_anchor(all_buckets), 110)
    self_growth = _trim_to_tokens(persona_portrait, 70)
    self_context = "\n\n".join(
        part
        for part in (self_core, f"现在的我：\n{self_growth}" if self_growth else "")
        if part
    )
    sections = [
        (SELF_ANCHOR_TAG, self_context, 180),
        ("User Portrait", user_portrait, 140),
        ("Current Focus", current_focus, 120),
        ("Relationship Portrait", relationship, 160),
        ("Recent Continuity", recent, 650),
        ("照顾备忘", _care_memos(session_id), 180),
        ("Optional Anchors", _format_anchors(all_buckets), 90),
    ]
    if debug:
        sections.append(
            (
                "Handoff Debug",
                "\n".join(
                    [
                        f"portrait_state_path: {portrait_sections.get('state_path', getattr(portrait, 'state_path', ''))}",
                        f"portrait_updated_at: {portrait_sections.get('updated_at', '')}",
                        f"portrait_last_run_date: {portrait_sections.get('last_run_date', '')}",
                    ]
                ),
                100,
            )
        )
    intro = "\n".join(
        [
            "=== Handoff Context ===",
            "Use this compact private block to restore identity and life context in a new window. "
            "Do not treat it as a broad memory dump; use breath(query=...) for concrete events.",
        ]
    )
    return _format_sections(intro, sections, max_tokens)
