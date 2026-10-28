"""Safe archive handling shared by modpacks, instance export and backups.

Zip and tar archives are attacker-controlled input: a modpack can contain
``../../.bashrc`` or an absolute path, and extracting it naively is a remote
code execution vector.  Every extraction in Apeiron goes through this module,
which rejects absolute paths, drive letters, ``..`` components, symlinks and
hard links that point outside the destination.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

from apeiron.core.errors import ArchiveError, PermissionError_

ZIP_MAGIC = b"PK\x03\x04"
GZIP_MAGIC = b"\x1f\x8b"


def _validate_member(name: str, destination: Path) -> Path:
    if not name or name in (".", "./"):
        raise ArchiveError("archive contains an empty entry name")
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or (len(normalised) > 1 and normalised[1] == ":"):
        raise PermissionError_(f"archive entry has an absolute path: {name}")
    parts = Path(normalised).parts
    if ".." in parts:
        raise PermissionError_(f"archive entry escapes the destination: {name}")
    target = (destination / normalised).resolve()
    root = destination.resolve()
    if root != target and root not in target.parents:
        raise PermissionError_(f"archive entry escapes the destination: {name}")
    return target


def extract_zip(
    archive: Path,
    destination: Path,
    *,
    members: Iterable[str] | None = None,
    max_total_bytes: int = 8 * 1024**3,
) -> list[Path]:
    """Extract a zip safely and return the written paths."""
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    wanted = set(members) if members is not None else None
    total = 0
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if wanted is not None and info.filename not in wanted:
                    continue
                if info.is_dir():
                    continue
                target = _validate_member(info.filename, destination)
                mode = info.external_attr >> 16
                if mode and (mode & 0o170000) == 0o120000:
                    raise PermissionError_(f"archive entry is a symlink: {info.filename}")
                total += info.file_size
                if total > max_total_bytes:
                    raise ArchiveError(
                        f"archive expands to more than {max_total_bytes} bytes; refusing to extract"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                written.append(target)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a valid zip archive: {archive.name}", detail=str(exc)) from exc
    return written


def extract_tar(archive: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    try:
        with tarfile.open(archive, "r:*") as tar:
            for member in tar.getmembers():
                if member.isdir():
                    continue
                _validate_member(member.name, destination)
                if member.issym() or member.islnk():
                    raise PermissionError_(f"archive entry is a link: {member.name}")
            tar.extractall(destination, filter="data")
            written = [destination / m.name for m in tar.getmembers() if m.isfile()]
    except (tarfile.TarError, OSError) as exc:
        if isinstance(exc, PermissionError_):
            raise
        raise ArchiveError(f"not a valid tar archive: {archive.name}", detail=str(exc)) from exc
    return written


def extract_any(archive: Path, destination: Path) -> list[Path]:
    with archive.open("rb") as handle:
        head = handle.read(4)
    if head.startswith(ZIP_MAGIC):
        return extract_zip(archive, destination)
    if head.startswith(GZIP_MAGIC):
        return extract_tar(archive, destination)
    # Some tar files are not compressed at all.
    try:
        return extract_tar(archive, destination)
    except ArchiveError as exc:
        raise ArchiveError(f"unsupported archive format: {archive.name}", detail=str(exc)) from exc


def zip_directory(
    source: Path,
    target: Path,
    *,
    exclude: Iterable[str] = (),
    arc_prefix: str = "",
) -> Path:
    """Create a deterministic zip of ``source`` (sorted, no timestamps churn)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    excluded = {e.strip("/") for e in exclude}
    tmp = target.with_suffix(target.suffix + ".part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            if any(relative == e or relative.startswith(e + "/") for e in excluded):
                continue
            arcname = f"{arc_prefix}{relative}" if arc_prefix else relative
            zf.write(path, arcname)
    os.replace(tmp, target)
    return target


def is_within(path: Path, parent: Path) -> bool:
    """True when ``path`` is ``parent`` or inside it (both resolved)."""
    try:
        resolved = path.resolve()
        root = parent.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents
