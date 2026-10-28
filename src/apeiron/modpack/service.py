"""High-level modpack and content installation.

Detects the archive format, creates an isolated instance, installs the required
loader and downloads verified files.  Unsupported or unlicensed sources fail
with a specific error instead of falling back to an unofficial mirror.
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from apeiron.core.errors import ModpackError
from apeiron.core.logging import get_logger
from apeiron.db.models import Instance, ModRecord
from apeiron.db.repositories import ModRepository
from apeiron.instances.manager import InstanceManager
from apeiron.loaders.fabric import FabricLoader, QuiltLoader
from apeiron.loaders.forge import ForgeLoader, NeoForgeLoader
from apeiron.modpack import curseforge, mrpack
from apeiron.net.downloader import DownloadManager

log = get_logger("modpack")

CONTENT_DIRS = {
    "mod": "mods",
    "resourcepack": "resourcepacks",
    "shaderpack": "shaderpacks",
    "datapack": "datapacks",
}
DISABLED_SUFFIX = ".disabled"


@dataclass
class ImportResult:
    instance: Instance
    format: str
    files_installed: int
    overrides_installed: int
    loader_installed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "instanceId": self.instance.id,
            "instanceName": self.instance.name,
            "format": self.format,
            "filesInstalled": self.files_installed,
            "overridesInstalled": self.overrides_installed,
            "loaderInstalled": self.loader_installed,
        }


ProgressFn = Callable[[str, int, int], None]


class ModpackService:
    def __init__(
        self,
        instances: InstanceManager,
        mods: ModRepository,
        downloader: DownloadManager,
        *,
        http,
        paths,
        curseforge_key: str = "",
        java_path: str = "",
    ) -> None:
        self.instances = instances
        self.mods = mods
        self.downloader = downloader
        self.http = http
        self.paths = paths
        self.curseforge = curseforge.CurseForgeClient(http, curseforge_key)
        self.java_path = java_path
        self.loaders: dict[str, Any] = {
            "fabric": FabricLoader(http, paths),
            "quilt": QuiltLoader(http, paths),
            "forge": ForgeLoader(http, paths, None, java_path),
            "neoforge": NeoForgeLoader(http, paths, None, java_path),
        }

    # -- detection ----------------------------------------------------------
    @staticmethod
    def detect_format(archive: Path) -> str:
        try:
            with zipfile.ZipFile(archive) as zf:
                names = set(zf.namelist())
        except (zipfile.BadZipFile, OSError) as exc:
            raise ModpackError(
                f"{archive.name} is not a readable archive", detail=str(exc)
            ) from exc
        if mrpack.INDEX_NAME in names:
            return "mrpack"
        if curseforge.MANIFEST_NAME in names:
            return "curseforge"
        raise ModpackError(
            "unrecognised modpack format",
            hint="Apeiron supports Modrinth (.mrpack) and CurseForge (manifest.json) packs.",
        )

    # -- import -------------------------------------------------------------
    def import_archive(self, archive: Path, *, name_override: str = "") -> ImportResult:
        fmt = self.detect_format(archive)
        if fmt == "mrpack":
            return self._import_mrpack(archive, name_override)
        return self._import_curseforge(archive, name_override)

    def _import_mrpack(self, archive: Path, name_override: str) -> ImportResult:
        contents = mrpack.read_mrpack(archive)
        index = contents.index
        instance = self.instances.create(
            name=name_override or index.name,
            mc_version=index.mc_version,
            loader=index.loader,
            loader_version=index.loader_version,
            metadata={
                "modpack": {"name": index.name, "versionId": index.version_id, "format": "mrpack"}
            },
        )
        instance_dir = Path(instance.directory)
        loader_ok = False
        if index.loader != "vanilla" and index.loader_version:
            loader_ok = self._install_loader(instance, index.loader, index.loader_version)
        files = mrpack.install_files(contents, instance_dir, self.downloader)
        for entry in index.files:
            self._record_file(
                instance,
                instance_dir,
                entry.path,
                "mod",
                entry.sha1,
                entry.size,
                entry.downloads[0],
            )
        overrides = mrpack.extract_overrides(archive, instance_dir)
        instance.installed = loader_ok or index.loader == "vanilla"
        self.instances.update(instance)
        log.info("imported mrpack %s (%d files, %d overrides)", instance.name, files, overrides)
        return ImportResult(instance, "mrpack", files, overrides, loader_ok)

    def _import_curseforge(self, archive: Path, name_override: str) -> ImportResult:
        manifest = curseforge.read_manifest(archive)
        if not self.curseforge.enabled:
            raise ModpackError(
                "this CurseForge pack cannot be installed without an API key",
                hint=(
                    "Add a CurseForge API key in Settings. Mod files are distributed by "
                    "CurseForge and may not be fetched from unofficial mirrors."
                ),
            )
        instance = self.instances.create(
            name=name_override or manifest.name,
            mc_version=manifest.mc_version,
            loader=manifest.loader,
            loader_version=manifest.loader_version,
            metadata={
                "modpack": {
                    "name": manifest.name,
                    "version": manifest.version,
                    "format": "curseforge",
                }
            },
        )
        instance_dir = Path(instance.directory)
        loader_ok = False
        if manifest.loader != "vanilla" and manifest.loader_version:
            loader_ok = self._install_loader(instance, manifest.loader, manifest.loader_version)
        files = self.curseforge.install_files(manifest, instance_dir, self.downloader)
        overrides = curseforge.extract_overrides(archive, manifest, instance_dir)
        instance.installed = loader_ok or manifest.loader == "vanilla"
        self.instances.update(instance)
        return ImportResult(instance, "curseforge", files, overrides, loader_ok)

    def _install_loader(self, instance: Instance, loader: str, version: str) -> bool:
        provider = self.loaders.get(loader)
        if provider is None or not version:
            return False
        try:
            if loader in ("forge", "neoforge"):
                result = provider.install(
                    instance.mc_version,
                    version,
                    java_path=self.java_path,
                    allow_execution=True,
                )
            else:
                result = provider.install(instance.mc_version, version)
        except Exception as exc:
            log.error("loader install failed for %s %s: %s", loader, version, exc)
            return False
        instance.loader = cast(Any, loader)
        instance.loader_version = version
        instance.metadata = {**instance.metadata, "loaderVersionId": result.version_id}
        self.instances.update(instance)
        return True

    def _record_file(
        self,
        instance: Instance,
        instance_dir: Path,
        relative: str,
        kind: str,
        sha1: str,
        size: int,
        url: str = "",
    ) -> None:
        path = instance_dir / relative
        if not path.exists():
            return
        record = ModRecord(
            instance_id=instance.id,
            filename=path.name,
            kind=kind,
            source="modrinth",
            sha1=sha1 or _sha1(path),
            size=size or path.stat().st_size,
            source_url=url,
        )
        self.mods.add(record)

    # -- individual content -------------------------------------------------
    def install_content_file(
        self, instance: Instance, source: Path, kind: str = "mod"
    ) -> ModRecord:
        if kind not in CONTENT_DIRS:
            raise ModpackError(f"unsupported content kind: {kind}")
        directory = Path(instance.directory) / CONTENT_DIRS[kind]
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / source.name
        if destination.exists():
            if _sha1(destination) == _sha1(source):
                log.info("%s is already installed in %s", source.name, instance.name)
                existing = next(
                    (
                        m
                        for m in self.mods.list_for_instance(instance.id, kind)
                        if m.filename == source.name
                    ),
                    None,
                )
                if existing:
                    return existing
            else:
                raise ModpackError(
                    f"{source.name} already exists in this instance with different contents"
                )
        shutil.copy2(source, destination)
        record = ModRecord(
            instance_id=instance.id,
            filename=source.name,
            kind=kind,
            source="manual",
            sha1=_sha1(destination),
            size=destination.stat().st_size,
        )
        self.mods.add(record)
        return record

    def set_content_enabled(self, instance: Instance, record: ModRecord, enabled: bool) -> None:
        directory = Path(instance.directory) / CONTENT_DIRS.get(record.kind, "mods")
        active = directory / record.filename
        disabled = directory / (record.filename + DISABLED_SUFFIX)
        if enabled and disabled.exists():
            disabled.replace(active)
        elif not enabled and active.exists():
            active.replace(disabled)
        self.mods.set_enabled(record.id, enabled)

    def remove_content(self, instance: Instance, record: ModRecord) -> None:
        directory = Path(instance.directory) / CONTENT_DIRS.get(record.kind, "mods")
        for candidate in (
            directory / record.filename,
            directory / (record.filename + DISABLED_SUFFIX),
        ):
            candidate.unlink(missing_ok=True)
        self.mods.delete(record.id)

    # -- export -------------------------------------------------------------
    def export_mrpack(self, instance: Instance, target: Path) -> Path:
        records = self.mods.list_for_instance(instance.id)
        files = [
            {
                "path": f"{CONTENT_DIRS.get(record.kind, 'mods')}/{record.filename}",
                "hashes": {"sha1": record.sha1, "sha512": record.sha512},
                "downloads": [record.source_url],
                "fileSize": record.size,
                "env": {"client": "required", "server": "optional"},
            }
            for record in records
            if record.source_url
        ]
        overrides = Path(instance.directory) / "config"
        return mrpack.export_mrpack(
            target=target,
            name=instance.name,
            version_id=f"{instance.mc_version}-{instance.loader}",
            mc_version=instance.mc_version,
            loader=instance.loader,
            loader_version=instance.loader_version,
            files=files,
            summary="Exported by Apeiron",
            overrides_dir=overrides if overrides.exists() else None,
        )


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
