"""Modrinth / CurseForge modpack handling."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from apeiron.core.errors import ModpackError, SecurityError
from apeiron.modpack import curseforge, mrpack
from apeiron.modpack.service import ModpackService

PAYLOAD = b"fake-mod-binary"


class StubDownloader:
    """Writes the expected bytes for every spec, verifying the destination path."""

    def __init__(self) -> None:
        self.specs = []

    def run(self, specs):
        results = []
        for spec in specs:
            self.specs.append(spec)
            spec.destination.parent.mkdir(parents=True, exist_ok=True)
            spec.destination.write_bytes(PAYLOAD)
            results.append(spec)
        return results


def _write_mrpack(path: Path, files: list[dict], overrides: dict[str, bytes] | None = None) -> Path:
    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": "1.0.0",
        "name": "Test Pack",
        "summary": "unit test pack",
        "files": files,
        "dependencies": {"minecraft": "1.20.1", "fabric-loader": "0.16.5"},
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("modrinth.index.json", json.dumps(index))
        for name, payload in (overrides or {}).items():
            zf.writestr(f"overrides/{name}", payload)
    return path


def test_read_mrpack(tmp_path: Path):
    archive = _write_mrpack(
        tmp_path / "pack.mrpack",
        [
            {
                "path": "mods/example.jar",
                "hashes": {"sha1": "abc", "sha512": "def"},
                "downloads": ["https://cdn.modrinth.com/example.jar"],
                "fileSize": 100,
            }
        ],
    )
    contents = mrpack.read_mrpack(archive)
    assert contents.index.name == "Test Pack"
    assert contents.index.mc_version == "1.20.1"
    assert contents.index.loader == "fabric"
    assert contents.index.loader_version == "0.16.5"
    assert len(contents.index.files) == 1


def test_mrpack_rejects_traversal(tmp_path: Path):
    archive = _write_mrpack(
        tmp_path / "evil.mrpack",
        [
            {
                "path": "../../escape.jar",
                "hashes": {"sha1": "abc"},
                "downloads": ["https://x/y.jar"],
                "fileSize": 1,
            }
        ],
    )
    with pytest.raises(SecurityError):
        mrpack.read_mrpack(archive)


def test_mrpack_requires_minecraft_version(tmp_path: Path):
    archive = tmp_path / "bad.mrpack"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("modrinth.index.json", json.dumps({"formatVersion": 1, "dependencies": {}}))
    with pytest.raises(ModpackError):
        mrpack.read_mrpack(archive)


def test_extract_overrides_only_from_override_tree(tmp_path: Path):
    archive = _write_mrpack(
        tmp_path / "p.mrpack", [], {"config/app.toml": b"a=1", "mods/x.jar": b"binary"}
    )
    target = tmp_path / "instance"
    target.mkdir()
    written = mrpack.extract_overrides(archive, target)
    assert written == 2
    assert (target / "config" / "app.toml").read_bytes() == b"a=1"
    assert (target / "mods" / "x.jar").read_bytes() == b"binary"


def test_import_mrpack_creates_instance(tmp_path: Path, repos, app_paths):
    archive = _write_mrpack(
        tmp_path / "pack.mrpack",
        [
            {
                "path": "mods/fabric-api.jar",
                "hashes": {"sha1": "abc"},
                "downloads": ["https://cdn.modrinth.com/a.jar"],
                "fileSize": len(PAYLOAD),
            }
        ],
        {"config/example.toml": b"x=1"},
    )
    downloader = StubDownloader()
    service = ModpackService(
        __import__("apeiron.instances.manager", fromlist=["InstanceManager"]).InstanceManager(
            repos["instances"], app_paths
        ),
        repos["mods"],
        downloader,
        http=None,
        paths=app_paths,
    )
    result = service.import_archive(archive)
    assert result.format == "mrpack"
    assert result.files_installed == 1
    assert result.overrides_installed == 1
    instance = service.instances.get(result.instance.id)
    assert instance.mc_version == "1.20.1"
    assert instance.loader == "fabric"
    assert (Path(instance.directory) / "mods" / "fabric-api.jar").read_bytes() == PAYLOAD
    assert (Path(instance.directory) / "config" / "example.toml").exists()
    records = repos["mods"].list_for_instance(instance.id)
    assert len(records) == 1
    assert records[0].source == "modrinth"


def test_import_curseforge_requires_api_key(tmp_path: Path, repos, app_paths):
    archive = tmp_path / "cf.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "name": "CF Pack",
                    "version": "1.0",
                    "minecraft": {
                        "version": "1.20.1",
                        "modLoaders": [{"id": "forge-47.2.0", "primary": True}],
                    },
                    "files": [{"projectID": 1, "fileID": 2, "required": True}],
                }
            ),
        )
    service = ModpackService(
        __import__("apeiron.instances.manager", fromlist=["InstanceManager"]).InstanceManager(
            repos["instances"], app_paths
        ),
        repos["mods"],
        StubDownloader(),
        http=None,
        paths=app_paths,
    )
    with pytest.raises(ModpackError) as excinfo:
        service.import_archive(archive)
    assert "API key" in str(excinfo.value)


def test_curseforge_manifest_parsing(tmp_path: Path):
    archive = tmp_path / "cf.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "name": "CF",
                    "version": "2.0",
                    "author": "someone",
                    "minecraft": {
                        "version": "1.19.2",
                        "modLoaders": [{"id": "forge-43.2.0", "primary": True}],
                    },
                    "files": [{"projectID": 10, "fileID": 20, "required": True}],
                }
            ),
        )
    manifest = curseforge.read_manifest(archive)
    assert manifest.mc_version == "1.19.2"
    assert manifest.loader == "forge"
    assert manifest.loader_version == "43.2.0"
    assert manifest.files[0].project_id == 10


def test_detect_format(tmp_path: Path, repos, app_paths):
    service = ModpackService(
        __import__("apeiron.instances.manager", fromlist=["InstanceManager"]).InstanceManager(
            repos["instances"], app_paths
        ),
        repos["mods"],
        StubDownloader(),
        http=None,
        paths=app_paths,
    )
    mrpack_archive = _write_mrpack(tmp_path / "a.mrpack", [])
    assert service.detect_format(mrpack_archive) == "mrpack"

    other = tmp_path / "b.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(ModpackError):
        service.detect_format(other)


def test_export_mrpack_is_valid(tmp_path: Path):
    target = tmp_path / "export.mrpack"
    mrpack.export_mrpack(
        target=target,
        name="Exported",
        version_id="1.0.0",
        mc_version="1.20.1",
        loader="fabric",
        loader_version="0.16.5",
        files=[
            {
                "path": "mods/a.jar",
                "hashes": {"sha1": "abc", "sha512": "def"},
                "downloads": ["https://cdn.modrinth.com/a.jar"],
                "fileSize": 10,
                "env": {"client": "required", "server": "optional"},
            }
        ],
        overrides_dir=None,
    )
    contents = mrpack.read_mrpack(target)
    assert contents.index.mc_version == "1.20.1"
    assert contents.index.loader == "fabric"
    assert len(contents.index.files) == 1


def test_install_and_toggle_content(tmp_path: Path, repos, app_paths):
    from apeiron.instances.manager import InstanceManager

    service = ModpackService(
        InstanceManager(repos["instances"], app_paths),
        repos["mods"],
        StubDownloader(),
        http=None,
        paths=app_paths,
    )
    instance = service.instances.create(name="Content", mc_version="1.20.1")
    source = tmp_path / "some-mod.jar"
    source.write_bytes(PAYLOAD)

    record = service.install_content_file(instance, source, "mod")
    assert (Path(instance.directory) / "mods" / "some-mod.jar").exists()

    service.set_content_enabled(instance, record, False)
    assert (Path(instance.directory) / "mods" / "some-mod.jar.disabled").exists()

    service.set_content_enabled(instance, record, True)
    assert (Path(instance.directory) / "mods" / "some-mod.jar").exists()

    service.remove_content(instance, record)
    assert not (Path(instance.directory) / "mods" / "some-mod.jar").exists()
    assert repos["mods"].list_for_instance(instance.id) == []
