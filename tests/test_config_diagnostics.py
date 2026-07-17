import json
import os

import yaml

from config_diagnostics import effective_config_report
from utils import load_config


def test_effective_config_report_identifies_sources_and_redacts_secrets(tmp_path):
    config_path = tmp_path / "config.yaml"
    runtime_path = tmp_path / "config.runtime.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "dehydration": {"model": "yaml-model", "api_key": "yaml-secret"},
                "embedding": {"model": "yaml-embed"},
            }
        ),
        encoding="utf-8",
    )
    runtime_path.write_text(
        yaml.safe_dump({"embedding": {"model": "runtime-embed"}}),
        encoding="utf-8",
    )
    effective = {
        "dehydration": {"model": "env-model", "api_key": "env-secret"},
        "embedding": {"model": "runtime-embed", "api_key": "embed-secret"},
        "gateway": {
            "upstreams": [
                {
                    "name": "claude",
                    "base_url": "https://example.invalid/v1",
                    "default_model": "claude-opus",
                    "api_key_env": "UPSTREAM_SECRET",
                }
            ]
        },
    }

    report = effective_config_report(
        effective,
        config_path=str(config_path),
        runtime_config_path=str(runtime_path),
        environ={
            "OMBRE_DEHYDRATION_MODEL": "env-model",
            "OMBRE_EMBED_API_KEY": "canonical-embed-key",
            "OMBRE_EMBEDDING_API_KEY": "legacy-embed-key",
            "UPSTREAM_SECRET": "gateway-secret",
        },
    )
    entries = {entry["path"]: entry for entry in report["entries"]}

    assert entries["dehydration.model"]["source"] == "env:OMBRE_DEHYDRATION_MODEL"
    assert entries["embedding.model"]["source"] == "runtime_yaml"
    assert entries["embedding.api_key"]["source"] == "env:OMBRE_EMBED_API_KEY"
    assert entries["dehydration.api_key"] == {
        "path": "dehydration.api_key",
        "source": "config_yaml",
        "sensitive": True,
        "set": True,
    }
    assert report["gateway_upstreams"][0]["api_key_set"] is True
    serialized = json.dumps(report)
    for secret in ("yaml-secret", "env-secret", "embed-secret", "gateway-secret"):
        assert secret not in serialized


def test_effective_config_report_preserves_legacy_embedding_env_provenance(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("OMBRE_EMBED_API_KEY", raising=False)
    monkeypatch.setenv("OMBRE_EMBEDDING_API_KEY", "legacy-only-key")
    effective = load_config(str(tmp_path / "missing-config.yaml"))

    report = effective_config_report(
        effective,
        config_path=str(tmp_path / "missing-config.yaml"),
        runtime_config_path=str(tmp_path / "missing-runtime.yaml"),
        environ=os.environ,
    )
    entries = {entry["path"]: entry for entry in report["entries"]}

    assert entries["embedding.api_key"]["source"] == (
        "env:OMBRE_EMBEDDING_API_KEY"
    )
