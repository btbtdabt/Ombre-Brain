from __future__ import annotations

from copy import deepcopy

import pytest

from tests.compat_workers.support import LegacyBucketManager


@pytest.fixture
def worker_config(tmp_path):
    buckets_dir = tmp_path / "buckets"
    state_dir = tmp_path / "state"
    for name in ("permanent", "dynamic", "archive", "feel", "letters"):
        (buckets_dir / name).mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    return {
        "buckets_dir": str(buckets_dir),
        "state_dir": str(state_dir),
        "matching": {"fuzzy_threshold": 50, "max_results": 10},
        "wikilink": {"enabled": False},
        "scoring_weights": {},
        "dehydration": {
            "api_key": "",
            "base_url": "https://example.invalid/v1",
            "model": "deepseek-chat",
        },
        "embedding": {"enabled": False, "api_key": ""},
        "identity": {"ai_name": "Ombre", "user_name": "Amy"},
        "persona": {
            "enabled": True,
            "profile_id": "haven_xiaoyu",
            "mode": "llm",
            "base_url": "https://example.invalid/v1",
            "model": "deepseek-chat",
            "api_key": "",
            "temperature": 0.1,
            "max_tokens": 500,
            "global_decay_hours": 168,
            "session_mood_half_life_minutes": 90,
            "max_personality_delta": 0.01,
            "max_relationship_delta": 0.03,
            "max_affect_delta": 0.18,
            "initial_personality": {
                "openness": 0.56,
                "conscientiousness": 0.50,
                "extraversion": 0.44,
                "agreeableness": 0.66,
                "neuroticism": 0.36,
            },
            "initial_relationship": {
                "affinity": 0.86,
                "dominance": 0.38,
                "defensiveness": 0.12,
                "trust": 0.82,
            },
            "initial_affect": {
                "valence": 0.56,
                "arousal": 0.34,
                "tenderness": 0.62,
                "possessiveness": 0.24,
                "longing": 0.34,
                "security": 0.68,
                "protective_drive": 0.52,
                "mood_label": "warm_neutral",
                "session_defensiveness": 0.12,
                "residue": "",
            },
        },
    }


@pytest.fixture
def test_config(worker_config):
    return deepcopy(worker_config)


@pytest.fixture
def bucket_mgr(worker_config):
    return LegacyBucketManager(worker_config)
