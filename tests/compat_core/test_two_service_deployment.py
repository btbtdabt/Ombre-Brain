from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _compose(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_entrypoint_selects_only_brain_or_gateway() -> None:
    script = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")

    assert 'case "${OMBRE_SERVICE:-brain}" in' in script
    assert 'SERVICE_ENTRY="src/server.py"' in script
    assert 'SERVICE_ENTRY="src/gateway.py"' in script
    assert "OMBRE_SERVICE must be 'brain' or 'gateway'" in script
    assert 'exec python "$SERVICE_ENTRY"' in script


def test_cloudflare_compose_runs_two_isolated_service_roles() -> None:
    services = _compose("compose.cloudflare.yml")["services"]
    brain = services["ombre-brain"]
    gateway = services["ombre-gateway"]

    assert brain["environment"]["OMBRE_SERVICE"] == "brain"
    assert brain["environment"]["OMBRE_CODE_DIR"] == "/state/_brain_app"
    assert gateway["environment"]["OMBRE_SERVICE"] == "gateway"
    assert gateway["environment"]["OMBRE_PERSIST_CODE"] == 0
    assert brain["environment"]["OMBRE_CONFIG_PATH"] == "/app/config.yaml"
    assert gateway["environment"]["OMBRE_CONFIG_PATH"] == "/app/config.yaml"


def test_vps_compose_runs_two_isolated_service_roles() -> None:
    services = _compose("compose.hk.yml")["services"]

    assert services["ombre-brain"]["environment"]["OMBRE_SERVICE"] == "brain"
    assert services["ombre-gateway"]["environment"]["OMBRE_SERVICE"] == "gateway"
    assert services["ombre-gateway"]["environment"]["OMBRE_PERSIST_CODE"] == 0
