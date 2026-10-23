"""Java Edition installation: libraries, natives, assets and the client jar.

The installer is deliberately idempotent: every file is content-addressed and
skipped when its SHA-1 already matches, so an interrupted installation can be
resumed simply by running it again.
"""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from apeiron.core.errors import ArchiveError, ConfigError, PermissionError_
from apeiron.core.logging import get_logger
from apeiron.core.paths import AppPaths, current_platform_key
from apeiron.mc.manifest import VersionManifest, resource_url
from apeiron.mc.version import Library, VersionDescriptor, classpath_entries
from apeiron.net.downloader import DownloadManager, DownloadSpec

log = get_logger("installer")


@dataclass
class InstallStage:
    """Structured progress for the UI (never parsed from log text)."""

    name: str  # version|libraries|natives|assets|client|done
    detail: str = ""
    completed: int = 0
    total: int = 0

    @property
    def percent(self) -> float:
        return 100.0 if self.total == 0 else min(100.0, self.completed * 100.0 / self.total)


StageCallback = Callable[[InstallStage], None]


@dataclass
class InstalledVersion:
    version_id: str
    descriptor: VersionDescriptor
    client_jar: Path
    natives_dir: Path
    libraries_root: Path
    assets_root: Path
    asset_index: str
    asset_index_path: Path
    classpath: list[str] = field(default_factory=list)

    def jvm_classpath(self) -> str:
        return os.pathsep.join(self.classpath)


