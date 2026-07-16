import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import yaml

import dehydrator as dehydrator_module
from dehydrator import Dehydrator
from embedding_engine import EmbeddingEngine
from utils import atomic_update_yaml


def _config(tmp_path, *, model="dehydrator-a"):
    buckets_dir = tmp_path / "buckets"
    buckets_dir.mkdir(exist_ok=True)
    return {
        "buckets_dir": str(buckets_dir),
        "dehydration": {
            "api_key": "",
            "model": model,
            "base_url": "https://dehydrator.example/v1",
        },
        "embedding": {
            "enabled": True,
            "api_key": "test-key",
            "model": "embed-model",
            "base_url": "https://embedding.example/v1",
            "query_cache_size": 2,
        },
    }


def test_dehydration_cache_is_scoped_by_runtime_identity(tmp_path, monkeypatch):
    first = Dehydrator(_config(tmp_path, model="dehydrator-a"))
    first._set_cached_summary("same content", "summary-a")

    assert first._get_cached_summary("same content") == "summary-a"

    second = Dehydrator(_config(tmp_path, model="dehydrator-b"))
    assert second._get_cached_summary("same content") is None

    monkeypatch.setattr(
        dehydrator_module,
        "DEHYDRATION_PROMPT_VERSION",
        dehydrator_module.DEHYDRATION_PROMPT_VERSION + 1,
    )
    assert first._get_cached_summary("same content") is None


def test_dehydration_cache_separates_summary_purposes(tmp_path):
    dehydrator = Dehydrator(_config(tmp_path))
    dehydrator._set_cached_summary("same content", "normal", purpose="dehydrate")
    dehydrator._set_cached_summary("same content", "capsule", purpose="direct_capsule")

    assert dehydrator._get_cached_summary("same content", purpose="dehydrate") == "normal"
    assert dehydrator._get_cached_summary("same content", purpose="direct_capsule") == "capsule"


def test_dehydration_prompts_preserve_subject_attribution():
    for prompt in (
        dehydrator_module.DEHYDRATE_PROMPT,
        dehydrator_module.DIRECT_BUCKET_CAPSULE_PROMPT,
        dehydrator_module.DIGEST_PROMPT_TEMPLATE,
        dehydrator_module.MERGE_PROMPT_TEMPLATE,
    ):
        assert "动作、感受、承诺和原话必须归属于原文中的主体" in prompt
        assert "不要交换双方视角" in prompt


class _FakeEmbeddings:
    def __init__(self):
        self.calls = []

    async def create(self, *, model, input):
        self.calls.append((model, input))
        value = float(len(self.calls))
        return SimpleNamespace(data=[SimpleNamespace(embedding=[value, value + 0.5])])


def test_embedding_query_lru_reuses_results_and_returns_copies(tmp_path):
    engine = EmbeddingEngine(_config(tmp_path))
    fake = _FakeEmbeddings()
    engine.client = SimpleNamespace(embeddings=fake)

    first = asyncio.run(engine._generate_embedding("seafood", kind="query"))
    first.append(999.0)
    second = asyncio.run(engine._generate_embedding("seafood", kind="query"))

    assert len(fake.calls) == 1
    assert second == [1.0, 1.5]

    asyncio.run(engine._generate_embedding("seafood", kind="document"))
    assert len(fake.calls) == 2


def test_embedding_query_lru_evicts_oldest_entry(tmp_path):
    engine = EmbeddingEngine(_config(tmp_path))
    fake = _FakeEmbeddings()
    engine.client = SimpleNamespace(embeddings=fake)

    asyncio.run(engine._generate_embedding("one", kind="query"))
    asyncio.run(engine._generate_embedding("two", kind="query"))
    asyncio.run(engine._generate_embedding("three", kind="query"))
    asyncio.run(engine._generate_embedding("one", kind="query"))

    assert len(fake.calls) == 4


def test_atomic_update_yaml_serializes_concurrent_read_modify_write(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("count: 0\nkept: true\n", encoding="utf-8")

    def increment(_):
        def mutate(config):
            config["count"] = int(config.get("count", 0)) + 1

        atomic_update_yaml(path, mutate)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(increment, range(40)))

    persisted = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert persisted == {"count": 40, "kept": True}


def test_atomic_update_yaml_leaves_original_when_mutation_fails(tmp_path):
    path = tmp_path / "config.yaml"
    original = "answer: 42\n"
    path.write_text(original, encoding="utf-8")

    def fail(config):
        config["answer"] = 0
        raise RuntimeError("mutation failed")

    try:
        atomic_update_yaml(path, fail)
    except RuntimeError as exc:
        assert str(exc) == "mutation failed"
    else:
        raise AssertionError("atomic_update_yaml should propagate mutation failures")

    assert path.read_text(encoding="utf-8") == original
