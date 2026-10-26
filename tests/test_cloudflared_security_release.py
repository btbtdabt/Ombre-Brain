"""Keep both Tunnel installation paths on the reviewed security release."""

import importlib.util
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "cloudflared_security_release", _REPO_ROOT / "deploy" / "fetch_cloudflared.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_FETCH = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_FETCH)

_SECURITY_VERSION = "2026.10.3"
# Published Linux artifact checksums from the reviewed Cloudflare release:
# https://github.com/cloudflare/cloudflared/releases/tag/2026.10.3
_OFFICIAL_SHA256 = {
    "386": "7c533230928eae6443d6d87dce16e0af67fa1f60fb56860dca4e09883a8ee394",
    "amd64": "053b80b0a23031a038fdbf3c073f59f82b8f1f152aa67971d7383ffafc0dbab0",
    "arm": "76f35faae119881ddf766b61baa9e27803aea6bea7111ed4170ed1b7a5c8637e",
    "arm64": "89297ebfb9e8c99c0dc013a1140601453cf3798d8306c5591651f4a3f5210be8",
}


def test_compose_tunnel_image_pins_reviewed_security_release() -> None:
    compose = yaml.safe_load(
        (_REPO_ROOT / "compose.cloudflare.yml").read_text(encoding="utf-8")
    )

    assert (
        compose["services"]["cloudflared"]["image"]
        == f"cloudflare/cloudflared:{_SECURITY_VERSION}"
    )


def test_compose_grace_period_is_scoped_to_tunnel() -> None:
    compose = yaml.safe_load(
        (_REPO_ROOT / "compose.cloudflare.yml").read_text(encoding="utf-8")
    )

    assert {
        name: service["stop_grace_period"]
        for name, service in compose["services"].items()
        if "stop_grace_period" in service
    } == {"cloudflared": "45s"}


def test_compose_security_upgrade_preserves_service_restart_policies() -> None:
    compose = yaml.safe_load(
        (_REPO_ROOT / "compose.cloudflare.yml").read_text(encoding="utf-8")
    )

    assert {
        name: service["restart"] for name, service in compose["services"].items()
    } == {
        "ombre-brain": "unless-stopped",
        "ombre-gateway": "unless-stopped",
        "cloudflared": "unless-stopped",
    }


@pytest.mark.parametrize("arch", _OFFICIAL_SHA256)
def test_bundled_tunnel_download_uses_reviewed_security_release(arch: str) -> None:
    assert _FETCH.release_url(arch) == (
        "https://github.com/cloudflare/cloudflared/releases/download/"
        f"{_SECURITY_VERSION}/cloudflared-linux-{arch}"
    )


@pytest.mark.parametrize("arch,digest", _OFFICIAL_SHA256.items())
def test_bundled_tunnel_hash_matches_official_release(arch: str, digest: str) -> None:
    assert _FETCH.expected_sha256(arch) == digest
