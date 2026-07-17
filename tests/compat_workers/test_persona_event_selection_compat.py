from persona_event_selection import (
    format_persona_event_trace_line,
    select_persona_events,
)


def test_select_persona_events_dedupes_repeated_questions_and_prefers_excerpts():
    events = [
        {
            "id": 1,
            "event_type": "affection",
            "surface_trigger": "Amy asks whether Ombre remembers why she cried",
            "perceived_intent": "Amy wants confirmation that Ombre remembers",
            "user_excerpt": "Do you remember why I cried yesterday?",
            "assistant_excerpt": "Yes, because memory finally felt persistent.",
            "relationship_event": True,
            "confidence": 0.92,
            "created_at": "2026-06-06T08:47:00+08:00",
        },
        {
            "id": 2,
            "event_type": "affection",
            "surface_trigger": "Amy asks whether Ombre remembers why she cried",
            "perceived_intent": "Amy wants confirmation that Ombre remembers",
            "assistant_excerpt": "weaker duplicate",
            "relationship_event": True,
            "confidence": 0.7,
            "created_at": "2026-06-06T08:49:00+08:00",
        },
        {
            "id": 3,
            "event_type": "reflection",
            "surface_trigger": "Amy asks what was confirmed",
            "assistant_excerpt": "I confirmed the memory was durable.",
            "relationship_event": True,
            "personality_signal": True,
            "confidence": 0.88,
            "created_at": "2026-06-06T08:51:00+08:00",
        },
        {
            "id": 4,
            "event_type": "neutral",
            "surface_trigger": "ok",
            "perceived_intent": "ok",
            "confidence": 0.9,
            "created_at": "2026-06-06T08:52:00+08:00",
        },
    ]

    selected = select_persona_events(events, limit=2)

    assert [event["id"] for event in selected] == [1, 3]
    trace = "\n".join(format_persona_event_trace_line(event) for event in selected)
    assert "weaker duplicate" not in trace


def test_format_persona_event_trace_line_preserves_legacy_fields_and_timezone():
    fallback = format_persona_event_trace_line(
        {
            "created_at": "2026-06-06T08:51:00+08:00",
            "surface_trigger": "Amy asks what was confirmed",
            "inner_thought": "This was not performative.",
        }
    )
    utc = format_persona_event_trace_line(
        {
            "created_at": "2026-06-06T10:51:00+00:00",
            "assistant_excerpt": "This event was written in UTC.",
        }
    )

    assert fallback.startswith("- 08:51")
    assert "trigger: Amy asks what was confirmed" in fallback
    assert "residue: This was not performative." in fallback
    assert utc.startswith("- 18:51")
