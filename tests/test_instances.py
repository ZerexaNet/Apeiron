"""Instance lifecycle and isolation."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from apeiron.core.errors import InstanceError, InstanceNotFoundError, SecurityError
from apeiron.instances.manager import InstanceManager


@pytest.fixture()
def manager(repos, app_paths) -> InstanceManager:
    return InstanceManager(repos["instances"], app_paths)


def test_create_instance_builds_directories(manager: InstanceManager, app_paths):
    instance = manager.create(name="Survival", mc_version="1.20.1")
    directory = Path(instance.directory)
    assert directory.is_dir()
    assert directory.parent == app_paths.instances
    for sub in ("mods", "saves", "config", "logs"):
        assert (directory / sub).is_dir()
    assert manager.get(instance.id).name == "Survival"


def test_create_assigns_unique_names(manager: InstanceManager):
    first = manager.create(name="Same", mc_version="1.20.1")
    second = manager.create(name="Same", mc_version="1.20.1")
    assert first.name == "Same"
    assert second.name == "Same (2)"
    assert first.directory != second.directory


def test_create_rejects_empty_name(manager: InstanceManager):
    with pytest.raises(InstanceError):
        manager.create(name="   ", mc_version="1.20.1")


def test_two_instances_are_isolated(manager: InstanceManager):
    first = manager.create(name="A", mc_version="1.20.1")
    second = manager.create(name="B", mc_version="1.20.1")
    (Path(first.directory) / "mods" / "only-a.jar").write_bytes(b"a")
    assert (Path(first.directory) / "mods" / "only-a.jar").exists()
    assert not (Path(second.directory) / "mods" / "only-a.jar").exists()
    assert first.directory != second.directory


def test_duplicate_copies_files_with_new_id(manager: InstanceManager):
    source = manager.create(name="Original", mc_version="1.20.1")
    (Path(source.directory) / "config" / "settings.txt").write_text("keep me")
    clone = manager.duplicate(source.id)
    assert clone.id != source.id
    assert clone.directory != source.directory
    assert (Path(clone.directory) / "config" / "settings.txt").read_text() == "keep me"
    assert clone.name.startswith("Original")


def test_rename_conflict(manager: InstanceManager):
    first = manager.create(name="One", mc_version="1.20.1")
    manager.create(name="Two", mc_version="1.20.1")
    with pytest.raises(InstanceError):
        manager.rename(first.id, "Two")
    renamed = manager.rename(first.id, "Uno")
    assert renamed.name == "Uno"


def test_delete_removes_only_that_instance(manager: InstanceManager):
    first = manager.create(name="Doomed", mc_version="1.20.1")
    second = manager.create(name="Safe", mc_version="1.20.1")
    (Path(second.directory) / "keep.txt").write_text("still here")
    manager.delete(first.id)
    assert not Path(first.directory).exists()
    assert Path(second.directory, "keep.txt").exists()
    with pytest.raises(InstanceNotFoundError):
        manager.get(first.id)


def test_delete_refuses_outside_instance_root(manager: InstanceManager, app_paths, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("do not delete")
    instance = manager.create(name="Weird", mc_version="1.20.1")
    instance.directory = str(outside)
    manager.update(instance)
    with pytest.raises(SecurityError):
        manager.delete(instance.id)
    assert (outside / "precious.txt").exists()


def test_delete_refuses_instances_root(manager: InstanceManager, app_paths):
    instance = manager.create(name="Rootish", mc_version="1.20.1")
    instance.directory = str(app_paths.instances)
    manager.update(instance)
    with pytest.raises(SecurityError):
        manager.delete(instance.id)
    assert app_paths.instances.exists()


def test_delete_refuses_nested_directory(manager: InstanceManager):
    instance = manager.create(name="Nested", mc_version="1.20.1")
    nested = Path(instance.directory) / "saves"
    instance.directory = str(nested)
    manager.update(instance)
    with pytest.raises(SecurityError):
        manager.delete(instance.id)


def test_backup_and_restore(manager: InstanceManager, app_paths):
    instance = manager.create(name="Backups", mc_version="1.20.1")
    world = Path(instance.directory) / "saves" / "World1"
    world.mkdir(parents=True)
    (world / "level.dat").write_text("original")

    info = manager.backup(instance.id)
    assert info.path.exists()
    assert info.size > 0

    (world / "level.dat").write_text("corrupted")
    restored = manager.restore_backup(instance.id, info.path)
    assert restored >= 1
    assert (world / "level.dat").read_text() == "original"
    # the previous (corrupted) state is preserved, not destroyed
    assert any(p.name.startswith("_pre-restore") for p in Path(instance.directory).iterdir())


def test_export_and_import_roundtrip(manager: InstanceManager, app_paths, tmp_path: Path):
    instance = manager.create(
        name="Exported",
        mc_version="1.20.1",
        loader="fabric",
        loader_version="0.16.5",
        memory_mb=3072,
        metadata={"loaderVersionId": "fabric-loader-0.16.5-1.20.1"},
    )
    (Path(instance.directory) / "config" / "mod.toml").write_text("enabled=true")

    archive = tmp_path / "export.zip"
    manager.export(instance.id, archive)
    assert archive.exists()
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    assert "apeiron-instance.json" in names
    assert any(name.startswith("config/") for name in names)
    assert not any(name.startswith("logs/") for name in names)

    imported = manager.import_instance(archive)
    assert imported.id != instance.id
    assert imported.mc_version == "1.20.1"
    assert imported.loader == "fabric"
    assert imported.memory_mb == 3072
    assert (Path(imported.directory) / "config" / "mod.toml").read_text() == "enabled=true"
    assert not (Path(imported.directory) / "apeiron-instance.json").exists()


def test_import_rejects_foreign_archive(manager: InstanceManager, tmp_path: Path):
    archive = tmp_path / "random.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("hello.txt", "world")
    with pytest.raises(InstanceError):
        manager.import_instance(archive)


def test_export_does_not_include_credentials(manager: InstanceManager, tmp_path: Path):
    instance = manager.create(name="Secretive", mc_version="1.20.1")
    instance.metadata = {"note": "ok", "credentials": {"token": "should-not-appear"}}
    manager.update(instance)
    archive = tmp_path / "secret.zip"
    manager.export(instance.id, archive)
    with zipfile.ZipFile(archive) as zf:
        payload = zf.read("apeiron-instance.json").decode("utf-8")
    assert "should-not-appear" not in payload


def test_total_size(manager: InstanceManager):
    instance = manager.create(name="Sized", mc_version="1.20.1")
    (Path(instance.directory) / "config" / "x.bin").write_bytes(b"0" * 100)
    assert manager.total_size(instance) >= 100
