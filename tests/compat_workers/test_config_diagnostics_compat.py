import json

import yaml

from config_diagnostics import effective_config_report


def test_effective_config_report_tracks_sources_without_disclosing_secrets(tmp_path):
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
            "UPSTREAM_SECRET": "gateway-secret",
        },
    )
    entries = {entry["path"]: entry for entry in report["entries"]}

    assert entries["dehydration.model"]["source"] == "env:OMBRE_DEHYDRATION_MODEL"
    assert entries["embedding.model"]["source"] == "runtime_yaml"
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


def test_effective_config_report_tracks_gateway_multi_env_and_direct_keys(tmp_path):
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
