"""Instance lifecycle: create, copy, rename, delete, backup, export, import.

Isolation rules enforced here (and covered by tests):

* every instance lives in ``<root>/instances/<id>`` and the id is validated;
* deletion refuses any path that is not strictly inside the instance root, and
  never removes the root itself;
* copying produces a *new* id and directory, so two instances can never share
  files;
* export omits credentials and the shared asset/library caches.
"""

from __future__ import annotations

import builtins
import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from apeiron.core.archive import extract_zip, is_within, zip_directory
from apeiron.core.errors import (
    InstanceError,
    InstanceNotFoundError,
    SecurityError,
)
from apeiron.core.logging import get_logger
from apeiron.core.paths import AppPaths
from apeiron.db.models import Instance, new_id, utc_now
from apeiron.db.repositories import InstanceRepository

log = get_logger("instances")

INSTANCE_MANIFEST = "apeiron-instance.json"
MANIFEST_VERSION = 1
STANDARD_DIRECTORIES = (
    "mods",
    "resourcepacks",
    "shaderpacks",
    "saves",
    "config",
    "logs",
    "datapacks",
)
EXPORT_EXCLUDE = ("logs", "crash-reports", "screenshots", ".cache")


@dataclass
class BackupInfo:
    path: Path
    name: str
    size: int
    created_at: str


