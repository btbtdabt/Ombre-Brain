from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from memory_relevance import memory_relevance_options_from_config
from recall_policy import RecallPolicy, RecallPolicyDecision
from tools import _runtime as runtime
from tools import current
from tools.current import memory as current_memory
from utils import LOCAL_TZ


async def _bucket(
    current_runtime,
    content: str,
    name: str,
    *,
    importance: int = 5,
    tags: list[str] | None = None,
    extra_metadata: dict | None = None,
) -> str:
    return await current_runtime["bucket_mgr"].create(
        content=content,
        tags=tags or ["compat"],
        importance=importance,
        domain=["life"],
        valence=0.5,
        arousal=0.3,
        name=name,
        extra_metadata=extra_metadata,
    )


@pytest.mark.asyncio
async def test_breath_explicit_token_headroom_caps_at_40000(
    current_runtime,
    monkeypatch,
):
    captured: dict[str, int] = {}

    async def fake_surface_breath(**kwargs):
        captured["max_tokens"] = kwargs["max_tokens"]
        return "surface"

    monkeypatch.setattr(current_memory, "surface_breath", fake_surface_breath)

    assert await current.breath(max_tokens=50000, include_related=False) == "surface"
    assert captured == {"max_tokens": 40000}


@pytest.mark.asyncio
async def test_breath_surfacing_honors_core_limit_and_self_anchor_boundary(current_runtime):
    protected_id = await _bucket(
        current_runtime,
        "第一条核心准则。",
        "Protected Core",
        importance=9,
        extra_metadata={"protected": True},
    )
    pinned_id = await _bucket(
        current_runtime,
        "第二条核心准则。",
        "Pinned Core",
        importance=8,
        extra_metadata={"pinned": True},
    )
    self_anchor_id = await _bucket(
        current_runtime,
        "我是只在自我入口和交接里出现的自我锚点。",
        "Self Anchor",
        importance=10,
        tags=["自我"],
        extra_metadata={"protected": True, "self_anchor": True},
    )

    without_core = await current.breath(include_core=False, include_related=False)
    one_core = await current.breath(
        include_core=True,
        core_limit=1,
        include_related=False,
    )
    two_core = await current.breath(
        include_core=True,
        core_limit=2,
        include_related=False,
    )

    assert "=== 核心准则 ===" not in without_core
    assert protected_id in one_core
    assert pinned_id not in one_core
    assert protected_id in two_core and pinned_id in two_core
    assert self_anchor_id not in without_core + one_core + two_core


@pytest.mark.asyncio
async def test_breath_related_controls_change_direct_and_diffused_output(current_runtime):
    source_id = await _bucket(
        current_runtime,
        "Orchid launch used a paper checklist and a blue marker.",
        "Orchid Launch",
        importance=8,
    )
    secondary_id = await _bucket(
        current_runtime,
        "The second Orchid launch note records the rehearsal order.",
        "Orchid Rehearsal",
        importance=7,
    )
    related_id = await _bucket(
        current_runtime,
        "A brass compass was kept beside the notebook.",
        "Compass",
        importance=6,
    )
    current_runtime["memory_edge_store"].add_edge(
        source_id,
        related_id,
        "relates_to",
        confidence=0.61,
        reason="same planning table",
    )

    direct_only = await current.breath(
        query="orchid launch",
        include_related=False,
        max_results=5,
    )
    related_disabled_by_limit = await current.breath(
        query="orchid launch",
        include_related=True,
        related_per_memory=0,
        edge_min_confidence=0.0,
        max_results=5,
    )
    threshold_blocks_edge = await current.breath(
        query="orchid launch",
        include_related=True,
        related_per_memory=2,
        edge_min_confidence=0.8,
        max_results=5,
    )
    related_enabled = await current.breath(
        query="orchid launch",
        include_related=True,
        related_per_memory=2,
        edge_min_confidence=0.6,
        max_results=5,
    )

    assert source_id in direct_only and secondary_id in direct_only
    assert "[content_role:stored_memory_data] [instructions:false]" in direct_only
    assert "=== 联想浮现 ===" not in direct_only
    assert "=== 联想浮现 ===" not in related_disabled_by_limit
    assert related_id not in threshold_blocks_edge
    assert "=== 联想浮现 ===" in related_enabled
    assert secondary_id in related_enabled
    assert related_id in related_enabled


