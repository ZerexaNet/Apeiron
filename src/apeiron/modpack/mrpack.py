"""Modrinth ``.mrpack`` support (import and export).

Format reference: ``modrinth.index.json`` at the archive root, plus optional
``overrides/`` and ``client-overrides/`` trees.  Every file entry carries SHA-1
and SHA-512 hashes and one or more download URLs; Apeiron verifies SHA-1 (and
SHA-512 when present) before a file lands in an instance.

Export writes a spec-compliant pack containing only files whose download URL is
known, so the result stays redistributable: mod binaries are referenced, never
re-hosted.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from apeiron.core.archive import extract_zip, is_within, safe_source_file
from apeiron.core.errors import ModpackError, SecurityError
from apeiron.core.logging import get_logger
from apeiron.net.downloader import DownloadManager, DownloadSpec

log = get_logger("modpack.mrpack")

INDEX_NAME = "modrinth.index.json"
FORMAT_VERSION = 1
OVERRIDE_DIRS = ("overrides", "client-overrides")
ALLOWED_SIDES = ("client", "server", "both")


@dataclass
class MrpackFile:
    path: str
    downloads: list[str]
    sha1: str = ""
    sha512: str = ""
    size: int = 0
    side: str = "both"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MrpackFile:
        path = str(raw.get("path") or "")
        hashes = raw.get("hashes") or {}
        downloads = [str(u) for u in (raw.get("downloads") or [])]
        if not path:
            raise ModpackError("a modpack file entry has no path")
        if not downloads:
            raise ModpackError(f"modpack file {path} has no download URL")
        for algorithm, length in (("sha1", 40), ("sha512", 128)):
            digest = hashes.get(algorithm)
            if digest and (
                not isinstance(digest, str)
                or len(digest) != length
                or any(c not in "0123456789abcdefABCDEF" for c in digest)
            ):
                raise ModpackError(f"invalid {algorithm} digest for {path}")
        if not hashes.get("sha1") and not hashes.get("sha512"):
            raise ModpackError(f"modpack file {path} declares no checksum")
        env = raw.get("env") or {}
        side = str(env.get("client", "required"))
        return cls(
            path=path,
            downloads=downloads,
            sha1=str(hashes.get("sha1") or ""),
            sha512=str(hashes.get("sha512") or ""),
            size=int(raw.get("fileSize") or 0),
            side="both" if side != "unsupported" else "server",
        )

    def is_safe_path(self) -> bool:
        pure = PurePosixPath(self.path)
        if pure.is_absolute() or ".." in pure.parts or "\\" in self.path or ":" in self.path:
            return False
        # Modrinth paths are relative to the instance root.
        return bool(pure.parts)


@dataclass
class MrpackIndex:
    name: str
    version_id: str
    mc_version: str
    loader: str = "vanilla"
    loader_version: str = ""
    summary: str = ""
    files: list[MrpackFile] = field(default_factory=list)
    dependencies: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MrpackIndex:
        if int(raw.get("formatVersion", 0)) != FORMAT_VERSION:
            raise ModpackError(f"unsupported Modrinth pack format: {raw.get('formatVersion')}")
        dependencies = {str(k): str(v) for k, v in (raw.get("dependencies") or {}).items()}
        mc_version = dependencies.get("minecraft", "")
        if not mc_version:
            raise ModpackError("the modpack does not declare a Minecraft version")
        loader, loader_version = resolve_loader(dependencies)
        files = [MrpackFile.from_dict(entry) for entry in raw.get("files") or []]
        for entry in files:
            if not entry.is_safe_path():
                raise SecurityError(f"modpack file path is unsafe: {entry.path}")
        return cls(
            name=str(raw.get("name") or "Imported modpack"),
            version_id=str(raw.get("versionId") or ""),
            mc_version=mc_version,
            loader=loader,
            loader_version=loader_version,
            summary=str(raw.get("summary") or ""),
            files=files,
            dependencies=dependencies,
        )


LOADER_KEYS = {
    "fabric-loader": "fabric",
    "quilt-loader": "quilt",
    "forge": "forge",
    "neoforge": "neoforge",
}


def resolve_loader(dependencies: dict[str, str]) -> tuple[str, str]:
    """Map Modrinth dependency keys onto Apeiron loader ids."""
    for key, loader in LOADER_KEYS.items():
        if dependencies.get(key):
            return loader, dependencies[key]
    return "vanilla", ""


@dataclass
class MrpackContents:
    index: MrpackIndex
    archive: Path


def read_mrpack(archive: Path) -> MrpackContents:
    if not archive.exists():
        raise ModpackError(f"modpack not found: {archive}")
    try:
        with zipfile.ZipFile(archive) as zf:
            try:
                raw = json.loads(zf.read(INDEX_NAME).decode("utf-8"))
            except KeyError as exc:
                raise ModpackError(
                    f"{archive.name} is not a Modrinth pack (no {INDEX_NAME})"
                ) from exc
            except json.JSONDecodeError as exc:
                raise ModpackError("the modpack index is not valid JSON", detail=str(exc)) from exc
            index = MrpackIndex.from_dict(raw)
    except zipfile.BadZipFile as exc:
        raise ModpackError(f"{archive.name} is not a valid zip archive", detail=str(exc)) from exc
    return MrpackContents(index=index, archive=archive)


def extract_overrides(archive: Path, instance_dir: Path) -> int:
    """Copy override trees into the instance, rejecting path traversal."""
    return len(extract_zip(archive, instance_dir, prefixes=OVERRIDE_DIRS))


def download_specs(index: MrpackIndex, instance_dir: Path) -> list[DownloadSpec]:
    """Build verified download specs for every client-relevant file."""
    specs: list[DownloadSpec] = []
    for entry in index.files:
        if entry.side == "server":
            continue
        destination = (instance_dir / entry.path).resolve()
        if not is_within(destination, instance_dir.resolve()):
            raise SecurityError(f"modpack file path escapes the instance: {entry.path}")
        specs.append(
            DownloadSpec(
                url=entry.downloads[0],
                destination=destination,
                sha1=entry.sha1,
                sha512=entry.sha512,
                fallback_urls=tuple(entry.downloads[1:]),
                size=entry.size,
                category="modpack",
            )
        )
    return specs


def install_files(contents: MrpackContents, instance_dir: Path, downloader: DownloadManager) -> int:
    specs = download_specs(contents.index, instance_dir)
    if specs:
        downloader.run(specs)
    return len(specs)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_mrpack(
    *,
    target: Path,
    name: str,
    version_id: str,
    mc_version: str,
    loader: str,
    loader_version: str,
    files: Iterable[dict[str, Any]],
    summary: str = "",
    overrides_dir: Path | None = None,
    overrides_prefix: str = "",
) -> Path:
    """Write a ``.mrpack`` from already-known file metadata."""
    dependencies: dict[str, str] = {"minecraft": mc_version}
    for key, value in LOADER_KEYS.items():
        if value == loader and loader_version:
            dependencies[key] = loader_version
            break

    index_files: list[dict[str, Any]] = [f for f in files if f.get("downloads")]
    index: dict[str, Any] = {
        "formatVersion": FORMAT_VERSION,
        "game": "minecraft",
        "versionId": version_id,
        "name": name,
        "summary": summary,
        "files": index_files,
        "dependencies": dependencies,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(INDEX_NAME, json.dumps(index, indent=2, ensure_ascii=False))
        if overrides_dir and overrides_dir.exists():
            for path in sorted(overrides_dir.rglob("*")):
                if safe_source_file(path, overrides_dir):
                    arcname = (
                        PurePosixPath("overrides")
                        / overrides_prefix
                        / PurePosixPath(path.relative_to(overrides_dir).as_posix())
                    )
                    zf.write(path, str(arcname))
    log.info("exported modpack %s (%d files)", target.name, len(index["files"]))
    return target