class InstanceManager:
    def __init__(self, repo: InstanceRepository, paths: AppPaths) -> None:
        self.repo = repo
        self.paths = paths

    # -- queries ------------------------------------------------------------
    def list(self) -> builtins.list[Instance]:
        return self.repo.list()

    def get(self, instance_id: str) -> Instance:
        return self.repo.get(instance_id)

    def directory(self, instance: Instance | str) -> Path:
        instance_id = instance if isinstance(instance, str) else instance.id
        return self.paths.instance_dir(instance_id)

    # -- create -------------------------------------------------------------
    def create(
        self,
        *,
        name: str,
        mc_version: str,
        game_type: str = "java",
        loader: str = "vanilla",
        loader_version: str = "",
        account_id: str | None = None,
        memory_mb: int = 4096,
        java_path: str = "",
        width: int = 854,
        height: int = 480,
        metadata: dict[str, Any] | None = None,
        instance_id: str | None = None,
    ) -> Instance:
        if not name.strip():
            raise InstanceError("instance name must not be empty")
        if not mc_version:
            raise InstanceError("a Minecraft version is required")
        unique_name = self.repo.unique_name(name.strip())
        identifier = instance_id or new_id()
        directory = self.paths.instance_dir(identifier)
        directory.mkdir(parents=True, exist_ok=False)
        for sub in STANDARD_DIRECTORIES:
            (directory / sub).mkdir(exist_ok=True)

        instance = Instance(
            id=identifier,
            name=unique_name,
            game_type="bedrock" if game_type == "bedrock" else "java",
            mc_version=mc_version,
            loader=cast(Any, loader)
            if loader in ("vanilla", "fabric", "forge", "neoforge", "quilt")
            else "vanilla",
            loader_version=loader_version,
            account_id=account_id,
            memory_mb=memory_mb,
            java_path=java_path,
            width=width,
            height=height,
            directory=str(directory),
            metadata=metadata or {},
        )
        try:
            self.repo.add(instance)
        except Exception:
            # Never leave an orphan directory behind when the row fails.
            shutil.rmtree(directory, ignore_errors=True)
            raise
        log.info("created instance %s (%s/%s)", instance.name, instance.mc_version, instance.loader)
        return instance

    # -- modify -------------------------------------------------------------
    def update(self, instance: Instance) -> Instance:
        return self.repo.update(instance)

    def rename(self, instance_id: str, new_name: str) -> Instance:
        instance = self.repo.get(instance_id)
        candidate = new_name.strip()
        if not candidate:
            raise InstanceError("instance name must not be empty")
        if candidate != instance.name:
            existing = self.repo.find_by_name(candidate)
            if existing is not None and existing.id != instance.id:
                raise InstanceError(f"an instance named {candidate} already exists")
        instance.name = candidate
        return self.repo.update(instance)

    def duplicate(self, instance_id: str, new_name: str = "") -> Instance:
        source = self.repo.get(instance_id)
        source_dir = Path(source.directory)
        if not source_dir.exists():
            raise InstanceNotFoundError(f"instance directory is missing: {source_dir}")
        clone = self.create(
            name=new_name or f"{source.name} copy",
            mc_version=source.mc_version,
            game_type=source.game_type,
            loader=source.loader,
            loader_version=source.loader_version,
            account_id=source.account_id,
            memory_mb=source.memory_mb,
            java_path=source.java_path,
            width=source.width,
            height=source.height,
            metadata=dict(source.metadata),
        )
        target_dir = Path(clone.directory)
        try:
            shutil.copytree(source_dir, target_dir, dirs_exist_ok=True, symlinks=False)
        except OSError as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            self.repo.delete(clone.id)
            raise InstanceError("could not copy the instance directory", detail=str(exc)) from exc
        clone.installed = source.installed
        return self.repo.update(clone)

    # -- delete -------------------------------------------------------------
    def delete(self, instance_id: str) -> None:
        instance = self.repo.get(instance_id)
        directory = Path(instance.directory)
        self._assert_safe_to_delete(directory)
        if directory.exists():
            shutil.rmtree(directory)
        self.repo.delete(instance_id)
        log.info("deleted instance %s", instance.name)

    def _assert_safe_to_delete(self, directory: Path) -> None:
        root = self.paths.instances.resolve()
        resolved = directory.resolve()
        if resolved == root:
            raise SecurityError("refusing to delete the instances root directory")
        if not is_within(resolved, root):
            raise SecurityError(
                f"refusing to delete a directory outside the instances root: {resolved}"
            )
        if resolved.parent != root:
            raise SecurityError(
                f"refusing to delete a nested directory that is not a direct instance: {resolved}"
            )
        if resolved == self.paths.root.resolve():
            raise SecurityError("refusing to delete the application root")

    # -- backup / restore ---------------------------------------------------
    def backup(self, instance_id: str, label: str = "") -> BackupInfo:
        instance = self.repo.get(instance_id)
        directory = Path(instance.directory)
        saves = directory / "saves"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_label = "".join(c for c in (label or instance.name) if c.isalnum() or c in "-_")[:40]
        target = self.paths.backups / f"{safe_label}-{stamp}.zip"
        self.paths.backups.mkdir(parents=True, exist_ok=True)
        if saves.exists():
            zip_directory(saves, target)
        else:
            with zipfile.ZipFile(target, "w") as zf:
                zf.writestr("EMPTY.txt", "This instance had no saves directory at backup time.\n")
        stat = target.stat()
        log.info("backed up %s to %s", instance.name, target.name)
        return BackupInfo(
            path=target,
            name=target.name,
            size=stat.st_size,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )

    def list_backups(self) -> builtins.list[BackupInfo]:
        if not self.paths.backups.exists():
            return []
        items: list[BackupInfo] = []
        for path in sorted(self.paths.backups.glob("*.zip"), reverse=True):
            stat = path.stat()
            items.append(
                BackupInfo(
                    path=path,
                    name=path.name,
                    size=stat.st_size,
                    created_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                )
            )
        return items

    def restore_backup(self, instance_id: str, backup: Path) -> int:
        instance = self.repo.get(instance_id)
        directory = Path(instance.directory)
        if not is_within(directory, self.paths.instances):
            raise SecurityError("instance directory is outside the instance root")
        if not backup.exists():
            raise InstanceError(f"backup not found: {backup}")
        saves = directory / "saves"
        # The previous worlds are moved next to (not inside) `saves`, so the
        # archive can be extracted into a clean directory.
        existing = directory / f"_pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        if saves.exists():
            shutil.move(str(saves), str(existing))
        written = extract_zip(backup, saves)
        log.info("restored %d files from %s", len(written), backup.name)
        return len(written)

    # -- export / import ----------------------------------------------------
    def export(self, instance_id: str, target: Path) -> Path:
        instance = self.repo.get(instance_id)
        directory = Path(instance.directory)
        if not directory.exists():
            raise InstanceNotFoundError(f"instance directory is missing: {directory}")
        manifest = {
            "manifestVersion": MANIFEST_VERSION,
            "name": instance.name,
            "mcVersion": instance.mc_version,
            "gameType": instance.game_type,
            "loader": instance.loader,
            "loaderVersion": instance.loader_version,
            "memoryMb": instance.memory_mb,
            "jvmArgs": instance.jvm_args,
            "gameArgs": instance.game_args,
            "width": instance.width,
            "height": instance.height,
            "tags": instance.tags,
            "metadata": {k: v for k, v in instance.metadata.items() if k != "credentials"},
            "exportedAt": utc_now(),
            "exportedBy": "Apeiron",
        }
        tmp_manifest = directory / INSTANCE_MANIFEST
        tmp_manifest.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        try:
            return zip_directory(directory, target, exclude=EXPORT_EXCLUDE)
        finally:
            tmp_manifest.unlink(missing_ok=True)

    def import_instance(self, archive: Path, new_name: str = "") -> Instance:
        if not archive.exists():
            raise InstanceError(f"archive not found: {archive}")
        destination_id = new_id()
        destination = self.paths.instance_dir(destination_id)
        destination.mkdir(parents=True, exist_ok=False)
        try:
            extract_zip(archive, destination)
            manifest_path = destination / INSTANCE_MANIFEST
            if not manifest_path.exists():
                raise InstanceError(
                    f"the archive is not an Apeiron instance export (missing {INSTANCE_MANIFEST})"
                )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_path.unlink(missing_ok=True)
            name = new_name or str(manifest.get("name") or "Imported instance")
            instance = self.repo.unique_name(name)
            record = Instance(
                id=destination_id,
                name=instance,
                game_type=cast(Any, str(manifest.get("gameType", "java"))),
                mc_version=str(manifest.get("mcVersion", "")),
                loader=cast(Any, str(manifest.get("loader", "vanilla"))),
                loader_version=str(manifest.get("loaderVersion", "")),
                memory_mb=int(manifest.get("memoryMb", 4096)),
                jvm_args=str(manifest.get("jvmArgs", "")),
                game_args=str(manifest.get("gameArgs", "")),
                width=int(manifest.get("width", 854)),
                height=int(manifest.get("height", 480)),
                tags=list(manifest.get("tags") or []),
                metadata=dict(manifest.get("metadata") or {}),
                directory=str(destination),
            )
            if not record.mc_version:
                raise InstanceError("the archive does not declare a Minecraft version")
            self.repo.add(record)
            log.info("imported instance %s from %s", record.name, archive.name)
            return record
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    # -- filesystem helpers -------------------------------------------------
    def ensure_layout(self, instance: Instance) -> Path:
        directory = Path(instance.directory)
        directory.mkdir(parents=True, exist_ok=True)
        for sub in STANDARD_DIRECTORIES:
            (directory / sub).mkdir(exist_ok=True)
        return directory

    def total_size(self, instance: Instance) -> int:
        directory = Path(instance.directory)
        if not directory.exists():
            return 0
        total = 0
        for path in directory.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
        return total


def instance_summary(instance: Instance, size: int | None = None) -> dict[str, Any]:
    """Projection consumed by the QML instance grid."""
    return {
        "id": instance.id,
        "name": instance.name,
        "gameType": instance.game_type,
        "mcVersion": instance.mc_version,
        "loader": instance.loader,
        "loaderVersion": instance.loader_version,
        "loaderLabel": instance.display_loader,
        "memoryMb": instance.memory_mb,
        "accountId": instance.account_id or "",
        "lastPlayed": instance.last_played_at or "",
        "playCount": instance.play_count,
        "installed": instance.installed,
        "sizeBytes": size if size is not None else 0,
        "tags": instance.tags,
    }
