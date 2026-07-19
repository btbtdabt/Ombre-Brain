from __future__ import annotations

import os
import errno
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pytest
import yaml
from dotenv import dotenv_values

from web import _shared as sh
from web import config_api


ROOT = Path(__file__).resolve().parents[1]


def test_project_env_path_honors_an_absolute_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    mounted_env = tmp_path / "mounted" / ".env"
    mounted_env.parent.mkdir()
    monkeypatch.setattr(sh, "repo_root", str(repo))
    monkeypatch.setenv("OMBRE_ENV_PATH", str(mounted_env))

    assert sh._project_env_path() == str(mounted_env.resolve())


def test_project_env_path_falls_back_to_the_injected_repository_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(sh, "repo_root", str(repo))
    monkeypatch.delenv("OMBRE_ENV_PATH", raising=False)

    assert sh._project_env_path() == str((repo / ".env").resolve())


def test_project_env_path_rejects_relative_or_symlink_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OMBRE_ENV_PATH", "relative/.env")
    with pytest.raises(ValueError, match="absolute"):
        sh._project_env_path()

    target = tmp_path / "real.env"
    target.touch()
    link = tmp_path / "linked.env"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    monkeypatch.setenv("OMBRE_ENV_PATH", str(link))
    with pytest.raises(ValueError, match="symlink"):
        sh._project_env_path()


def test_atomic_env_update_falls_back_for_a_writable_bind_file_mount(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "PROVIDER_API_KEY='old-first'\n"
        "UNCHANGED='value'\n"
        "PROVIDER_API_KEY='stale-last'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OMBRE_ENV_PATH", str(env_path))
    monkeypatch.setattr(config_api.sh, "in_docker", lambda: True)

    def reject_mountpoint_replace(_source: str, _target: str) -> None:
        raise OSError(errno.EBUSY, "bind mount cannot be replaced")

    monkeypatch.setattr(sh.os, "replace", reject_mountpoint_replace)

    config_api._atomic_update_env_vars(
        {"PROVIDER_API_KEY": "new$secret'with\\slashes"}
    )

    values = dotenv_values(env_path, interpolate=False)
    assert values["UNCHANGED"] == "value"
    assert values["PROVIDER_API_KEY"] == "new$secret'with\\slashes"
    assert env_path.read_text(encoding="utf-8").count("PROVIDER_API_KEY=") == 1
    assert list(tmp_path.glob(".env.*.tmp")) == []


def test_env_persistence_accepts_an_existing_writable_bind_file_with_read_only_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.touch()
    parent = str(tmp_path)
    monkeypatch.setenv("OMBRE_ENV_PATH", str(env_path))
    monkeypatch.setattr(sh, "in_docker", lambda: True)
    real_access = sh.os.access

    def simulated_access(path: Any, mode: int) -> bool:
        normalized = os.fspath(path)
        if normalized == str(env_path) and mode == os.W_OK:
            return True
        if normalized == parent and mode == os.W_OK:
            return False
        return real_access(path, mode)

    monkeypatch.setattr(sh.os, "access", simulated_access)

    assert sh._env_persistence_issue() == ""


def test_shared_env_writer_handles_bind_mounts_and_preserves_unrelated_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# keep this comment\n"
        "UNCHANGED='value'\n"
        "PROVIDER_API_KEY='old'\n"
        "PROVIDER_API_KEY='stale-duplicate'\n",
        encoding="utf-8",
    )
    secret = r'''new$secret 'quoted' \\path ${TOKEN} `cmd` # tail'''
    monkeypatch.setenv("OMBRE_ENV_PATH", str(env_path))
    monkeypatch.setattr(sh, "in_docker", lambda: True)

    def reject_mountpoint_replace(_source: str, _target: str) -> None:
        raise OSError(errno.EBUSY, "bind mount cannot be replaced")

    monkeypatch.setattr(sh.os, "replace", reject_mountpoint_replace)

    sh._write_env_var("PROVIDER_API_KEY", secret)

    serialized = env_path.read_text(encoding="utf-8")
    assert serialized.startswith("# keep this comment\nUNCHANGED='value'\n")
    assert serialized.count("PROVIDER_API_KEY=") == 1
    assert dotenv_values(env_path, interpolate=False)["PROVIDER_API_KEY"] == secret
    assert list(tmp_path.glob(".env.*.tmp")) == []


