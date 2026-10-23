"""Paths and settings behaviour."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from apeiron.core.config import (
    SCHEMA_VERSION,
    Settings,
    SettingsStore,
    migrate,
)
from apeiron.core.paths import AppPaths, current_platform_key


def test_app_paths_layout(app_paths: AppPaths):
    assert app_paths.data.is_dir()
    assert app_paths.logs.is_dir()
    assert app_paths.instances.is_dir()
    assert app_paths.libraries.is_dir()
    assert app_paths.versions.is_dir()
    assert app_paths.database.parent == app_paths.data


def test_instance_dir_rejects_traversal(app_paths: AppPaths):
    with pytest.raises(ValueError):
        app_paths.instance_dir("../escape")
    with pytest.raises(ValueError):
        app_paths.instance_dir("a/b")
    with pytest.raises(ValueError):
        app_paths.instance_dir("")


def test_instance_dir_is_stable(app_paths: AppPaths):
    first = app_paths.instance_dir("abc123")
    second = app_paths.instance_dir("abc123")
    assert first == second
    assert first.parent == app_paths.instances


def test_platform_key_is_known():
    assert current_platform_key() in {"windows", "macos", "linux"}


def test_settings_defaults_are_chinese_first(settings_store: SettingsStore):
    settings = settings_store.load()
    assert settings.language == "system"
    assert settings.default_memory_mb >= 512


def test_settings_roundtrip(tmp_path: Path):
    store = SettingsStore(tmp_path / "settings.json")
    store.load()
    store.update(language="en_US", theme="dark", default_memory_mb=8192)
    reloaded = SettingsStore(tmp_path / "settings.json")
    settings = reloaded.load()
    assert settings.language == "en_US"
    assert settings.theme == "dark"
    assert settings.default_memory_mb == 8192


def test_settings_clamp_out_of_range(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "default_memory_mb": 1,
                "max_concurrent_downloads": 999,
                "download_timeout": -5,
                "theme": "neon",
                "language": "xx_YY",
            }
        ),
        encoding="utf-8",
    )
    settings = SettingsStore(path).load()
    assert settings.default_memory_mb == 512
    assert settings.max_concurrent_downloads == 16
    assert settings.download_timeout >= 5
    assert settings.theme == "system"
    assert settings.language == "system"


def test_settings_corrupt_file_falls_back_and_backs_up(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    settings = SettingsStore(path).load()
    assert settings.language == "system"
    assert (tmp_path / "settings.json.broken").exists()


def test_settings_migration_v1(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"schema_version": 1, "lang": "en_US", "dark_mode": True}))
    settings = SettingsStore(path).load()
    assert settings.language == "en_US"
    assert settings.theme == "dark"
    assert settings.schema_version == SCHEMA_VERSION


def test_unknown_keys_are_preserved(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "future_option": {"a": 1}}))
    store = SettingsStore(path)
    store.load()
    store.update(theme="dark")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["future_option"] == {"a": 1}


def test_atomic_write_leaves_no_partial_file(tmp_path: Path):
    store = SettingsStore(tmp_path / "settings.json")
    store.load()
    store.update(theme="light")
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".settings.json.")]
    assert leftovers == []


def test_settings_file_permissions(tmp_path: Path):
    store = SettingsStore(tmp_path / "settings.json")
    store.load()
    store.update(theme="dark")
    if os.name == "posix":
        assert (tmp_path / "settings.json").stat().st_mode & 0o777 == 0o600


def test_migrate_keeps_version_two_values():
    raw = {"schema_version": 2, "language": "zh_CN", "theme": "light"}
    migrated = migrate(dict(raw))
    assert migrated["language"] == "zh_CN"
    assert migrated["schema_version"] == SCHEMA_VERSION


def test_normalised_copy_does_not_mutate_original():
    original = Settings(default_memory_mb=10_000_000)
    clone = original.normalised()
    assert original.default_memory_mb == 10_000_000
    assert clone.default_memory_mb == 65536