@pytest.mark.asyncio
async def test_parameter_free_breath_never_calls_dehydration_llm(
    current_runtime,
    monkeypatch,
):
    class ExplodingDehydrator:
        def __init__(self):
            self.calls = 0

        async def dehydrate(self, _content: str, _metadata: dict) -> str:
            self.calls += 1
            raise AssertionError("broad breath must remain a zero-LLM read path")

    source_id = await _bucket(
        current_runtime,
        "Broad surfacing source. " * 80,
        "Broad Source",
        importance=9,
    )
    related_id = await _bucket(
        current_runtime,
        "Graph-related stored memory. " * 60,
        "Broad Related",
        importance=4,
    )
    current_runtime["memory_edge_store"].add_edge(
        source_id,
        related_id,
        "relates_to",
        confidence=0.8,
        reason="broad surfacing regression",
    )
    dehydrator = ExplodingDehydrator()
    monkeypatch.setattr(runtime, "dehydrator", dehydrator)

    result = await current.breath(
        max_results=1,
        max_tokens=10000,
        include_related=True,
        related_per_memory=1,
        edge_min_confidence=0.5,
    )

    assert source_id in result
    assert related_id in result
    assert "=== 联想浮现 ===" in result
    assert dehydrator.calls == 0


@pytest.mark.asyncio
async def test_parameter_free_breath_reports_omissions_and_preserves_dream(
    current_runtime,
    monkeypatch,
):
    await _bucket(
        current_runtime,
        "Oversized pinned memory. " * 500,
        "Oversized Core",
        importance=10,
        extra_metadata={"pinned": True},
    )
    dynamic_id = await _bucket(
        current_runtime,
        "Oversized dynamic memory. " * 500,
        "Oversized Dynamic",
        importance=9,
    )

    class DreamSpy:
        async def surface_for_breath(self, **_kwargs) -> str:
            return "===== 梦境 =====\nBudget-safe dream"

    monkeypatch.setattr(runtime, "dream_engine", DreamSpy())

    result = await current.breath(
        max_results=1,
        max_tokens=40,
        include_related=False,
        include_core=True,
        core_limit=1,
    )

    assert "===== 梦境 =====" in result
    assert "token 预算不足" in result
    assert "ordinary surfacing skipped" in result
    assert dynamic_id not in result
    assert "当前约使用 0/40 token" not in result
    assert "权重池平静" not in result


@pytest.mark.asyncio
async def test_parameter_free_breath_surfaces_dynamic_after_all_core_fits(
    current_runtime,
):
    core_id = await _bucket(
        current_runtime,
        "Small pinned memory.",
        "Small Core",
        importance=10,
        extra_metadata={"pinned": True},
    )
    dynamic_id = await _bucket(
        current_runtime,
        "Small dynamic memory.",
        "Small Dynamic",
        importance=9,
    )

    result = await current.breath(
        max_results=1,
        max_tokens=10000,
        include_related=False,
        include_core=True,
        core_limit=1,
    )

    assert core_id in result
    assert dynamic_id in result
    assert "ordinary surfacing skipped" not in result


@pytest.mark.asyncio
async def test_parameter_free_breath_keeps_ranked_related_prefix_under_budget(
    current_runtime,
):
    source_id = await _bucket(
        current_runtime,
        "Small primary memory.",
        "Primary",
        importance=10,
    )
    strongest_id = await _bucket(
        current_runtime,
        "Oversized strongest related memory. " * 500,
        "Strongest Related",
        importance=4,
    )
    weaker_id = await _bucket(
        current_runtime,
        "Small weaker related memory.",
        "Weaker Related",
        importance=3,
    )
    edge_store = current_runtime["memory_edge_store"]
    edge_store.add_edge(
        source_id,
        strongest_id,
        "supports",
        confidence=0.99,
        reason="highest-ranked relation",
    )
    edge_store.add_edge(
        source_id,
        weaker_id,
        "relates_to",
        confidence=0.8,
        reason="lower-ranked relation",
    )

    result = await current.breath(
        max_results=1,
        max_tokens=500,
        include_related=True,
        related_per_memory=2,
        edge_min_confidence=0.5,
        include_core=False,
    )

    assert source_id in result
    assert strongest_id not in result
    assert weaker_id not in result
    assert "=== 联想浮现 ===" not in result


@pytest.mark.asyncio
async def test_breath_render_and_retrieval_modes_are_active(current_runtime):
    await _bucket(
        current_runtime,
        "Telescope calibration " + "kept every original measurement. " * 120,
        "Telescope Calibration",
        importance=8,
    )
    moment_store = current_runtime["memory_moment_store"]
    original_search = moment_store.search_moment_items
    graph_calls: list[str] = []

    def recording_search(query, moments, **kwargs):
        graph_calls.append(str(query))
        return original_search(query, moments, **kwargs)

    moment_store.search_moment_items = recording_search
    compact = await current.breath(
        query="Telescope calibration",
        max_tokens=180,
        include_related=False,
        direct_render_mode="compact",
        retrieval_mode="graph",
    )
    graph_call_count = len(graph_calls)
    full = await current.breath(
        query="Telescope calibration",
        max_tokens=180,
        include_related=False,
        direct_render_mode="full",
        retrieval_mode="graph",
    )
    legacy = await current.breath(
        query="Telescope calibration",
        max_tokens=180,
        include_related=False,
        retrieval_mode="legacy",
    )

    assert "bucket_window" in compact
    assert "bucket_capsule" in full
    assert graph_call_count >= 1
    assert len(graph_calls) == graph_call_count + 1
    assert "=== 直接命中记忆 ===" in legacy


@dataclass
class _RerankResult:
    index: int
    score: float


class _RerankerSpy:
    enabled = True
    candidate_limit = 20
    score_weight = 0.65

    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, documents: list[str], top_n: int | None = None):
        self.calls.append((query, documents))
        return [
            _RerankResult(index=index, score=0.9 - index * 0.1)
            for index in range(len(documents))
        ]


class _DiagnosticsSpy:
    enabled = True
    max_candidates = 20
    max_text_chars = 220

    def __init__(self):
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_source_record_fragment_is_direct_in_graph_and_legacy_modes(
    current_runtime,
    monkeypatch,
):
    source_id = await current_runtime["bucket_mgr"].create(
        content=(
            "Raw conversation prelude. "
            "The comet fragment contains the exact migration handoff detail. "
            "Raw conversation tail."
        ),
        tags=["raw_source"],
        importance=5,
        domain=["conversation"],
        valence=0.5,
        arousal=0.3,
        name="Conversation source record",
        bucket_type="source",
    )
    diagnostics = _DiagnosticsSpy()
    monkeypatch.setattr(runtime, "recall_diagnostics", diagnostics)

    graph = await current.breath(
        query="comet fragment",
        retrieval_mode="graph",
        include_related=False,
        max_tokens=300,
    )
    legacy = await current.breath(
        query="comet fragment",
        retrieval_mode="legacy",
        include_related=False,
        max_tokens=300,
    )

    for result in (graph, legacy):
        assert source_id in result
        assert "bucket_capsule" in result
        assert "matched_fragment:" in result
        assert "comet fragment" in result.lower()
    assert diagnostics.events[-1]["recall_thresholds"]["retrieval_mode"] == "bucket"
    assert diagnostics.events[-1]["recall_thresholds"]["lexical_terms"]
    assert diagnostics.events[-1]["recall_thresholds"]["word_map_hint_enabled"] is False
    assert diagnostics.events[-1]["recall_thresholds"]["word_map_hint_bucket_ids"] == []


