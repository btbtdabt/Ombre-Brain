from __future__ import annotations

import logging
from pathlib import Path

import pytest

from bucket_manager import BucketManager
from darkroom import DarkroomStore
from entity_edges import EntityEdgeStore
from letter_service import LetterService
from memory_edges import MemoryEdgeStore
from memory_moments import MemoryMomentStore
from reminder_store import ReminderStore
from tools import _runtime as runtime


class FakeEmbeddingEngine:
    enabled = False

    async def generate_and_store(self, bucket_id: str, content: str) -> bool:
        return True

    async def get_embedding(self, bucket_id: str):
        return None

    async def search_similar(self, query: str, top_k: int = 10):
        return []

    def delete_embedding(self, bucket_id: str) -> None:
        return None


class FakeDehydrator:
    async def analyze(self, content: str) -> dict:
        return {
            "domain": ["life"],
            "valence": 0.6,
            "arousal": 0.4,
            "importance": 6,
            "tags": ["autotag"],
            "suggested_name": "自动标题",
        }

    async def digest(self, content: str) -> list[dict]:
        return [
            {
                "content": part.strip(),
                "domain": ["life"],
                "valence": 0.5,
                "arousal": 0.3,
                "importance": 5,
                "tags": ["digest"],
                "name": f"片段 {index}",
            }
            for index, part in enumerate(content.split("||"), start=1)
            if part.strip()
        ]

    async def dehydrate(self, content: str, metadata: dict) -> str:
        return str(content).strip()[:240]

    async def dehydrate_direct_capsule(self, content: str, metadata: dict) -> str:
        name = str(metadata.get("name") or "memory")
        return f"capsule:{name}:{str(content).strip()[:80]}"

    def invalidate_cache(self, content: str) -> None:
        return None


class FakeDecayEngine:
    is_running = True

    async def ensure_started(self) -> None:
        return None

    def calculate_score(self, metadata: dict) -> float:
        return float(metadata.get("importance", 5))


@pytest.fixture
def current_runtime(tmp_path: Path):
    buckets_dir = tmp_path / "buckets"
    state_dir = tmp_path / "state"
    for child in ("permanent", "dynamic", "archive", "feel", "letters"):
        (buckets_dir / child).mkdir(parents=True, exist_ok=True)

    config = {
        "buckets_dir": str(buckets_dir),
        "state_dir": str(state_dir),
        "merge_threshold": 75,
        "matching": {"fuzzy_threshold": 50, "max_results": 10},
        "wikilink": {"enabled": False},
        "identity": {
            "ai_name": "Haven",
            "user_name": "Amy",
            "user_display_name": "Amy",
            "user_aliases": ["她"],
        },
        "scoring_weights": {
            "topic_relevance": 4.0,
            "emotion_resonance": 2.0,
            "time_proximity": 1.5,
            "importance": 1.0,
            "content_weight": 1.0,
        },
        "decay": {
            "lambda": 0.05,
            "threshold": 0.3,
            "check_interval_hours": 24,
            "emotion_weights": {"base": 1.0, "arousal_boost": 0.8},
        },
        "embedding": {"enabled": False},
    }
    embedding = FakeEmbeddingEngine()
    bucket_mgr = BucketManager(config, embedding_engine=embedding)
    managers = {
        "config": config,
        "bucket_mgr": bucket_mgr,
        "dehydrator": FakeDehydrator(),
        "decay_engine": FakeDecayEngine(),
        "embedding_engine": embedding,
        "logger": logging.getLogger("compat_tools"),
        "reminder_store": ReminderStore(config),
        "letter_service": LetterService(config, bucket_mgr, embedding),
        "darkroom_store": DarkroomStore(config),
        "memory_edge_store": MemoryEdgeStore(config),
        "memory_moment_store": MemoryMomentStore(config),
        "entity_edge_store": EntityEdgeStore(config),
    }
    previous = {name: getattr(runtime, name, None) for name in managers}
    runtime.init(**managers)
    yield managers
    runtime.init(
        **{
            name: previous[name]
            for name, injected in managers.items()
            if getattr(runtime, name, None) is injected
        }
    )
