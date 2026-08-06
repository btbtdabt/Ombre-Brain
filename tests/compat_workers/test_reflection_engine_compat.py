from reflection_engine import CLASSIFY_PROMPT, REFLECT_PROMPT, ReflectionEngine


def _config(worker_config):
    worker_config["reflection"] = {
        "enabled": True,
        "auto_enabled": False,
        "enrich_on_write": True,
        "api_key": "",
        "base_url": "",
        "model": "",
        "timezone": "Asia/Shanghai",
        "daily_chat_memory_api_key_env": "__OMBRE_TEST_NO_DAILY_CHAT_KEY__",
        "daily_chat_memory_api_key": "",
    }
    return worker_config


def test_daily_chat_memory_defaults_to_off(worker_config):
    engine = ReflectionEngine(_config(worker_config))

    assert engine.daily_chat_memory_mode == "off"
    assert engine._normalize_daily_chat_memory_mode("unknown") == "off"


def test_reflection_prompts_preserve_non_template_affect_contract(worker_config):
    engine = ReflectionEngine(_config(worker_config))

    assert "Fmaj9 -> C/E -> Am add9 -> G6sus4" not in REFLECT_PROMPT
    assert "Fmaj9 -> C/E -> Am add9 -> G6sus4" not in CLASSIFY_PROMPT
    assert "不编造材料之外的事件" in REFLECT_PROMPT
    assert "看不出关系时返回空 edges" in CLASSIFY_PROMPT
    assert "kind 只能是 key_event / stable_preference" in engine._daily_chat_memory_prompt()


def test_reflection_json_parser_accepts_wrapped_object(worker_config):
    engine = ReflectionEngine(_config(worker_config))

    parsed = engine._parse_json_object(
        '```json\n{"tags":["relationship_event"],"importance":6}\n```\ntail'
    )

    assert parsed == {"tags": ["relationship_event"], "importance": 6}


def test_daily_chat_windows_overlap_to_preserve_boundaries(worker_config):
    engine = ReflectionEngine(_config(worker_config))
    turns = [{"id": index} for index in range(1, 26)]

    windows = engine._daily_chat_memory_windows(turns)

    assert [[turn["id"] for turn in window] for window in windows] == [
        list(range(1, 15)),
        list(range(8, 22)),
        list(range(15, 26)),
    ]


def test_daily_chat_candidate_normalization_repairs_historical_domain_kinds(worker_config):
    engine = ReflectionEngine(_config(worker_config))

    candidates = engine._normalize_daily_chat_memory_candidates(
        "2026-07-01",
        [
            {
                "should_write": True,
                "kind": "relationship.symbol",
                "title": "A relationship symbol",
                "content": "Amy described two flames moving together as a durable relationship symbol.",
                "domain": "relationship.symbol",
                "tags": ["relationship.symbol"],
                "confidence": 0.95,
                "source_event_ids": [1, 2],
                "source_turn_ids": [8],
            },
            {
                "should_write": True,
                "kind": "project.companion_system",
                "title": "Bridge memory refactor",
                "content": "The Bridge memory pipeline now assembles context packets on the server.",
                "domain": "project.companion_system",
                "tags": ["project.companion_system"],
                "confidence": 0.9,
                "source_event_ids": [3, 4],
                "source_turn_ids": [15],
            },
        ],
        [
            {"id": 8, "raw_event_ids": [1, 2]},
            {"id": 15, "raw_event_ids": [3, 4]},
        ],
    )

    assert candidates[0]["kind"] == "relationship_anchor"
    assert candidates[0]["domain"] == ["relationship"]
    assert candidates[1]["kind"] == "project_state"
    assert candidates[1]["domain"] == ["project"]


def test_daily_chat_pending_state_refreshes_legacy_template_shell(worker_config):
    engine = ReflectionEngine(_config(worker_config))
    engine._save_daily_chat_memory_pending(
        [
            {
                "id": "old-candidate",
                "date": "2026-07-01",
                "status": "pending",
                "created_at": "2026-07-02T00:00:00+00:00",
                "candidate": {
                    "id": "old-candidate",
                    "date": "2026-07-01",
                    "kind": "key_event",
                    "title": "2026-07-01 自动记忆",
                    "content": "2026-07-01 发生了一件之后可能需要按日期回看的关键事件：笔友名单需要核对，未确认前不能写入。",
                    "confidence": 0.9,
                },
            }
        ]
    )

    item = engine.list_daily_chat_memory_pending()[0]["candidate"]

    assert item["title"].startswith("笔友名单需要核对")
    assert "自动记忆" not in item["title"]
    assert item["content"].startswith("笔友名单需要核对")


def test_reflection_anchor_varies_without_fixed_chord(worker_config):
    engine = ReflectionEngine(_config(worker_config))
    anchors = []
    for day in range(20, 28):
        key = f"2026-05-{day:02d}"
        anchors.append(
            engine._fallback_reflection_anchor(
                "daily",
                key,
                f"{key} 日印象",
                f"我从 weather {day} 里带走了一点关系温度。",
            )
        )

    chords = [anchor["chords"] for anchor in anchors]
    assert len(set(chords)) > 1
    assert "Fmaj9 -> C/E -> Am add9 -> G6sus4" not in chords
    assert all("meaning" not in anchor for anchor in anchors)