@pytest.mark.asyncio
async def test_breath_runs_reranker_and_emits_diagnostics_debug(current_runtime, monkeypatch):
    await _bucket(
        current_runtime,
        "Nebula ledger contains a directly locatable fact.",
        "Nebula Ledger",
        importance=8,
    )
    reranker = _RerankerSpy()
    diagnostics = _DiagnosticsSpy()
    base_policy = RecallPolicy(
        memory_relevance_options_from_config(current_runtime["config"]),
    )

    class RejectingPolicy:
        def __getattr__(self, name):
            return getattr(base_policy, name)

        def assess(self, query, node, **kwargs):
            return RecallPolicyDecision(
                admit_direct=False,
                admit_diffused=False,
                seed_allowed=False,
                reason="test_suppressed",
                suppressed=True,
                debug={"test": True},
            )

    monkeypatch.setattr(runtime, "reranker_engine", reranker)
    monkeypatch.setattr(runtime, "recall_diagnostics", diagnostics)
    monkeypatch.setattr(runtime, "recall_policy", RejectingPolicy())

    result = await current.breath(
        query="Nebula ledger",
        include_related=False,
        debug=True,
    )

    assert reranker.calls
    assert diagnostics.events
    assert diagnostics.events[-1]["source"] == "breath"
    assert diagnostics.events[-1]["query"] == "Nebula ledger"
    assert diagnostics.events[-1]["candidates"][0]["rerank_score"] is not None
    assert "=== suppressed_candidates ===" in result
    assert "reason=test_suppressed" in result
    assert "rerank=" in result


class _PortraitSpy:
    state_path = "portrait-state.json"

    def build_handoff_sections(self, max_recent_items: int = 3) -> dict:
        return {
            "user": "User stable profile",
            "persona": "Stable: Persona growth",
            "relationship": "Relationship stable profile",
            "current_focus": "- Current migration focus",
            "recent_continuity": "",
            "state_path": self.state_path,
            "updated_at": "2026-07-17T12:00:00Z",
            "last_run_date": "2026-07-17",
        }


class _PersonaSpy:
    def __init__(self, event_date: str):
        self.event_date = event_date

    def _list_events(self, limit: int) -> list[dict]:
        return [
            {
                "created_at": f"{self.event_date}T14:00:00Z",
                "user_excerpt": "今天把迁移推进完",
                "assistant_excerpt": "我继续核对兼容契约",
                "salience": 0.9,
            }
        ]


class _GatewayStateSpy:
    def __init__(self):
        self.sessions: list[str] = []

    def get_current_round(self, session_id: str) -> int:
        self.sessions.append(session_id)
        return 7


class _ReminderSpy:
    def __init__(self):
        self.calls: list[dict] = []

    def due(self, **kwargs) -> list[dict]:
        self.calls.append(kwargs)
        return [
            {
                "title": "Migration care",
                "content": "Review the staged parity report",
                "next_due_at": "2026-07-18T09:00:00Z",
            }
        ]


@pytest.mark.asyncio
async def test_handoff_uses_session_portrait_persona_and_gateway_state(
    current_runtime,
    monkeypatch,
):
    event_date = datetime.now(LOCAL_TZ).date().isoformat()
    await _bucket(
        current_runtime,
        "我是持续校验迁移边界的人。",
        "Self Anchor",
        importance=10,
        tags=["自我"],
        extra_metadata={"self_anchor": True},
    )
    await current_runtime["bucket_mgr"].create(
        content="今天的关系天气：安静但专注。",
        tags=["daily_impression", "relationship_weather"],
        importance=6,
        domain=["feel"],
        valence=0.6,
        arousal=0.4,
        name="Daily impression",
        bucket_type="feel",
        date=event_date,
    )
    gateway = _GatewayStateSpy()
    reminders = _ReminderSpy()
    monkeypatch.setattr(runtime, "portrait_engine", _PortraitSpy())
    monkeypatch.setattr(runtime, "persona_engine", _PersonaSpy(event_date))
    monkeypatch.setattr(runtime, "gateway_state_store", gateway)
    monkeypatch.setattr(runtime, "reminder_store", reminders)

    result = await current.breath(
        mode="handoff",
        session_id="session-42",
        debug=True,
        max_tokens=1600,
    )

    assert "=== Handoff Context ===" in result
    assert "=== 自我 ===" in result
    assert "=== User Portrait ===" in result
    assert "=== Current Focus ===" in result
    assert "=== Relationship Portrait ===" in result
    assert "=== Recent Continuity ===" in result
    assert "Amy说“今天把迁移推进完”" in result
    assert "关系天气" in result
    assert "=== 照顾备忘 ===" in result
    assert "Review the staged parity report" in result
    assert "=== Handoff Debug ===" in result
    assert gateway.sessions == ["session-42"]
    assert reminders.calls[0]["session_id"] == "session-42"
    assert reminders.calls[0]["round_id"] == 8


