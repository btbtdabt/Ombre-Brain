import asyncio
from decimal import Decimal
from pathlib import Path

import frontmatter

from bucket_manager import BucketManager
from dehydrator import Dehydrator
from dream_engine import _clamp
from memory_nodes import _facet_keywords_for_config
from portrait_engine import DailyPortraitMaintainer


def _config(tmp_path: Path) -> dict:
    return {
        "buckets_dir": str(tmp_path / "buckets"),
        "state_dir": str(tmp_path / "state"),
        "memory": {"max_results": 5},
    }


def test_move_bucket_preserves_scalar_domain_name(tmp_path: Path) -> None:
    manager = BucketManager(_config(tmp_path))
    for index, (domain, expected) in enumerate(
        (("relationship", "relationship"), (("work", "life"), "work"))
    ):
        source = tmp_path / f"source-{index}.md"
        source.write_text("memory", encoding="utf-8")

        destination = Path(manager._move_bucket(str(source), str(tmp_path / "permanent"), domain))

        assert destination.parent.name == expected
        assert destination.read_text(encoding="utf-8") == "memory"


def test_missing_display_name_does_not_create_none_facet_keyword() -> None:
    keywords = _facet_keywords_for_config({"identity": {"user_aliases": []}})

    assert "None" not in keywords["relation.intimacy"]
    assert "None" not in keywords["topic.love"]


def test_dream_clamp_accepts_float_convertible_values() -> None:
    assert _clamp(Decimal("0.7")) == 0.7


def test_string_activation_counts_remain_mutable(tmp_path: Path) -> None:
    manager = BucketManager(_config(tmp_path))
    source_id = asyncio.run(manager.create(content="source"))
    target_id = asyncio.run(manager.create(content="target"))

    def set_count(bucket_id: str, value: str) -> None:
        bucket_path = manager._find_bucket_file(bucket_id)
        assert bucket_path is not None
        post = frontmatter.load(bucket_path)
        post["activation_count"] = value
        Path(bucket_path).write_text(frontmatter.dumps(post), encoding="utf-8")

    set_count(target_id, "3")
    assert asyncio.run(manager.add_comment(target_id, "comment")) is not None
    target = asyncio.run(manager.get(target_id))
    assert target is not None
    assert target["metadata"]["activation_count"] == 4.0

    set_count(target_id, "2.5")
    asyncio.run(manager.touch(target_id, ripple=False))
    target = asyncio.run(manager.get(target_id))
    assert target is not None
    assert target["metadata"]["activation_count"] == 3.5

    set_count(target_id, "1.5")
    reference_time = manager._parse_iso_datetime(target["metadata"]["created"])
    assert reference_time is not None
    asyncio.run(manager._time_ripple(source_id, reference_time))
    target = asyncio.run(manager.get(target_id))
    assert target is not None
    assert target["metadata"]["activation_count"] == 1.8


def test_optional_llm_clients_fail_with_explicit_runtime_errors(tmp_path: Path) -> None:
    components = (Dehydrator(_config(tmp_path)), DailyPortraitMaintainer(_config(tmp_path)))

    for component in components:
        try:
            component._require_client()
        except RuntimeError:
            continue
        raise AssertionError(f"{type(component).__name__} accepted a missing LLM client")