class JavaInstaller:
    """Downloads and verifies everything a version needs to start."""

    def __init__(
        self,
        http,
        paths: AppPaths,
        downloader: DownloadManager,
        manifest: VersionManifest,
        platform_key: str | None = None,
    ) -> None:
        self.http = http
        self.paths = paths
        self.downloader = downloader
        self.manifest = manifest
        self.platform_key = platform_key or current_platform_key()

    # -- public -------------------------------------------------------------
    def install(
        self,
        version_id: str,
        *,
        on_stage: StageCallback | None = None,
        include_assets: bool = True,
    ) -> InstalledVersion:
        report = on_stage or (lambda stage: None)

        report(InstallStage("version", detail=version_id))
        descriptor = self.load_descriptor(version_id)
        merged = descriptor.merged()

        report(InstallStage("libraries", total=len(merged.libraries)))
        self._install_libraries(merged, report)

        report(InstallStage("natives"))
        natives_dir = self.paths.versions / version_id / "natives"
        natives_dir.mkdir(parents=True, exist_ok=True)
        self._install_natives(merged, natives_dir, report)

        report(InstallStage("client"))
        client_jar = self._install_client(version_id, merged)

        asset_index_name = merged.assets
        index_path = self.paths.assets / "indexes" / f"{asset_index_name}.json"
        if include_assets:
            report(InstallStage("assets", detail=asset_index_name))
            self._install_assets(merged, report)
        elif not index_path.exists():
            self._download_asset_index(merged)

        self._install_logging_config(merged)

        classpath = classpath_entries(merged.libraries, self.paths.libraries, client_jar)
        report(InstallStage("done"))
        log.info("version %s ready (%d classpath entries)", version_id, len(classpath))
        return InstalledVersion(
            version_id=version_id,
            descriptor=merged,
            client_jar=client_jar,
            natives_dir=natives_dir,
            libraries_root=self.paths.libraries,
            assets_root=self.paths.assets,
            asset_index=asset_index_name,
            asset_index_path=index_path,
            classpath=classpath,
        )

    def load_descriptor(self, version_id: str) -> VersionDescriptor:
        """Load a version JSON, fetching or resolving its parent as needed."""
        local = self.paths.versions / version_id / f"{version_id}.json"
        if not local.exists():
            self.manifest.download_version_json(version_id, self.paths.versions)
        raw = json.loads(local.read_text(encoding="utf-8"))
        parent_id = raw.get("inheritsFrom")
        descriptor = VersionDescriptor.from_dict(raw)
        if parent_id:
            descriptor.parent = self.load_descriptor(str(parent_id))
        return descriptor

    # -- libraries ----------------------------------------------------------
    def _install_libraries(self, descriptor: VersionDescriptor, report: StageCallback) -> None:
        specs: list[DownloadSpec] = []
        for library in descriptor.libraries:
            if not library.is_allowed(platform_key=self.platform_key):
                continue
            artifact = library.resolved_artifact()
            if artifact is None:
                continue
            specs.append(
                DownloadSpec(
                    url=artifact.url,
                    destination=self.paths.libraries / artifact.path,
                    sha1=artifact.sha1,
                    size=artifact.size,
                    category="library",
                )
            )
        report(InstallStage("libraries", completed=0, total=len(specs)))
        if specs:
            self.downloader.run(specs)
        report(InstallStage("libraries", completed=len(specs), total=len(specs)))

    def _install_natives(
        self, descriptor: VersionDescriptor, natives_dir: Path, report: StageCallback
    ) -> None:
        pairs: list[tuple[Library, DownloadSpec]] = []
        for library in descriptor.native_libraries(self.platform_key):
            artifact = library.native_artifact(self.platform_key)
            if artifact is None:
                log.debug("no native artifact for %s on %s", library.name, self.platform_key)
                continue
            pairs.append(
                (
                    library,
                    DownloadSpec(
                        url=artifact.url,
                        destination=self.paths.libraries / artifact.path,
                        sha1=artifact.sha1,
                        size=artifact.size,
                        category="native",
                    ),
                )
            )
        if not pairs:
            return
        self.downloader.run([spec for _, spec in pairs])
        for library, spec in pairs:
            self._extract_native(spec.destination, natives_dir, library.extract)
        report(InstallStage("natives", completed=len(pairs), total=len(pairs)))

    @staticmethod
    def _extract_native(archive: Path, destination: Path, excludes: Iterable[str]) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename
                    if any(name.startswith(prefix) for prefix in excludes):
                        continue
                    if "/" in name or "\\" in name:
                        # Natives live at the archive root; nested entries are
                        # documentation or metadata and must not be unpacked.
                        continue
                    if name.startswith("/") or ".." in name:
                        raise PermissionError_(f"native archive member is unsafe: {name}")
                    target = destination / name
                    with zf.open(info) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"corrupt native archive: {archive.name}", detail=str(exc)) from exc

    # -- client -------------------------------------------------------------
    def _install_client(self, version_id: str, descriptor: VersionDescriptor) -> Path:
        artifact = descriptor.client_download()
        if artifact is None:
            raise ConfigError(f"version {version_id} declares no client download")
        target = self.paths.versions / version_id / f"{version_id}.jar"
        if target.exists() and (not artifact.sha1 or _sha1(target) == artifact.sha1):
            return target
        self.downloader.run(
            [
                DownloadSpec(
                    url=artifact.url,
                    destination=target,
                    sha1=artifact.sha1,
                    size=artifact.size,
                    category="client",
                )
            ]
        )
        return target

    # -- assets -------------------------------------------------------------
    def _download_asset_index(self, descriptor: VersionDescriptor) -> Path:
        index = descriptor.asset_index or {}
        url = index.get("url")
        if not url:
            raise ConfigError("version JSON has no asset index URL")
        target = self.paths.assets / "indexes" / f"{descriptor.assets}.json"
        self.downloader.run(
            [
                DownloadSpec(
                    url=str(url),
                    destination=target,
                    sha1=str(index.get("sha1") or ""),
                    size=int(index.get("size") or 0),
                    category="asset-index",
                )
            ]
        )
        return target

    def _install_assets(self, descriptor: VersionDescriptor, report: StageCallback) -> None:
        index_path = self._download_asset_index(descriptor)
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"cannot read asset index {index_path}", detail=str(exc)) from exc

        objects_root = self.paths.assets / "objects"
        specs: list[DownloadSpec] = []
        seen: set[str] = set()
        for _name, entry in (payload.get("objects") or {}).items():
            digest = str(entry.get("hash") or "")
            if not digest or digest in seen:
                continue
            seen.add(digest)
            specs.append(
                DownloadSpec(
                    url=resource_url(digest),
                    destination=objects_root / digest[:2] / digest,
                    sha1=digest,
                    size=int(entry.get("size") or 0),
                    category="asset",
                )
            )
        report(InstallStage("assets", detail=descriptor.assets, total=len(specs)))
        # Assets are numerous; chunking keeps progress meaningful and memory flat.
        chunk = 400
        for start in range(0, len(specs), chunk):
            batch = specs[start : start + chunk]
            self.downloader.run(batch)
            report(
                InstallStage(
                    "assets",
                    detail=descriptor.assets,
                    completed=min(len(specs), start + len(batch)),
                    total=len(specs),
                )
            )

    def _install_logging_config(self, descriptor: VersionDescriptor) -> None:
        artifact = descriptor.logging_config()
        if artifact is None:
            return
        target = self.paths.assets / "log_configs" / Path(artifact.path).name
        try:
            self.downloader.run(
                [
                    DownloadSpec(
                        url=artifact.url,
                        destination=target,
                        sha1=artifact.sha1,
                        size=artifact.size,
                        category="log-config",
                    )
                ]
            )
        except Exception as exc:
            log.warning("could not install the vanilla log config: %s", exc)

    # -- verification -------------------------------------------------------
    def verify(self, version_id: str) -> list[str]:
        """Return a list of problems (empty when the installation is intact)."""
        problems: list[str] = []
        try:
            descriptor = self.load_descriptor(version_id).merged()
        except Exception as exc:
            return [f"version metadata: {exc}"]

        client = descriptor.client_download()
        if client is not None:
            target = self.paths.versions / version_id / f"{version_id}.jar"
            if not target.exists():
                problems.append(f"missing client jar: {target.name}")
            elif client.sha1 and _sha1(target) != client.sha1:
                problems.append("client jar checksum mismatch")

        for library in descriptor.libraries:
            if not library.is_allowed(platform_key=self.platform_key):
                continue
            artifact = library.resolved_artifact()
            if artifact is None:
                continue
            path = self.paths.libraries / artifact.path
            if not path.exists():
                problems.append(f"missing library: {library.name}")
            elif artifact.sha1 and _sha1(path) != artifact.sha1:
                problems.append(f"library checksum mismatch: {library.name}")

        index = self.paths.assets / "indexes" / f"{descriptor.assets}.json"
        if not index.exists():
            problems.append("missing asset index")
        return problems


def _sha1(path: Path) -> str:
    import hashlib

    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def library_specs(libraries: Sequence[Library], libraries_root: Path) -> list[DownloadSpec]:
    """Exposed for tests: turn libraries into download specs."""
    specs: list[DownloadSpec] = []
    for library in libraries:
        artifact = library.resolved_artifact()
        if artifact is None:
            continue
        specs.append(
            DownloadSpec(
                url=artifact.url,
                destination=libraries_root / artifact.path,
                sha1=artifact.sha1,
                size=artifact.size,
                category="library",
            )
        )
    return specs