def test_shared_and_config_env_writers_use_one_lock_without_lost_updates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("# shared\n", encoding="utf-8")
    monkeypatch.setenv("OMBRE_ENV_PATH", str(env_path))
    monkeypatch.setattr(sh, "in_docker", lambda: True)

    updates = {f"PROVIDER_{index}_API_KEY": f"secret-{index}" for index in range(12)}

    def write_item(item: tuple[str, str]) -> None:
        key, value = item
        if int(key.split("_")[1]) % 2:
            config_api._atomic_update_env_vars({key: value})
        else:
            sh._write_env_var(key, value)

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(write_item, updates.items()))

    parsed = dotenv_values(env_path, interpolate=False)
    assert {key: parsed[key] for key in updates} == updates
    assert env_path.read_text(encoding="utf-8").startswith("# shared\n")


def test_native_env_serialization_round_trips_through_real_bash(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")
    available = subprocess.run(
        [bash, "--version"], capture_output=True, text=True, check=False
    )
    if available.returncode != 0:
        pytest.skip("a runnable POSIX bash is unavailable")

    secret = r'''prefix\\path\"double" and 'single' $TOKEN ${TOKEN} `cmd` # tail'''
    env_path = tmp_path / ".env"
    env_path.write_text(
        "PROVIDER_API_KEY="
        + config_api._serialize_env_value(secret, shell_source=True)
        + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            bash,
            "-c",
            'set -a; . "$1"; printf "%s" "$PROVIDER_API_KEY"',
            "bash",
            str(env_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == secret


def test_generated_powershell_launcher_decodes_native_shell_quoted_env_values(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        windows_pwsh = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
        powershell = str(windows_pwsh) if windows_pwsh.is_file() else None
    if powershell is None:
        pytest.skip("PowerShell is unavailable")

    secret = r'''prefix\\path\"double" and 'single' $TOKEN ${TOKEN} `cmd` # tail'''
    env_path = tmp_path / ".env"
    env_path.write_text(
        "PROVIDER_API_KEY="
        + config_api._serialize_env_value(secret, shell_source=True)
        + "\n",
        encoding="utf-8",
    )

    one_click = (
        Path(__file__).resolve().parents[1] / "scripts" / "one_click.sh"
    ).read_text(encoding="utf-8")
    generated_start = one_click.index("  cat > start_local.ps1 <<'EOF'")
    loader_start = one_click.index('if (Test-Path ".env") {', generated_start)
    loader_end = one_click.index('$env:OMBRE_TRANSPORT = "streamable-http"', loader_start)
    loader = one_click[loader_start:loader_end]
    assert "$splice" in loader
    assert ".Replace($splice" in loader

    probe = tmp_path / "probe.ps1"
    probe.write_text(
        '$ErrorActionPreference = "Stop"\n'
        + loader
        + '[Console]::Out.Write($env:PROVIDER_API_KEY)\n',
        encoding="utf-8",
    )
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-File", str(probe)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == secret


@pytest.mark.parametrize("launcher", ["bash", "powershell"])
def test_docker_written_managed_secret_round_trips_after_native_switch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    launcher: str,
) -> None:
    executable = (
        shutil.which("bash")
        if launcher == "bash"
        else shutil.which("pwsh") or shutil.which("powershell")
    )
    if launcher == "powershell" and executable is None:
        windows_pwsh = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
        executable = str(windows_pwsh) if windows_pwsh.is_file() else None
    if executable is None:
        pytest.skip(f"{launcher} is unavailable")
    if launcher == "bash":
        available = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if available.returncode != 0:
            pytest.skip("a runnable POSIX bash is unavailable")

    managed_env = tmp_path / "state" / ".ombre-managed.env"
    managed_env.parent.mkdir()
    secret = r'''docker 'quoted' \\path $TOKEN ${TOKEN} `cmd`'''
    monkeypatch.setenv("OMBRE_ENV_PATH", str(managed_env))
    monkeypatch.setattr(sh, "in_docker", lambda: True)
    sh._atomic_update_env_vars({"OMBRE_COMPRESS_API_KEY": secret})

    process_env = os.environ.copy()
    process_env.pop("OMBRE_COMPRESS_API_KEY", None)
    process_env.update(
        {
            "OMBRE_ENV_PATH": str(managed_env),
            "OMBRE_MANAGED_ENV_OVERRIDE": "1",
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHON_EXE": str(Path(sys.executable)),
        }
    )
    python_probe = tmp_path / "managed_env_probe.py"
    python_probe.write_text(
        "import os, utils\n"
        "print(os.environ.get('OMBRE_COMPRESS_API_KEY', ''), end='')\n",
        encoding="utf-8",
    )
    if launcher == "bash":
        completed = subprocess.run(
            [
                executable,
                "-c",
                '"$PYTHON_EXE" "$1"',
                "bash",
                str(python_probe),
            ],
            env=process_env,
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        powershell_probe = tmp_path / "managed-env-probe.ps1"
        powershell_probe.write_text(
            '& $env:PYTHON_EXE "'
            + str(python_probe).replace('"', '`"')
            + '"\nexit $LASTEXITCODE\n',
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                executable,
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(powershell_probe),
            ],
            env=process_env,
            capture_output=True,
            text=True,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == secret


def test_managed_env_source_loads_before_config_with_literal_override_semantics(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    secret = r'''new$secret 'quoted' \\path ${TOKEN} `cmd` # tail'''
    env_path.write_text(
        "OMBRE_COMPRESS_API_KEY="
        + config_api._serialize_env_value(secret, shell_source=False)
        + "\nUNRELATED_SETTING='must-not-load'\n"
        + "OMBRE_CONFIG_PATH='/attacker/config.yaml'\n"
        + "OMBRE_SERVICE='attacker-service'\n"
        + "OMBRE_GATEWAY_TOKEN='attacker-token'\n"
        + "OMBRE_TRANSPORT='sse'\n"
        + "OMBRE_HOST_VAULT_DIR='/managed/vault'\n",
        encoding="utf-8",
    )
    process_env = os.environ.copy()
    process_env.update(
        {
            "OMBRE_ENV_PATH": str(env_path),
            "OMBRE_MANAGED_ENV_OVERRIDE": "1",
            "OMBRE_COMPRESS_API_KEY": "stale-container-value",
            "OMBRE_CONFIG_PATH": "/app/config.yaml",
            "OMBRE_SERVICE": "brain",
            "OMBRE_GATEWAY_TOKEN": "operator-token",
            "OMBRE_TRANSPORT": "streamable-http",
            "OMBRE_HOST_VAULT_DIR": "/old/vault",
            "PYTHONPATH": str(ROOT / "src"),
        }
    )
    completed = subprocess.run(
        [
            str(Path(sys.executable)),
            "-c",
            (
                "import json, os, utils; "
                "print(json.dumps({"
                "'value': os.environ.get('OMBRE_COMPRESS_API_KEY'), "
                "'unrelated': os.environ.get('UNRELATED_SETTING'), "
                "'config_path': os.environ.get('OMBRE_CONFIG_PATH'), "
                "'service': os.environ.get('OMBRE_SERVICE'), "
                "'gateway_token': os.environ.get('OMBRE_GATEWAY_TOKEN'), "
                "'transport': os.environ.get('OMBRE_TRANSPORT'), "
                "'host_vault': os.environ.get('OMBRE_HOST_VAULT_DIR'), "
                "'external': 'OMBRE_COMPRESS_API_KEY' in utils.BOOT_ENV_CONFIG"
                "}))"
            ),
        ],
        cwd=ROOT,
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "value": secret,
        "unrelated": None,
        "config_path": "/app/config.yaml",
        "service": "brain",
        "gateway_token": "operator-token",
        "transport": "sse",
        "host_vault": "/managed/vault",
        "external": False,
    }


def test_managed_env_source_does_not_override_operator_env_without_opt_in(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("OMBRE_COMPRESS_API_KEY='stale-file-value'\n", encoding="utf-8")
    process_env = os.environ.copy()
    process_env.pop("OMBRE_MANAGED_ENV_OVERRIDE", None)
    process_env.update(
        {
            "OMBRE_ENV_PATH": str(env_path),
            "OMBRE_COMPRESS_API_KEY": "rotated-operator-value",
            "PYTHONPATH": str(ROOT / "src"),
        }
    )
    completed = subprocess.run(
        [
            str(Path(sys.executable)),
            "-c",
            (
                "import json, os, utils; "
                "print(json.dumps({"
                "'value': os.environ.get('OMBRE_COMPRESS_API_KEY'), "
                "'external': 'OMBRE_COMPRESS_API_KEY' in utils.BOOT_ENV_CONFIG"
                "}))"
            ),
        ],
        cwd=ROOT,
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "value": "rotated-operator-value",
        "external": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("canonical_name", "legacy_name", "section"),
    [
        ("OMBRE_COMPRESS_API_KEY", "OMBRE_API_KEY", "dehydration"),
        ("OMBRE_EMBED_API_KEY", "OMBRE_EMBEDDING_API_KEY", "embedding"),
    ],
)
async def test_managed_empty_provider_key_is_a_restart_tombstone_for_yaml_and_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    canonical_name: str,
    legacy_name: str,
    section: str,
) -> None:
    env_path = tmp_path / ".env"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "buckets_dir": str(tmp_path / "vault"),
                "state_dir": str(tmp_path / "state"),
                section: {"api_key": "stale-yaml-secret"},
            }
        ),
        encoding="utf-8",
    )

    runtime_config = {
        section: {
            "api_key": "live-secret",
            "api_format": "openai_compat",
            "base_url": "https://provider.example/v1",
            "model": "provider-model",
            "timeout_seconds": 60,
            "enabled": True,
        }
    }
    monkeypatch.setenv(canonical_name, "live-secret")
    monkeypatch.setenv(legacy_name, "stale-legacy-secret")
    monkeypatch.setattr(config_api.sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(config_api.sh, "_project_env_path", lambda: str(env_path))
    monkeypatch.setattr(config_api.sh, "config", runtime_config)
    if section == "dehydration":
        dehydrator = type("DehydratorProbe", (), {})()
        for name, value in {
            "api_key": "live-secret",
            "api_format": "openai_compat",
            "base_url": "https://provider.example/v1",
            "model": "provider-model",
            "timeout_seconds": 60.0,
            "api_available": True,
            "client": object(),
        }.items():
            setattr(dehydrator, name, value)
        monkeypatch.setattr(config_api.sh, "dehydrator", dehydrator)
    else:
        embedding_engine = type("EmbeddingProbe", (), {})()
        setattr(embedding_engine, "_backend", object())
        setattr(embedding_engine, "enabled", True)
        monkeypatch.setattr(config_api.sh, "embedding_engine", embedding_engine)
        monkeypatch.setattr(
            config_api.sh,
            "replace_embedding_engine",
            lambda _engine: None,
        )

    class MCP:
        def __init__(self) -> None:
            self.routes: dict[tuple[str, str], Any] = {}

        def custom_route(self, path: str, methods: list[str]):
            def decorator(handler):
                for method in methods:
                    self.routes[(method, path)] = handler
                return handler

            return decorator

    class Request:
        async def json(self) -> dict[str, dict[str, str]]:
            return {"updates": {canonical_name: ""}}

    mcp = MCP()
    config_api.register(mcp)
    response = await mcp.routes[("POST", "/api/env-config")](Request())

    assert response.status_code == 200
    assert canonical_name not in os.environ
    assert runtime_config[section]["api_key"] == ""
    assert dotenv_values(env_path, interpolate=False)[canonical_name] == ""

    process_env = os.environ.copy()
    for name in (
        canonical_name,
        legacy_name,
        "OMBRE_BUCKETS_DIR",
        "OMBRE_VAULT_DIR",
        "OMBRE_STATE_DIR",
        "OMBRE_RUNTIME_CONFIG_PATH",
    ):
        process_env.pop(name, None)
    process_env.update(
        {
            "OMBRE_ENV_PATH": str(env_path),
            "OMBRE_MANAGED_ENV_OVERRIDE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(ROOT / "src"),
            "TEST_CONFIG_PATH": str(config_path),
            legacy_name: "stale-legacy-secret",
        }
    )
    completed = subprocess.run(
        [
            str(Path(sys.executable)),
            "-c",
            (
                "import json, os, utils; "
                "config = utils.load_config(os.environ['TEST_CONFIG_PATH']); "
                "print(json.dumps({"
                f"'api_key': config[{section!r}].get('api_key'), "
                f"'managed': {canonical_name!r} in utils.MANAGED_ENV_FILE_KEYS, "
                f"'external': {canonical_name!r} in utils.BOOT_ENV_CONFIG, "
                f"'runtime': os.environ.get({canonical_name!r})"
                "}))"
            ),
        ],
        cwd=ROOT,
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "api_key": "",
        "managed": True,
        "external": False,
        "runtime": "",
    }


@pytest.mark.parametrize(
    ("canonical_name", "legacy_name", "section"),
    [
        ("OMBRE_COMPRESS_BASE_URL", "OMBRE_BASE_URL", "dehydration"),
        ("OMBRE_EMBED_BASE_URL", None, "embedding"),
    ],
)
def test_managed_empty_provider_base_url_overrides_yaml_and_legacy_alias(
    tmp_path: Path,
    canonical_name: str,
    legacy_name: str | None,
    section: str,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(f"{canonical_name}=''\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "buckets_dir": str(tmp_path / "vault"),
                "state_dir": str(tmp_path / "state"),
                section: {"base_url": "https://stale-yaml.example/v1"},
            }
        ),
        encoding="utf-8",
    )
    process_env = os.environ.copy()
    for name in (
        canonical_name,
        legacy_name,
        "OMBRE_BUCKETS_DIR",
        "OMBRE_VAULT_DIR",
        "OMBRE_STATE_DIR",
        "OMBRE_RUNTIME_CONFIG_PATH",
    ):
        if name is not None:
            process_env.pop(name, None)
    process_env.update(
        {
            "OMBRE_ENV_PATH": str(env_path),
            "OMBRE_MANAGED_ENV_OVERRIDE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(ROOT / "src"),
            "TEST_CONFIG_PATH": str(config_path),
        }
    )
    if legacy_name is not None:
        process_env[legacy_name] = "https://stale-legacy.example/v1"

    completed = subprocess.run(
        [
            str(Path(sys.executable)),
            "-c",
            (
                "import json, os, utils; "
                "config = utils.load_config(os.environ['TEST_CONFIG_PATH']); "
                f"print(json.dumps(config[{section!r}].get('base_url')))"
            ),
        ],
        cwd=ROOT,
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == ""


@pytest.mark.parametrize(
    ("configured_transport", "expected_transport"),
    [("streamable-http", "streamable-http"), (None, "stdio")],
)
def test_managed_empty_non_secret_does_not_blank_boot_control_values(
    tmp_path: Path,
    configured_transport: str | None,
    expected_transport: str,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("OMBRE_TRANSPORT=\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    payload = {
        "buckets_dir": str(tmp_path / "vault"),
        "state_dir": str(tmp_path / "state"),
    }
    if configured_transport is not None:
        payload["transport"] = configured_transport
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    process_env = os.environ.copy()
    for name in (
        "OMBRE_TRANSPORT",
        "OMBRE_BUCKETS_DIR",
        "OMBRE_VAULT_DIR",
        "OMBRE_STATE_DIR",
        "OMBRE_RUNTIME_CONFIG_PATH",
    ):
        process_env.pop(name, None)
    process_env.update(
        {
            "OMBRE_ENV_PATH": str(env_path),
            "OMBRE_MANAGED_ENV_OVERRIDE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(ROOT / "src"),
            "TEST_CONFIG_PATH": str(config_path),
        }
    )

    completed = subprocess.run(
        [
            str(Path(sys.executable)),
            "-c",
            (
                "import os, utils; "
                "config = utils.load_config(os.environ['TEST_CONFIG_PATH']); "
                "print(config['transport'])"
            ),
        ],
        cwd=ROOT,
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == expected_transport


def test_direct_python_restart_loads_dashboard_managed_root_env_by_default(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    source_dir = repo / "src"
    source_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "src" / "utils.py", source_dir / "utils.py")
    secret = r'''native restart 'quoted' \\path $TOKEN'''
    (repo / ".env").write_text(
        "OMBRE_COMPRESS_API_KEY="
        + config_api._serialize_env_value(secret, shell_source=True)
        + "\n",
        encoding="utf-8",
    )
    process_env = os.environ.copy()
    for name in (
        "OMBRE_ENV_PATH",
        "OMBRE_MANAGED_ENV_OVERRIDE",
        "OMBRE_COMPRESS_API_KEY",
    ):
        process_env.pop(name, None)
    process_env["PYTHONPATH"] = str(source_dir)

    completed = subprocess.run(
        [
            str(Path(sys.executable)),
            "-c",
            "import os, utils; print(os.environ.get('OMBRE_COMPRESS_API_KEY', ''))",
        ],
        cwd=repo,
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == secret


@pytest.mark.parametrize(
    ("compose_name", "service_names"),
    [
        ("docker-compose.yml", ("ombre-brain",)),
        ("docker-compose.user.yml", ("ombre-brain",)),
        ("docker-compose.multi.yml", ("ming", "hong")),
    ],
)
def test_supported_compose_brains_persist_dashboard_env_in_the_vault_mount(
    compose_name: str,
    service_names: tuple[str, ...],
) -> None:
    payload = yaml.safe_load((ROOT / "deploy" / compose_name).read_text(encoding="utf-8"))

    for service_name in service_names:
        service = payload["services"][service_name]
        environment = service["environment"]
        if isinstance(environment, list):
            environment = {
                entry.partition("=")[0]: entry.partition("=")[2]
                for entry in environment
            }
        assert environment["OMBRE_ENV_PATH"] == (
            "/app/buckets/.ombre-managed.env"
        )
        assert str(environment["OMBRE_MANAGED_ENV_OVERRIDE"]) == "1"
        assert any(
            isinstance(volume, dict) and volume.get("target") == "/app/buckets"
            for volume in service["volumes"]
        )


def test_docker_image_defaults_dashboard_env_to_the_persistent_bucket_volume() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ENV OMBRE_ENV_PATH=/app/buckets/.ombre-managed.env" in dockerfile
    assert "ENV OMBRE_MANAGED_ENV_OVERRIDE=1" in dockerfile


def test_render_blueprint_persists_dashboard_env_on_its_bucket_disk() -> None:
    payload = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    service = payload["services"][0]
    env = {entry["key"]: entry for entry in service["envVars"]}

    assert env["OMBRE_ENV_PATH"]["value"] == (
        "/opt/render/project/src/buckets/.ombre-managed.env"
    )
    assert str(env["OMBRE_MANAGED_ENV_OVERRIDE"]["value"]) == "1"
    assert service["disk"]["mountPath"] == "/opt/render/project/src/buckets"


@pytest.mark.parametrize("compose_name", ["compose.cloudflare.yml", "compose.hk.yml"])
def test_full_compose_shares_the_managed_env_with_a_read_only_gateway(
    compose_name: str,
) -> None:
    payload = yaml.safe_load((ROOT / compose_name).read_text(encoding="utf-8"))
    brain = payload["services"]["ombre-brain"]
    gateway = payload["services"]["ombre-gateway"]

    for service in (brain, gateway):
        assert service["environment"]["OMBRE_ENV_PATH"] == (
            "/state/.ombre-managed.env"
        )
        assert str(service["environment"]["OMBRE_MANAGED_ENV_OVERRIDE"]) == "1"
    assert "/srv/ombre-brain/state:/state" in brain["volumes"]
    assert "/srv/ombre-brain/state:/state" in gateway["volumes"]
    assert all("/app/.env" not in str(volume) for volume in brain["volumes"])
    assert all("/app/.env" not in str(volume) for volume in gateway["volumes"])


def test_one_click_full_compose_shares_env_with_read_only_gateway() -> None:
    source = (ROOT / "scripts" / "one_click.sh").read_text(encoding="utf-8")
    start = source.index("write_compose_file()")
    end = source.index("ensure_tools()", start)
    generated = source[start:end]

    assert generated.count("OMBRE_ENV_PATH: /state/.ombre-managed.env") == 2
    assert generated.count("OMBRE_MANAGED_ENV_OVERRIDE: 1") == 2
    assert "./.env:/app/.env" not in generated


def test_one_click_native_launchers_keep_managed_env_out_of_shell_source() -> None:
    source = (ROOT / "scripts" / "one_click.sh").read_text(encoding="utf-8")
    native = source[
        source.index("load_python_direct_env()") : source.index(
            "select_deploy_target_for_task()"
        )
    ]
    generated = source[
        source.index("start_python_runtime()") : source.index(
            "update_python_runtime()"
        )
    ]

    assert 'OMBRE_ENV_PATH="${PWD}/state/.ombre-managed.env"' in native
    assert 'OMBRE_ENV_PATH="${PWD}/state/.ombre-managed.env"' in generated
    assert 'Join-Path $Root "state/.ombre-managed.env"' in generated
    assert "source state/.ombre-managed.env" not in source
