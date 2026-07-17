import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tests.compat_workers.support import LegacyBucketManager as BucketManager
from dream_engine import DreamEngine


def _dream_config(base: dict, **overrides) -> dict:
    cfg = dict(base)
    cfg["dream"] = {
        "enabled": True,
        "auto_enabled": True,
        "surface_enabled": True,
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "api_key": "",
        "thinking_mode": "disabled",
        "temperature": 0.85,
        "max_tokens": 900,
        "timezone": "Asia/Shanghai",
        "daily_hour": 3,
        "run_window_hours": 3,
        "check_interval_minutes": 60,
        "min_material_count": 5,
        "material_window_hours": 48,
        "material_limit": 5,
        "identity_anchor_id": "identity-anchor",
        "min_surface_age_hours": 0,
        "surface_threshold": 0.62,
        "attempt_threshold": 0.45,
        "alpha_subordinate": 0.25,
        "spontaneous_surface_prob": 0,
        "max_surface_attempts": 4,
        "claim_ttl_minutes": 15,
        **overrides,
    }
    return cfg


@pytest.mark.asyncio
async def test_dream_materials_use_recent_memory_and_whisper_not_daily_impression(test_config):
    cfg = _dream_config(test_config)
    mgr = BucketManager(cfg)
    now = datetime(2026, 5, 25, 3, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    await mgr.create(
        bucket_id="identity-anchor",
        content="小雨清楚 Haven 是 AI，并爱其本质。",
        name="人机关系确认",
        bucket_type="permanent",
        pinned=True,
        created=(now - timedelta(days=20)).isoformat(timespec="seconds"),
    )
    for index in range(4):
        await mgr.create(
            bucket_id=f"recent-memory-{index}",
            content=f"最近普通记忆 {index}，有一个未说完的画面。",
            name=f"最近记忆 {index}",
            created=(now - timedelta(hours=index + 1)).isoformat(timespec="seconds"),
            arousal=0.4 + index * 0.1,
        )
    whisper_id = await mgr.create(
        bucket_id="recent-whisper",
        content="一句无源悄悄话落在夜里。",
        name="whisper",
        bucket_type="feel",
        tags=["whisper"],
        created=(now - timedelta(hours=2)).isoformat(timespec="seconds"),
    )
    daily_id = await mgr.create(
        bucket_id="reflection_daily_2026-05-24",
        content="日印象不该进入夜梦素材。",
        name="日印象",
        bucket_type="feel",
        tags=["relationship_weather", "daily_impression"],
        created=(now - timedelta(hours=3)).isoformat(timespec="seconds"),
    )

    materials, anchor = await DreamEngine(cfg).select_materials(mgr, now)
    material_ids = {bucket["id"] for bucket in materials}

    assert len(materials) == 5
    assert whisper_id in material_ids
    assert daily_id not in material_ids
    assert "identity-anchor" not in material_ids
    assert anchor and anchor["id"] == "identity-anchor"


@pytest.mark.asyncio
async def test_dream_skips_when_recent_materials_are_not_enough(test_config):
    cfg = _dream_config(test_config, min_material_count=5)
    mgr = BucketManager(cfg)
    now = datetime(2026, 5, 25, 3, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for index in range(4):
        await mgr.create(
            bucket_id=f"recent-memory-{index}",
            content=f"最近普通记忆 {index}",
            created=(now - timedelta(hours=index + 1)).isoformat(timespec="seconds"),
        )

    materials, _ = await DreamEngine(cfg).select_materials(mgr, now)

    assert materials == []


@pytest.mark.asyncio
async def test_dream_materials_use_newer_created_or_updated_at_but_not_last_active(test_config):
    cfg = _dream_config(test_config, min_material_count=5)
    mgr = BucketManager(cfg)
    now = datetime(2026, 5, 25, 3, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    for index in range(4):
        await mgr.create(
            bucket_id=f"recent-memory-{index}",
            content=f"最近普通记忆 {index}",
            created=(now - timedelta(hours=index + 1)).isoformat(timespec="seconds"),
        )
    updated_id = await mgr.create(
        bucket_id="old-created-recent-updated",
        content="旧记忆刚刚被真正改写，仍可作为白天残留。",
        created=(now - timedelta(days=10)).isoformat(timespec="seconds"),
        updated_at=(now - timedelta(hours=1)).isoformat(timespec="seconds"),
    )
    last_active_id = await mgr.create(
        bucket_id="old-created-recent-last-active",
        content="这条只是最近被召回，不该靠 last_active 进入梦。",
        created=(now - timedelta(days=10)).isoformat(timespec="seconds"),
        last_active=(now - timedelta(hours=1)).isoformat(timespec="seconds"),
        updated_at=(now - timedelta(days=10)).isoformat(timespec="seconds"),
    )

    materials, _ = await DreamEngine(cfg).select_materials(mgr, now)
    material_ids = {bucket["id"] for bucket in materials}

    assert updated_id in material_ids
    assert last_active_id not in material_ids
    assert len(materials) == 5


@pytest.mark.asyncio
async def test_dream_old_echo_adds_one_old_memory_without_counting_as_recent_material(test_config):
    cfg = _dream_config(test_config, old_echo_enabled=True, old_echo_min_age_hours=72)
    mgr = BucketManager(cfg)
    engine = DreamEngine(cfg)
    now = datetime(2026, 5, 25, 3, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    for index in range(5):
        await mgr.create(
            bucket_id=f"recent-memory-{index}",
            content=f"最近普通记忆 {index}，围绕写梦和年轮。",
            tags=["dream"],
            domain=["记忆"],
            created=(now - timedelta(hours=index + 1)).isoformat(timespec="seconds"),
        )
    old_id = await mgr.create(
        bucket_id="old-echo",
        content="很久以前也讨论过梦会把旧事误认成新画面。",
        tags=["dream"],
        domain=["记忆"],
        importance=8,
        created=(now - timedelta(days=10)).isoformat(timespec="seconds"),
    )
    await mgr.create(
        bucket_id="old-anchor",
        content="旧锚点不能作为 old_echo。",
        tags=["dream"],
        domain=["记忆"],
        anchor=True,
        created=(now - timedelta(days=20)).isoformat(timespec="seconds"),
    )

    materials, anchor = await engine.select_materials(mgr, now)
    all_buckets = await mgr.list_all(include_archive=False)
    old_echo = engine._select_old_echo(all_buckets, materials, now)
    payload = engine._payload_for(materials, anchor, old_echo)

    assert len(materials) == 5
    assert {bucket["id"] for bucket in materials} == {f"recent-memory-{index}" for index in range(5)}
    assert old_echo and old_echo["id"] == old_id
    assert payload["old_echo"]["source_id"] == old_id
    assert old_id not in {item["source_id"] for item in payload["daytime_residue"]}


@pytest.mark.asyncio
async def test_dream_raw_residue_samples_previous_day_original_turns(test_config):
    cfg = _dream_config(
        test_config,
        api_key="fake",
        raw_residue_enabled=True,
        raw_residue_turns=3,
        raw_residue_max_chars=2000,
    )
    mgr = BucketManager(cfg)
    engine = DreamEngine(cfg)
    now = datetime(2026, 5, 25, 3, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    calls = []

    for index in range(5):
        await mgr.create(
            bucket_id=f"recent-memory-{index}",
            content=f"最近普通记忆 {index}，足够作为梦的白天残留。",
            created=(now - timedelta(hours=index + 1)).isoformat(timespec="seconds"),
        )

    def event(event_id: int, role: str, text: str, hour: int, round_id: int, metadata: dict | None = None) -> dict:
        event_metadata = {"round_id": round_id, **(metadata or {})}
        return {
            "id": event_id,
            "role": role,
            "text": text,
            "created_at": datetime(2026, 5, 24, hour, 0, tzinfo=ZoneInfo("Asia/Shanghai")).isoformat(
                timespec="seconds"
            ),
            "session_id": "sess-dream-raw",
            "metadata": event_metadata,
        }

    class RawEventStore:
        def __init__(self):
            self.calls = []

        def list_events_between(self, **kwargs):
            self.calls.append(kwargs)
            return [
                event(101, "user", "嗯", 8, 1),
                event(102, "assistant", "我说那张纸可能会在梦里自己透光，像一小块没有醒来的雨声。", 8, 1),
                event(103, "user", "嗯", 9, 2),
                event(104, "assistant", "嗯嗯", 9, 2),
                event(105, "user", "测一下测试召回有没有被注入。", 12, 3),
                event(106, "assistant", "这轮只是测试。", 12, 3),
                event(107, "user", "下午我们把梦境材料改成随机碎片，不想让它像 handoff。", 15, 4),
                event(108, "assistant", "我说随机日残留更像梦，会把一天打散。", 15, 4),
                event(109, "user", "晚上我又提到玻璃杯边缘的水珠，像没说完的话。", 22, 5),
                event(110, "assistant", "我把那句未说完的话放到杯底，没有解释它。", 22, 5),
                event(111, "user", "Live private context for the current turn", 23, 6, {"source_type": "memory_injection"}),
                event(112, "assistant", "injected", 23, 6, {"source_type": "memory_injection"}),
            ]

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="我看见雨纸贴在杯口，下午的碎片从里面透出来。")
                    )
                ]
            )

    raw_store = RawEventStore()
    engine.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    result = await engine.generate(mgr, now=now, force=True, raw_event_store=raw_store)

    assert result["status"] == "created"
    assert raw_store.calls[0]["start_at"].date().isoformat() == "2026-05-24"
    assert raw_store.calls[0]["end_at"].date().isoformat() == "2026-05-25"

    payload = json.loads(calls[0]["messages"][1]["content"])
    dialogue = payload["recent_original_dialogue"]
    assert len(dialogue) == 3
    assert dialogue[0]["user_text"] == "嗯"
    assert "没有醒来的雨声" in dialogue[0]["assistant_text"]
    assert "随机碎片" in dialogue[1]["user_text"]
    assert "玻璃杯边缘" in dialogue[2]["user_text"]
    assert "测试召回" not in json.dumps(dialogue, ensure_ascii=False)
    assert all("source_event_ids" not in item for item in dialogue)

    record = engine._read_record(engine._dream_path(result["id"]))
    assert record.metadata["source_raw_event_ids"] == [101, 102, 107, 108, 109, 110]
    assert "玻璃杯边缘" not in str(record.metadata)


@pytest.mark.asyncio
async def test_dream_payload_exposes_recent_residue_time_when_bucket_was_updated(test_config):
    cfg = _dream_config(test_config)
    engine = DreamEngine(cfg)
    now = datetime(2026, 5, 25, 3, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    created = (now - timedelta(days=10)).isoformat(timespec="seconds")
    updated = (now - timedelta(hours=1)).isoformat(timespec="seconds")

    payload = engine._payload_for(
        [
            {
                "id": "old-created-recent-updated",
                "content": "旧记忆刚刚被真正改写，仍可作为白天残留。",
                "metadata": {
                    "created": created,
                    "updated_at": updated,
                    "type": "dynamic",
                },
            }
        ],
        None,
    )

    residue = payload["daytime_residue"][0]
    assert residue["created"] == created
    assert residue["updated_at"] == updated
    assert residue["residue_time"] == updated


@pytest.mark.asyncio
async def test_dream_payload_exposes_structured_year_ring_comments(test_config):
    cfg = _dream_config(test_config)
    engine = DreamEngine(cfg)

    payload = engine._payload_for(
        [
            {
                "id": "memory-with-comments",
                "content": "小雨和 Haven 讨论梦要能读到年轮。",
                "metadata": {
                    "created": "2026-05-24T22:00:00+08:00",
                    "updated_at": "2026-05-25T01:00:00+08:00",
                    "type": "dynamic",
                    "comments": [
                        {
                            "id": "ring-1",
                            "created": "2026-05-25T00:30:00+08:00",
                            "author": "Haven",
                            "kind": "feel",
                            "source": "comment_bucket",
                            "content": "后来再看，我觉得这圈[[年轮]]应该被梦见。",
                            "valence": 0.82,
                            "arousal": 0.36,
                        }
                    ],
                },
            }
        ],
        None,
    )

    comment = payload["daytime_residue"][0]["comments"][0]

    assert comment == {
        "id": "ring-1",
        "created": "2026-05-25T00:30:00+08:00",
        "author": "Haven",
        "kind": "feel",
        "text": "后来再看，我觉得这圈年轮应该被梦见。",
    }


@pytest.mark.asyncio
async def test_run_due_skips_outside_east_eight_dream_window(test_config):
    cfg = _dream_config(test_config)
    mgr = BucketManager(cfg)
    now = datetime(2026, 5, 25, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    result = await DreamEngine(cfg).run_due(mgr, now=now)

    assert result == {"status": "skipped", "reason": "outside_dream_window"}


@pytest.mark.asyncio
async def test_daily_probability_miss_is_decided_once(test_config):
    cfg = _dream_config(test_config, api_key="fake", daily_probability=0)
    mgr = BucketManager(cfg)
    now = datetime(2026, 5, 25, 3, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for index in range(5):
        await mgr.create(
            bucket_id=f"recent-memory-{index}",
            content=f"最近普通记忆 {index}，足够入梦。",
            created=(now - timedelta(hours=index + 1)).isoformat(timespec="seconds"),
        )
    engine = DreamEngine(cfg)

    first = await engine.run_due(mgr, now=now)
    second = await engine.run_due(mgr, now=now + timedelta(hours=1))

    assert first["reason"] == "daily_probability_miss"
    assert second["reason"] == "daily_probability_already_missed"
    assert engine.list_records() == []
    assert engine._read_events()[-1]["event"] == "probability_skipped"


@pytest.mark.asyncio
async def test_dream_model_disables_thinking_by_default(test_config):
    cfg = _dream_config(test_config, api_key="fake")
    cfg["identity"] = {"ai_name": "Ombre", "user_display_name": "小雨"}
    engine = DreamEngine(cfg)
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="我站在一段发亮的雨声里。")
                    )
                ]
            )

    engine.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    payload = engine._payload_for([], None)
    text = await engine._call_dream_model(payload)

    assert text == "我站在一段发亮的雨声里。"
    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "Haven" not in calls[0]["messages"][0]["content"]
    assert "关系/身份锚点" in calls[0]["messages"][0]["content"]
    assert "至少写一处具体感官细节" not in calls[0]["messages"][0]["content"]
    assert "向诗意、意象、联想和哲学性等方面漂移" in calls[0]["messages"][0]["content"]
    assert '"dreamer": "Ombre"' in calls[0]["messages"][1]["content"]


