from pathlib import Path

import yaml


def test_example_config_contains_both_runtime_contracts() -> None:
    config = yaml.safe_load(
        Path("config.example.yaml").read_text(encoding="utf-8")
    )

    p0_sections = {
        "deployment",
        "hooks",
        "limits",
        "storage",
        "surfacing",
        "bucket_type_defaults",
    }
    production_sections = {
        "gateway",
        "raw_events",
        "memory_diffusion",
        "memory_relevance",
        "memory_write_gate",
        "identity_semantics",
        "persona",
        "reflection",
        "portrait",
        "dream",
        "reranker",
    }

    assert p0_sections <= config.keys()
    assert production_sections <= config.keys()
    assert config["dehydration"]["thinking_mode"] == ""
    assert config["embedding"]["max_chars"] > 0
    assert config["embedding"]["query_timeout_seconds"] > 0
    assert config["embedding"]["query_instruction"]
