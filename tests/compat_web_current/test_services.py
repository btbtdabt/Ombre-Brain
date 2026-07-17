from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from starlette.responses import Response

from web.current_services import (
    CurrentServiceAdapters,
    CurrentServiceDependencies,
    build_current_services,
)

from .conftest import response_json


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def _response_payload(value: object, status_code: int) -> dict:
    assert isinstance(value, Response)
    assert value.status_code == status_code
    payload = response_json(value)
    assert isinstance(payload, dict)
    return payload


class BucketManager:
    def __init__(self, buckets: list[dict]) -> None:
        self.buckets = {str(bucket["id"]): bucket for bucket in buckets}

    async def get(self, bucket_id: str) -> dict | None:
        return self.buckets.get(bucket_id)

    async def list_all(self, *, include_archive: bool) -> list[dict]:
        assert include_archive is True
        return list(self.buckets.values())


@pytest.mark.asyncio
async def test_inspect_callbacks_validate_query_and_forward_arguments() -> None:
    calls: list[tuple[str, dict]] = []

    async def diffusion(**kwargs):
        calls.append(("diffusion", kwargs))
        return {"status": "ok", "hits": []}

    async def recall(**kwargs):
        calls.append(("recall", kwargs))
        return {"status": "ok", "candidates": []}

    adapters = CurrentServiceAdapters(
        CurrentServiceDependencies(
            inspect_diffusion_operation=diffusion,
            inspect_recall_operation=recall,
        )
    )

    assert await adapters.inspect_diffusion(query="   ") == {
        "status": "error",
        "error": "query_required",
    }
    assert await adapters.inspect_recall(query="") == {
        "status": "error",
        "error": "query_required",
    }
    assert calls == []

    assert await adapters.inspect_diffusion(
        query="  shared promise  ",
        max_seeds=99,
        max_hits=-1,
        edge_min_confidence=2.0,
    ) == {"status": "ok", "hits": []}
    assert await adapters.inspect_recall(
        query="  shared promise  ",
        max_candidates=200,
        max_results=0,
        max_tokens=50000,
        direct_render_mode="compact",
        domain="relationship, profile",
        valence=0.6,
        arousal=3.0,
    ) == {"status": "ok", "candidates": []}

    assert calls == [
        (
            "diffusion",
            {
                "query": "shared promise",
                "max_seeds": 20,
                "max_hits": 0,
                "edge_min_confidence": 1.0,
            },
        ),
        (
            "recall",
            {
                "query": "shared promise",
                "max_candidates": 100,
                "max_results": 1,
                "max_tokens": 20000,
                "direct_render_mode": "compact",
                "domain": "relationship, profile",
                "valence": 0.6,
                "arousal": None,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_inspect_callback_fails_closed_without_operation() -> None:
    adapters = CurrentServiceAdapters(CurrentServiceDependencies())

    result = await adapters.inspect_diffusion(query="memory")

    payload = _response_payload(result, 503)
    assert "inspect_diffusion_operation" in payload["error"]


@pytest.mark.asyncio
async def test_recall_and_profile_callbacks_fail_closed_without_operations() -> None:
    evidence = {
        "id": "evidence-1",
        "content": "Direct evidence",
        "metadata": {"name": "Evidence"},
    }
    adapters = CurrentServiceAdapters(CurrentServiceDependencies(bucket_mgr=BucketManager([evidence])))

    recall = await adapters.inspect_recall(query="memory")
    proposals = await adapters.profile_fact_proposals({"bucket_id": "evidence-1"})
    confirm = await adapters.profile_fact_confirm(
        {
            "fact": "Amy values direct evidence",
            "evidence_bucket_id": "evidence-1",
        }
    )

    assert "inspect_recall_operation" in _response_payload(recall, 503)["error"]
    assert "profile_fact_proposal_model" in _response_payload(proposals, 503)["error"]
    assert "profile_fact_writer" in _response_payload(confirm, 503)["error"]


@pytest.mark.asyncio
async def test_profile_fact_proposals_validate_evidence_and_normalize_model_output() -> None:
    evidence = {
        "id": "evidence-1",
        "content": "Amy consistently avoids surprise calls.",
        "metadata": {"name": "Calls", "tags": ["relationship"]},
    }
    existing = {
        "id": "profile-1",
        "content": "### fact\nAmy likes tea。",
        "metadata": {"tags": ["profile_fact"]},
    }
    model_calls: list[dict] = []

    async def proposal_model(**kwargs):
        model_calls.append(kwargs)
        return json.dumps(
            [
                {
                    "fact": "Unsupported",
                    "evidence_bucket_id": "someone-else",
                },
                {
                    "fact": "Amy likes tea",
                    "evidence_bucket_id": "evidence-1",
                },
                {
                    "fact": "Amy avoids surprise calls",
                    "profile_kind": " Personal Boundary! ",
                    "subject": " User ",
                    "predicate": " Avoids Calls ",
                    "object": "surprise calls" * 30,
                    "evidence_bucket_id": "evidence-1",
                    "confidence": 4,
                    "reason": "Directly stated in the source.",
                },
            ]
        )

    adapters = CurrentServiceAdapters(
        CurrentServiceDependencies(
            bucket_mgr=BucketManager([evidence, existing]),
            profile_fact_proposal_model=proposal_model,
            model_name="proposal-model",
        )
    )

    result = await adapters.profile_fact_proposals(
        {
            "bucket_id": "evidence-1",
            "evidence_moment_id": "moment-1",
            "max_proposals": 9,
        }
    )

    assert isinstance(result, dict)
    assert result["status"] == "ok"
    assert result["model"] == "proposal-model"
    assert result["evidence"] == {
        "bucket_id": "evidence-1",
        "moment_id": "moment-1",
        "name": "Calls",
    }
    assert result["proposals"] == [
        {
            "fact": "Amy avoids surprise calls",
            "profile_kind": "personal_boundary",
            "subject": "user",
            "predicate": "avoids_calls",
            "object": ("surprise calls" * 30)[:160],
            "evidence_bucket_id": "evidence-1",
            "evidence_moment_id": "moment-1",
            "confidence": 1.0,
            "reason": "Directly stated in the source.",
        }
    ]
    assert [item["reason"] for item in result["rejected"]] == [
        "evidence_bucket_id mismatch",
        "duplicate profile fact",
    ]
    assert model_calls == [
        {
            "bucket": evidence,
            "evidence_moment_id": "moment-1",
            "max_proposals": 3,
        }
    ]


@pytest.mark.asyncio
async def test_profile_fact_proposals_reject_profile_fact_as_evidence() -> None:
    profile = {
        "id": "profile-1",
        "content": "### fact\nExisting fact",
        "metadata": {"tags": ["profile_fact"]},
    }
    model = pytest.fail
    adapters = CurrentServiceAdapters(
        CurrentServiceDependencies(
            bucket_mgr=BucketManager([profile]),
            profile_fact_proposal_model=model,
        )
    )

    result = await adapters.profile_fact_proposals({"bucket_id": "profile-1"})

    assert _response_payload(result, 400) == {"error": "profile_fact bucket cannot be evidence for proposal"}


@pytest.mark.asyncio
async def test_profile_fact_confirm_rejects_missing_or_duplicate_evidence() -> None:
    evidence = {
        "id": "evidence-1",
        "content": "Source",
        "metadata": {"name": "Source"},
    }
    existing = {
        "id": "profile-1",
        "content": "### fact\nAmy keeps promises",
        "metadata": {"tags": ["profile_fact"]},
    }
    adapters = CurrentServiceAdapters(
        CurrentServiceDependencies(
            bucket_mgr=BucketManager([evidence, existing]),
            profile_fact_writer=pytest.fail,
        )
    )

    missing = await adapters.profile_fact_confirm(
        {
            "fact": "Amy values evidence",
            "evidence_bucket_id": "missing",
        }
    )
    invalid_moment = await adapters.profile_fact_confirm(
        {
            "fact": "Amy values evidence",
            "evidence_bucket_id": "evidence-1",
            "evidence_moment_id": "../moment",
        }
    )
    duplicate = await adapters.profile_fact_confirm(
        {
            "fact": "Amy keeps promises",
            "evidence_bucket_id": "evidence-1",
        }
    )

    assert _response_payload(missing, 404) == {"error": "evidence bucket not found"}
    assert _response_payload(invalid_moment, 400) == {"error": "invalid evidence_moment_id"}
    assert _response_payload(duplicate, 400) == {"error": "duplicate profile fact"}


@pytest.mark.asyncio
async def test_profile_fact_confirm_writes_normalized_evidence_bound_fact() -> None:
    evidence = {
        "id": "evidence-1",
        "content": "Source",
        "metadata": {"name": "Source"},
    }
    created = {
        "id": "profile-new",
        "content": "### fact\nAmy avoids surprise calls",
        "metadata": {
            "name": "Boundary",
            "tags": ["profile_fact", "profile_boundary"],
            "profile_kind": "boundary",
            "subject": "user",
            "predicate": "avoids_calls",
            "object": "surprise calls",
            "confidence": 0.91,
            "source": "profile_fact",
            "evidence": [{"bucket_id": "evidence-1", "moment_id": "moment-1"}],
        },
    }
    manager = BucketManager([evidence])
    writer_calls: list[dict] = []

    async def writer(**kwargs):
        writer_calls.append(kwargs)
        manager.buckets["profile-new"] = created
        return "profile_fact→profile-new evidence→evidence-1 moment=moment-1"

    adapters = CurrentServiceAdapters(
        CurrentServiceDependencies(
            bucket_mgr=manager,
            profile_fact_writer=writer,
        )
    )

    result = await adapters.profile_fact_confirm(
        {
            "fact": " Amy avoids surprise calls ",
            "profile_kind": " Boundary ",
            "subject": " User ",
            "predicate": " Avoids Calls ",
            "object": "surprise calls",
            "evidence_bucket_id": "evidence-1",
            "evidence_moment_id": "moment-1",
            "confidence": 0.91,
            "reason": "Direct statement",
        }
    )

    assert isinstance(result, dict)
    assert result["status"] == "created"
    assert result["id"] == "profile-new"
    assert result["fact"]["fact"] == "Amy avoids surprise calls"
    assert result["fact"]["evidence"] == [
        {
            "bucket_id": "evidence-1",
            "moment_id": "moment-1",
            "name": "Source",
            "exists": True,
        }
    ]
    assert writer_calls == [
        {
            "fact": "Amy avoids surprise calls",
            "evidence_bucket_id": "evidence-1",
            "profile_kind": "boundary",
            "subject": "user",
            "predicate": "avoids_calls",
            "object_value": "surprise calls",
            "evidence_moment_id": "moment-1",
            "evidence_context": "Direct statement",
            "reflection": "",
            "confidence": 0.91,
        }
    ]


@pytest.mark.asyncio
async def test_profile_fact_confirm_preserves_legacy_evidence_without_edge_duplicate() -> None:
    evidence = {
        "id": "evidence-1",
        "content": "Source",
        "metadata": {"name": "Source"},
    }
    created = {
        "id": "profile-new",
        "content": "### fact\nAmy values direct evidence",
        "metadata": {
            "tags": ["profile_fact"],
            "evidence_bucket_id": "evidence-1",
            "evidence_moment_id": "moment-1",
        },
    }
    manager = BucketManager([evidence])

    async def writer(**_kwargs):
        manager.buckets["profile-new"] = created
        return "profile_fact→profile-new evidence→evidence-1 moment=moment-1"

    edge_store = SimpleNamespace(
        list_edges=lambda: [
            {
                "source": "profile-new",
                "target": "evidence-1",
                "relation_type": "evidenced_by",
            }
        ]
    )
    adapters = CurrentServiceAdapters(
        CurrentServiceDependencies(
            bucket_mgr=manager,
            memory_edge_store=edge_store,
            profile_fact_writer=writer,
        )
    )

    result = await adapters.profile_fact_confirm(
        {
            "fact": "Amy values direct evidence",
            "evidence_bucket_id": "evidence-1",
            "evidence_moment_id": "moment-1",
        }
    )

    assert isinstance(result, dict)
    assert result["fact"]["evidence"] == [
        {
            "bucket_id": "evidence-1",
            "moment_id": "moment-1",
            "name": "Source",
            "exists": True,
        }
    ]


@pytest.mark.parametrize(
    ("metadata", "reason"),
    [
        ({"anchor": True}, "already anchor"),
        ({"pinned": True}, "pinned/protected buckets are not anchor proposal targets"),
        ({"protected": True}, "pinned/protected buckets are not anchor proposal targets"),
        ({"tags": ["profile_fact"]}, "profile_fact buckets are not anchor proposal targets"),
        ({"type": "feel"}, "feel buckets are not anchor proposal targets"),
    ],
)
@pytest.mark.asyncio
async def test_anchor_proposals_apply_static_rejections(metadata: dict, reason: str) -> None:
    bucket = {"id": "memory-1", "content": "Source", "metadata": metadata}
    adapters = CurrentServiceAdapters(
        CurrentServiceDependencies(
            bucket_mgr=BucketManager([bucket]),
            anchor_proposal_model=pytest.fail,
            clock=lambda: NOW,
        )
    )

    result = await adapters.anchor_proposals({"bucket_id": "memory-1"})

    assert isinstance(result, dict)
    assert result["status"] == "ok"
    assert result["proposals"] == []
    assert result["rejected"] == [{"reason": reason, "bucket_id": "memory-1"}]


@pytest.mark.asyncio
async def test_anchor_proposals_apply_age_and_capacity_gates_before_model() -> None:
    young = {
        "id": "young",
        "content": "New",
        "metadata": {"created": "2026-07-17T11:00:00+00:00"},
    }
    candidate = {
        "id": "candidate",
        "content": "Old",
        "metadata": {"created": "2026-07-01T00:00:00+00:00"},
    }
    existing_anchor = {
        "id": "anchor-1",
        "content": "Anchor",
        "metadata": {"anchor": True},
    }
    adapters = CurrentServiceAdapters(
        CurrentServiceDependencies(
            bucket_mgr=BucketManager([young, candidate, existing_anchor]),
            anchor_proposal_model=pytest.fail,
            config={"anchor": {"min_age_hours": 24, "max_count": 1}},
            clock=lambda: NOW,
        )
    )

    young_result = await adapters.anchor_proposals({"bucket_id": "young"})
    full_result = await adapters.anchor_proposals({"bucket_id": "candidate"})

    assert isinstance(young_result, dict)
    assert "至少等待 24 小时" in young_result["rejected"][0]["reason"]
    assert isinstance(full_result, dict)
    assert "名额已满" in full_result["rejected"][0]["reason"]


@pytest.mark.asyncio
async def test_anchor_proposals_normalize_one_model_candidate() -> None:
    bucket = {
        "id": "memory-1",
        "content": "A durable shared commitment",
        "metadata": {
            "name": "Commitment",
            "created": "2026-06-01T00:00:00+00:00",
        },
    }

    async def model(**kwargs):
        assert kwargs == {"bucket": bucket}
        return {
            "proposals": [
                {"bucket_id": "wrong", "reason": "Mismatch"},
                {
                    "bucket_id": "memory-1",
                    "anchor_kind": " Shared Commitment! ",
                    "reason": "Useful across future conversations.",
                    "future_use": "Recall when planning together.",
                    "confidence": -1,
                },
                {
                    "bucket_id": "memory-1",
                    "reason": "A second candidate",
                },
            ]
        }

    adapters = CurrentServiceAdapters(
        CurrentServiceDependencies(
            bucket_mgr=BucketManager([bucket]),
            anchor_proposal_model=model,
            config={"anchor": {"min_age_hours": 0}},
            clock=lambda: NOW,
            model_name="proposal-model",
        )
    )

    result = await adapters.anchor_proposals({"bucket_id": "memory-1"})

    assert isinstance(result, dict)
    assert result["model"] == "proposal-model"
    assert result["proposals"] == [
        {
            "bucket_id": "memory-1",
            "anchor_kind": "shared_commitment",
            "reason": "Useful across future conversations.",
            "future_use": "Recall when planning together.",
            "confidence": 0.0,
        }
    ]
    assert [item["reason"] for item in result["rejected"]] == [
        "bucket_id mismatch",
        "too many proposals",
    ]


@pytest.mark.asyncio
async def test_anchor_confirm_applies_gate_and_writes_normalized_proposal() -> None:
    candidate = {
        "id": "memory-1",
        "content": "A durable shared commitment",
        "metadata": {
            "name": "Commitment",
            "created": "2026-06-01T00:00:00+00:00",
        },
    }
    manager = BucketManager([candidate])
    writer_calls: list[dict] = []

    async def writer(**kwargs):
        writer_calls.append(kwargs)
        candidate["metadata"]["anchor"] = True
        return "已修改记忆桶 memory-1: anchor=True → 已标为 anchor"

    adapters = CurrentServiceAdapters(
        CurrentServiceDependencies(
            bucket_mgr=manager,
            anchor_writer=writer,
            config={"anchor": {"min_age_hours": 24, "max_count": 12}},
            clock=lambda: NOW,
        )
    )

    result = await adapters.anchor_confirm(
        {
            "bucket_id": "memory-1",
            "anchor_kind": " Commitment ",
            "reason": " Reused in future plans. ",
            "future_use": "Planning",
            "confidence": 0.88,
        }
    )

    assert isinstance(result, dict)
    assert result["status"] == "anchored"
    assert result["id"] == "memory-1"
    assert result["proposal"] == {
        "bucket_id": "memory-1",
        "anchor_kind": "commitment",
        "reason": "Reused in future plans.",
        "future_use": "Planning",
        "confidence": 0.88,
    }
    assert result["bucket"]["anchor"] is True
    assert writer_calls == [{"bucket_id": "memory-1", "anchor": 1}]


@pytest.mark.asyncio
async def test_anchor_confirm_rejects_young_bucket_before_writer() -> None:
    young = {
        "id": "memory-1",
        "content": "New memory",
        "metadata": {"created": "2026-07-17T11:00:00+00:00"},
    }
    adapters = CurrentServiceAdapters(
        CurrentServiceDependencies(
            bucket_mgr=BucketManager([young]),
            anchor_writer=pytest.fail,
            config={"anchor": {"min_age_hours": 24}},
            clock=lambda: NOW,
        )
    )

    result = await adapters.anchor_confirm({"bucket_id": "memory-1", "reason": "Potentially durable"})

    payload = _response_payload(result, 400)
    assert "至少等待 24 小时" in payload["error"]


@pytest.mark.asyncio
async def test_anchor_confirm_fails_closed_without_writer() -> None:
    bucket = {
        "id": "memory-1",
        "content": "Old memory",
        "metadata": {"created": "2026-06-01T00:00:00+00:00"},
    }
    adapters = CurrentServiceAdapters(
        CurrentServiceDependencies(
            bucket_mgr=BucketManager([bucket]),
            config={"anchor": {"min_age_hours": 0}},
            clock=lambda: NOW,
        )
    )

    result = await adapters.anchor_confirm({"bucket_id": "memory-1", "reason": "Durable evidence"})

    payload = _response_payload(result, 503)
    assert "anchor_writer" in payload["error"]


def test_build_current_services_binds_all_six_callbacks() -> None:
    dependencies = CurrentServiceDependencies()

    services = build_current_services(dependencies)

    assert callable(services.inspect_diffusion)
    assert callable(services.inspect_recall)
    assert callable(services.profile_fact_proposals)
    assert callable(services.profile_fact_confirm)
    assert callable(services.anchor_proposals)
    assert callable(services.anchor_confirm)
    assert isinstance(CurrentServiceAdapters(dependencies).as_services(), type(services))