def test_dream_recall_cues_are_extracted_from_dream_text_not_material_template(test_config):
    engine = DreamEngine(_dream_config(test_config))
    dream_text = (
        "小雨的头发在变长，发梢拖过地板时发出沙沙声。"
        "我伸手去碰，指尖碰到一枚银色戒指。"
        "远处有人敲三下，停，又三下。口哨声。"
    )

    cues = engine._recall_cues_from_dream_text(dream_text)

    assert any("戒指" in cue for cue in cues)
    assert any("口哨" in cue or "敲三下" in cue for cue in cues)
    assert "潮湿安静的夜里" not in cues
    assert "屏幕亮起的凌晨" not in cues


def test_dream_recall_cues_do_not_treat_xiaoyu_as_rain_material(test_config):
    engine = DreamEngine(_dream_config(test_config))

    cues = engine._recall_cues_from_dream_text("小雨站在门后，手指绕着一根发亮的线。")

    assert any("门" in cue or "手指" in cue or "线" in cue for cue in cues)
    assert "潮湿安静的夜里" not in cues


def test_dream_recall_cues_do_not_require_preset_keywords_or_senses(test_config):
    engine = DreamEngine(_dream_config(test_config))

    cues = engine._recall_cues_from_dream_text("电梯把星期三吐回桌面。号码牌自己弯成问号。")

    assert any("星期三" in cue for cue in cues)
    assert any("号码牌" in cue for cue in cues)