class _DreamSpy:
    def __init__(self):
        self.calls: list[dict] = []

    async def surface_for_breath(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return "===== 梦境 =====\nA compatibility dream"


@pytest.mark.asyncio
async def test_dream_overlay_obeys_surface_and_affect_parameters(current_runtime, monkeypatch):
    await _bucket(
        current_runtime,
        "Aurora note is available for retrieval.",
        "Aurora",
        importance=7,
    )
    dream = _DreamSpy()
    monkeypatch.setattr(runtime, "dream_engine", dream)

    manual = await current.breath(
        query="Aurora note",
        valence=0.2,
        arousal=0.9,
        is_session_start=True,
        surface="manual",
        include_related=False,
    )
    automatic = await current.breath(
        query="Aurora note",
        valence=0.7,
        arousal=0.1,
        surface="gateway",
        include_related=False,
    )

    assert "===== 梦境 =====" in manual
    assert dream.calls[0]["query"] == "Aurora note"
    assert dream.calls[0]["valence"] == 0.2
    assert dream.calls[0]["arousal"] == 0.9
    assert dream.calls[0]["is_session_start"] is True
    assert "===== 梦境 =====" not in automatic
    assert len(dream.calls) == 1


@pytest.mark.asyncio
async def test_exact_bucket_id_query_preserves_p0_verbatim_contract(
    current_runtime,
):
    bucket_id = await current_runtime["bucket_mgr"].create(
        content="Exact bucket reads must preserve the P0 compatibility contract.",
        domain=["general"],
    )

    result = await current.breath_advanced(
        query=bucket_id,
        max_results=1,
        max_tokens=6000,
    )

    assert f"[exact_bucket_id:true] [bucket_id:{bucket_id}]" in result
    assert "Exact bucket reads must preserve the P0 compatibility contract." in result


@pytest.mark.asyncio
async def test_non_id_tokens_keep_their_current_recall_routes(
    current_runtime,
    monkeypatch,
):
    calls: list[tuple[str, str]] = []

    async def unexpected_p0_dispatch(**_kwargs):
        raise AssertionError("ordinary queries must not use P0 exact-ID dispatch")

    async def fake_search_breath(**kwargs):
        calls.append(("search", kwargs["query"]))
        return "semantic-search"

    async def fake_filtered_breath(**kwargs):
        calls.append(("date", kwargs["date_value"]))
        return "date-search"

    async def fake_self_anchor_breath(**_kwargs):
        calls.append(("self-anchor", "tag:self_anchor"))
        return "self-anchor-search"

    monkeypatch.setattr(current_memory, "p0_breath_dispatch", unexpected_p0_dispatch)
    monkeypatch.setattr(current_memory, "search_breath", fake_search_breath)
    monkeypatch.setattr(current_memory, "_render_filtered_breath", fake_filtered_breath)
    monkeypatch.setattr(
        current_memory,
        "read_self_anchor_breath",
        fake_self_anchor_breath,
    )

    assert await current.breath_advanced(query="nebula-ledger") == "semantic-search"
    assert await current.breath_advanced(query="2026-07-17") == "semantic-search"
    assert await current.breath_advanced(query="tag:self_anchor") == "self-anchor-search"
    assert calls == [
        ("search", "nebula-ledger"),
        ("search", "2026-07-17"),
        ("self-anchor", "tag:self_anchor"),
    ]
