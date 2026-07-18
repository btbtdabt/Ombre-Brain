from pathlib import Path

from ombrebrain.maintenance import report as maintenance_report
from web import system


ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, content: str = "asset\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_diagnostics_fall_back_to_immutable_image_assets(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    image_root = tmp_path / "image"
    required = (
        "tools/vnext_preflight.py",
        "src/web/system.py",
    )
    for relative in required:
        _write(image_root / relative)
    monkeypatch.setenv("OMBRE_IMAGE_ROOT", str(image_root))

    assert system._diagnostic_asset_root(str(runtime_root), *required) == str(image_root)
    assert maintenance_report._repository_asset_root(
        *required,
        runtime_root=runtime_root,
    ) == image_root


def test_diagnostics_prefer_complete_live_runtime(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    image_root = tmp_path / "image"
    required = ("tools/vnext_preflight.py", "src/web/system.py")
    for relative in required:
        _write(runtime_root / relative)
        _write(image_root / relative)
    monkeypatch.setenv("OMBRE_IMAGE_ROOT", str(image_root))

    assert system._diagnostic_asset_root(str(runtime_root), *required) == str(runtime_root)
    assert maintenance_report._repository_asset_root(
        *required,
        runtime_root=runtime_root,
    ) == runtime_root


def test_adr_diagnostics_ignore_empty_live_directory(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    image_root = tmp_path / "image"
    (runtime_root / "docs" / "adr").mkdir(parents=True)
    _write(image_root / "docs" / "adr" / "ADR-0001-image.md")
    monkeypatch.setenv("OMBRE_IMAGE_ROOT", str(image_root))

    assert system._diagnostic_adr_root(str(runtime_root)) == str(image_root)


def test_container_build_packages_only_required_diagnostics_assets():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    for path in (
        "tools/vnext_preflight.py",
        "tools/clean_orphan_embeddings.py",
        "kernel/rust/ombre-kernel/Cargo.toml",
        "kernel/rust/ombre-kernel/src/lib.rs",
        "docs/adr/",
    ):
        assert path in dockerfile
        assert f"!{path}" in dockerignore


def test_fork_docker_workflow_builds_without_registry_credentials():
    workflow = (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )

    assert "Build without publishing" in workflow
    assert "steps.registry.outputs.configured != 'true'" in workflow
    assert "steps.registry.outputs.configured == 'true'" in workflow
    assert "p0luz/ombre-brain" not in workflow