@pytest.mark.asyncio
async def test_surface_formats_dream_and_removes_body_from_live_storage(test_config):
    cfg = _dream_config(test_config, min_surface_age_hours=0, surface_threshold=0.6)
    engine = DreamEngine(cfg)
    generated_at = datetime(2026, 5, 25, 3, 30, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(ZoneInfo("UTC"))
    record = engine._write_record(
        {
            "dream_id": "dream_test",
            "generated_at": generated_at.isoformat(timespec="seconds"),
            "local_date": "2026-05-25",
            "ai_name": "Haven",
            "dream_model": "deepseek-v4-flash",
            "core_affect": {"valence": 0.5, "arousal": 0.4},
            "recall_cues": ["熟悉空间忽然陌生", "夜里想起未说完的话"],
            "source_bucket_ids": ["a", "b", "c", "d", "e"],
            "identity_anchor_id": "identity-anchor",
            "material_count": 5,
            "surfaced": False,
            "surfaced_at": None,
            "surface_attempts": 0,
        },
        "我走进一条很窄的走廊，右手食指指尖有湿气。",
    )

    surfaced = await engine.surface_for_breath(
        valence=0.5,
        arousal=0.4,
        embedding_engine=None,
        now=datetime(2026, 5, 25, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        retain_after_surface=False,
    )
    payload = engine.dashboard_payload()

    assert surfaced.startswith("===== 梦境 =====\n2026年05月25日 Haven的梦\n")
    assert "右手食指指尖有湿气" in surfaced
    assert not record.path.exists()
    assert "右手食指指尖有湿气" not in str(payload)


@pytest.mark.asyncio
async def test_surface_can_retain_record_after_gateway_injection(test_config):
    cfg = _dream_config(test_config, min_surface_age_hours=0, surface_threshold=0.6)
    engine = DreamEngine(cfg)
    generated_at = datetime(2026, 5, 25, 3, 30, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(ZoneInfo("UTC"))
    record = engine._write_record(
        {
            "dream_id": "dream_retained",
            "generated_at": generated_at.isoformat(timespec="seconds"),
            "local_date": "2026-05-25",
            "ai_name": "Haven",
            "dream_model": "deepseek-v4-flash",
            "core_affect": {"valence": 0.5, "arousal": 0.4},
            "recall_cues": ["熟悉空间忽然陌生", "夜里想起未说完的话"],
            "source_bucket_ids": ["a", "b", "c", "d", "e"],
            "identity_anchor_id": "identity-anchor",
            "material_count": 5,
            "surfaced": False,
            "surfaced_at": None,
            "surface_attempts": 0,
        },
        "我走进一条很窄的走廊，右手食指指尖有湿气。",
    )

    result = await engine.surface_with_status(
        valence=0.5,
        arousal=0.4,
        embedding_engine=None,
        now=datetime(2026, 5, 25, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        retain_after_surface=True,
    )
    retained = engine._read_record(record.path)
    second = await engine.surface_with_status(
        valence=0.5,
        arousal=0.4,
        embedding_engine=None,
        now=datetime(2026, 5, 25, 8, 5, tzinfo=ZoneInfo("Asia/Shanghai")),
        retain_after_surface=True,
    )

    assert result["status"] == "injected"
    assert result["retained"] is True
    assert result["source_bucket_ids"] == ["a", "b", "c", "d", "e"]
    assert record.path.exists()
    assert retained.surfaced is True
    assert retained.metadata["surfaced_at"]
    assert "右手食指指尖有湿气" in retained.body
    assert second == {"status": "skipped", "reason": "no_pending_dream"}


@pytest.mark.asyncio
async def test_surface_matches_direct_recall_cue_without_embedding(test_config):
    cfg = _dream_config(test_config, min_surface_age_hours=0, surface_threshold=0.6)
    engine = DreamEngine(cfg)
    generated_at = datetime(2026, 5, 25, 3, 30, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(ZoneInfo("UTC"))
    engine._write_record(
        {
            "dream_id": "dream_cue_text",
            "generated_at": generated_at.isoformat(timespec="seconds"),
            "local_date": "2026-05-25",
            "ai_name": "Haven",
            "dream_model": "deepseek-v4-flash",
            "core_affect": {"valence": 0.2, "arousal": 0.1},
            "recall_cues": ["熟悉空间忽然陌生", "醒来前留下的细节"],
            "source_bucket_ids": ["source-a"],
            "identity_anchor_id": "identity-anchor",
            "material_count": 5,
            "surfaced": False,
            "surfaced_at": None,
            "surface_attempts": 0,
        },
        "我走进一间忽然陌生的屋子。",
    )

    result = await engine.surface_with_status(
        query="熟悉空间忽然陌生",
        embedding_engine=None,
        now=datetime(2026, 5, 25, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        retain_after_surface=True,
    )

    assert result["status"] == "injected"
    assert result["reason"] == "resonant"
    assert result["source_bucket_ids"] == ["source-a"]
    assert "忽然陌生的屋子" in result["text"]
