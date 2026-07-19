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


def test_effective_config_report_tracks_gateway_multi_env_and_direct_key_provenance(
    tmp_path,
):
    report = effective_config_report(
        {
            "gateway": {
                "upstreams": [
                    {
                        "name": "provider",
                        "base_url": "https://models.example/v1",
                        "default_model": "model-a",
                        "api_key_env": "UPSTREAM_KEY_LEGACY",
                        "api_key_envs": [
                            "UPSTREAM_KEY_PRIMARY",
                            "UPSTREAM_KEY_SECONDARY",
                        ],
                        "api_key": "direct-primary-secret",
                        "api_keys": [
                            "direct-secondary-secret",
                            "",
                            "direct-tertiary-secret",
                        ],
                    }
                ]
            }
        },
        config_path=str(tmp_path / "missing-config.yaml"),
        runtime_config_path=str(tmp_path / "missing-runtime.yaml"),
        environ={
            "UPSTREAM_KEY_LEGACY": "legacy-secret",
            "UPSTREAM_KEY_PRIMARY": "primary-secret",
            "UPSTREAM_KEY_SECONDARY": "",
        },
    )

    assert report["gateway_upstreams"] == [
        {
            "name": "provider",
            "base_url": "https://models.example/v1",
            "default_model": "model-a",
            "models": [],
            "api_key_env": "UPSTREAM_KEY_LEGACY",
            "api_key_envs": ["UPSTREAM_KEY_PRIMARY", "UPSTREAM_KEY_SECONDARY"],
            "api_key_set": True,
            "has_direct_api_key": True,
            "direct_api_key_count": 3,
            "key_count": 4,
        }
    ]
    serialized = json.dumps(report)
    for secret in (
        "legacy-secret",
        "primary-secret",
        "direct-primary-secret",
        "direct-secondary-secret",
        "direct-tertiary-secret",
    ):
        assert secret not in serialized
    assert "api_key" not in report["gateway_upstreams"][0]
    assert "api_keys" not in report["gateway_upstreams"][0]


def test_effective_config_report_counts_each_gateway_key_source_independently(
    tmp_path,
):
    report = effective_config_report(
        {
            "gateway": {
                "upstreams": [
                    {
                        "name": "plural-env",
                        "api_key_envs": ["PLURAL_KEY", "EMPTY_PLURAL_KEY"],
                    },
                    {"name": "legacy-env", "api_key_env": "LEGACY_KEY"},
                    {"name": "direct-key", "api_key": "direct-only-secret"},
                    {
                        "name": "direct-keys",
                        "api_keys": ["list-secret-one", "", "list-secret-two"],
                    },
                    {
                        "name": "missing",
                        "api_key_envs": ["MISSING_KEY"],
                        "api_keys": [],
                    },
                    {
                        "name": "plural-overrides-legacy",
                        "api_key_env": "LEGACY_KEY",
                        "api_key_envs": ["EMPTY_PLURAL_KEY"],
                    },
                ]
            }
        },
        config_path=str(tmp_path / "missing-config.yaml"),
        runtime_config_path=str(tmp_path / "missing-runtime.yaml"),
        environ={
            "PLURAL_KEY": "plural-only-secret",
            "EMPTY_PLURAL_KEY": "",
            "LEGACY_KEY": "legacy-only-secret",
        },
    )

    summaries = {
        upstream["name"]: (
            upstream["api_key_set"],
            upstream["key_count"],
            upstream["direct_api_key_count"],
        )
        for upstream in report["gateway_upstreams"]
    }
    assert summaries == {
        "plural-env": (True, 1, 0),
        "legacy-env": (True, 1, 0),
        "direct-key": (True, 1, 1),
        "direct-keys": (True, 2, 2),
        "missing": (False, 0, 0),
        "plural-overrides-legacy": (False, 0, 0),
    }
    serialized = json.dumps(report)
    for secret in (
        "plural-only-secret",
        "legacy-only-secret",
        "direct-only-secret",
        "list-secret-one",
        "list-secret-two",
    ):
        assert secret not in serialized
