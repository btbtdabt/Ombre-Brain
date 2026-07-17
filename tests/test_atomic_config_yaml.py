"""Applicable P0 atomic-YAML regressions adapted to the root runtime API."""

import errno

import pytest
import yaml

import utils


def _set_value(key, value):
    def mutate(config):
        config[key] = value

    return mutate


def test_atomic_update_yaml_validates_top_level_mapping(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("- not\n- a mapping\n", encoding="utf-8")

    with pytest.raises(ValueError, match="top level must be a mapping"):
        utils.atomic_update_yaml(config_path, _set_value("new", "value"))


def test_atomic_update_yaml_falls_back_for_single_file_bind_mount(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("existing: keep\n", encoding="utf-8")

    def bind_mount_replace(_source, _target):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(utils.os, "replace", bind_mount_replace)
    monkeypatch.setattr(utils, "_is_exact_linux_mount_point", lambda _path: True)

    persisted = utils.atomic_update_yaml(
        config_path,
        _set_value("dehydration", {"api_key": "new-key"}),
    )

    assert persisted == {
        "existing": "keep",
        "dehydration": {"api_key": "new-key"},
    }
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == persisted
    assert list(tmp_path.glob(".config.yaml.tmp.*")) == []


def test_atomic_update_yaml_creates_missing_parent(tmp_path):
    config_path = tmp_path / "new" / "nested" / "config.yaml"

    persisted = utils.atomic_update_yaml(
        config_path,
        _set_value("deployment", {"profile": "local"}),
    )

    assert persisted == {"deployment": {"profile": "local"}}
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == persisted


def test_atomic_update_yaml_does_not_hide_other_replace_failures(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("existing: keep\n", encoding="utf-8")

    def denied_replace(_source, _target):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(utils.os, "replace", denied_replace)

    with pytest.raises(OSError, match="Permission denied"):
        utils.atomic_update_yaml(config_path, _set_value("new", "value"))

    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == {
        "existing": "keep"
    }
    assert list(tmp_path.glob(".config.yaml.tmp.*")) == []


def test_busy_replace_is_not_overwritten_when_target_is_not_mount_point(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "config.yaml"
    original = b"existing: keep\n"
    config_path.write_bytes(original)
    monkeypatch.setattr(
        utils.os,
        "replace",
        lambda _source, _target: (_ for _ in ()).throw(
            OSError(errno.EBUSY, "Device or resource busy")
        ),
    )
    monkeypatch.setattr(utils, "_is_exact_linux_mount_point", lambda _path: False)

    with pytest.raises(OSError, match="Device or resource busy"):
        utils.atomic_update_yaml(config_path, _set_value("new", "value"))

    assert config_path.read_bytes() == original
    assert list(tmp_path.glob(".config.yaml.tmp.*")) == []


def test_bind_mount_write_failure_restores_previous_config(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    original = b"existing: keep\n"
    config_path.write_bytes(original)
    monkeypatch.setattr(
        utils.os,
        "replace",
        lambda _source, _target: (_ for _ in ()).throw(
            OSError(errno.EBUSY, "Device or resource busy")
        ),
    )
    monkeypatch.setattr(utils, "_is_exact_linux_mount_point", lambda _path: True)

    real_fsync = utils.os.fsync
    fsync_calls = 0

    def fail_target_fsync_once(descriptor):
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError(errno.EIO, "simulated target fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(utils.os, "fsync", fail_target_fsync_once)

    with pytest.raises(OSError, match="simulated target fsync failure"):
        utils.atomic_update_yaml(config_path, _set_value("new", "value"))

    assert config_path.read_bytes() == original
    assert list(tmp_path.glob(".config.yaml.tmp.*")) == []
