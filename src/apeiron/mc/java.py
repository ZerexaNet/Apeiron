"""Java runtime discovery, validation and provisioning.

Apeiron never assumes ``java`` on PATH is usable: every candidate is executed
with ``-version`` and its reported major version is parsed.  When no suitable
runtime exists the launcher can fetch an Eclipse Temurin build from the
official Adoptium API, which publishes SHA-256 checksums for every package.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apeiron.core.errors import JavaError, PermissionError_
from apeiron.core.http import HttpClient
from apeiron.core.logging import get_logger
from apeiron.core.paths import current_platform_key

log = get_logger("java")

ADOPTIUM_LATEST = "https://api.adoptium.net/v3/assets/latest/{major}/hotspot"
ADOPTIUM_AVAILABLE = "https://api.adoptium.net/v3/info/available_releases"
VERSION_RE = re.compile(r'version "([^"]+)"')
LEGACY_VERSION_RE = re.compile(r"version (\S+)")
SUPPORTED_MAJORS = (8, 11, 16, 17, 21, 22, 23, 24, 25)


@dataclass(frozen=True)
class JavaRuntime:
    path: Path  # path to the java executable
    major: int
    version: str
    vendor: str = ""
    arch: str = ""
    managed: bool = False

    @property
    def home(self) -> Path:
        return self.path.parent.parent

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "major": self.major,
            "version": self.version,
            "vendor": self.vendor,
            "arch": self.arch,
            "managed": self.managed,
        }


def parse_java_version(output: str) -> tuple[int, str]:
    """Parse ``java -version`` output into (major, full_version).

    Handles modern (``17.0.9``), legacy (``1.8.0_392``) and early-access
    (``21-ea``) strings, and vendor prefixes such as ``OpenJDK 64-Bit``.
    """
    match = VERSION_RE.search(output) or LEGACY_VERSION_RE.search(output)
    if not match:
        raise JavaError("could not parse the Java version output", detail=output[:500])
    raw = match.group(1).strip()
    parts = raw.split(".")
    try:
        if parts[0] == "1" and len(parts) > 1:
            major = int(re.sub(r"\D.*", "", parts[1]))
        else:
            major = int(re.sub(r"\D.*", "", parts[0]))
    except ValueError as exc:
        raise JavaError(f"unrecognised Java version: {raw}") from exc
    return major, raw


def probe_java(executable: Path, *, timeout: float = 15.0) -> JavaRuntime:
    """Run ``java -version`` and build a runtime descriptor."""
    if not executable.exists():
        raise JavaError(f"Java executable not found: {executable}")
    try:
        completed = subprocess.run(
            [str(executable), "-version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise JavaError(f"cannot execute {executable}", detail=str(exc)) from exc
    output = (completed.stderr or "") + (completed.stdout or "")
    if completed.returncode != 0 and not output:
        raise JavaError(
            f"{executable} exited with code {completed.returncode}",
            detail="no version output",
        )
    major, version = parse_java_version(output)
    vendor = ""
    for candidate in (
        "Temurin",
        "AdoptOpenJDK",
        "OpenJDK",
        "Oracle",
        "Microsoft",
        "Zulu",
        "GraalVM",
    ):
        if candidate.lower() in output.lower():
            vendor = candidate
            break
    match = re.search(r"(\d+)-Bit", output)
    arch = f"x{match.group(1)}" if match else platform.machine()
    return JavaRuntime(path=executable, major=major, version=version, vendor=vendor, arch=arch)


def java_executable_name() -> str:
    return "java.exe" if os.name == "nt" else "java"


def candidate_paths() -> Iterable[Path]:
    """Common installation locations, in priority order."""
    exe = java_executable_name()
    seen: set[Path] = set()

    def emit(path: Path) -> Iterable[Path]:
        resolved = path
        try:
            resolved = path.resolve()
        except OSError:
            pass
        if resolved not in seen and resolved.is_file():
            seen.add(resolved)
            return [resolved]
        return []

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        yield from emit(Path(java_home) / "bin" / exe)

    found = shutil.which("java")
    if found:
        yield from emit(Path(found))

    home = Path.home()
    platform_key = current_platform_key()
    roots: list[Path] = []
    if platform_key == "windows":
        for base in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if base:
                roots.extend(
                    [
                        Path(base) / "Java",
                        Path(base) / "Eclipse Adoptium",
                        Path(base) / "Microsoft",
                        Path(base) / "Zulu",
                    ]
                )
    elif platform_key == "macos":
        roots.extend(
            [
                Path("/Library/Java/JavaVirtualMachines"),
                home / "Library" / "Java" / "JavaVirtualMachines",
                Path("/opt/homebrew/opt"),
                Path("/usr/local/opt"),
            ]
        )
    else:
        roots.extend(
            [
                Path("/usr/lib/jvm"),
                Path("/usr/java"),
                Path("/opt/java"),
                home / ".jdks",
                home / ".sdkman" / "candidates" / "java",
            ]
        )

    for root in roots:
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            for candidate in (
                entry / "bin" / exe,
                entry / "Contents" / "Home" / "bin" / exe,
                entry / "jre" / "bin" / exe,
            ):
                yield from emit(candidate)


def detect_runtimes(extra: Iterable[Path] = ()) -> list[JavaRuntime]:
    """Probe every discoverable runtime, de-duplicated and sorted by major."""
    runtimes: list[JavaRuntime] = []
    seen: set[Path] = set()
    for path in list(candidate_paths()) + [Path(p) for p in extra if p]:
        if path in seen:
            continue
        seen.add(path)
        try:
            runtimes.append(probe_java(path))
        except JavaError:
            continue
    runtimes.sort(key=lambda rt: (rt.major, str(rt.path)))
    return runtimes


def select_runtime(required_major: int, runtimes: Iterable[JavaRuntime]) -> JavaRuntime | None:
    """Pick the best runtime: exact major, else the lowest newer major."""
    candidates = [rt for rt in runtimes if rt.major >= max(8, required_major)]
    if not candidates:
        return None
    exact = [rt for rt in candidates if rt.major == required_major]
    if exact:
        return sorted(exact, key=lambda rt: rt.version, reverse=True)[0]
    return sorted(candidates, key=lambda rt: (rt.major, rt.version))[0]


# ---------------------------------------------------------------------------
# Managed runtimes (Adoptium Temurin)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdoptiumPackage:
    major: int
    version: str
    url: str
    checksum: str
    size: int
    name: str
    image_type: str = "jre"


def adoptium_os_name(platform_key: str | None = None) -> str:
    key = platform_key or current_platform_key()
    return {"windows": "windows", "macos": "mac", "linux": "linux"}.get(key, "linux")


def adoptium_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x64"
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    if machine in ("i386", "i686", "x86"):
        return "x86"
    return "x64"


class AdoptiumClient:
    """Resolves Temurin JRE packages from the official API."""

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def latest(self, major: int, *, image_type: str = "jre") -> AdoptiumPackage:
        params = (
            f"?architecture={adoptium_arch()}&image_type={image_type}"
            f"&os={adoptium_os_name()}&vendor=eclipse"
        )
        payload = self.http.get_json(f"{ADOPTIUM_LATEST.format(major=major)}{params}")
        if not isinstance(payload, list) or not payload:
            raise JavaError(f"no Temurin {image_type} package for Java {major}")
        entry = payload[0]
        binary = entry.get("binary") or {}
        package = binary.get("package") or {}
        if not package.get("link"):
            raise JavaError("Adoptium response contained no download link")
        return AdoptiumPackage(
            major=int(major),
            version=str(entry.get("version", {}).get("semver") or ""),
            url=str(package["link"]),
            checksum=str(package.get("checksum") or ""),
            size=int(package.get("size") or 0),
            name=str(package.get("name") or f"temurin-{major}.tar.gz"),
            image_type=image_type,
        )

    def available_majors(self) -> list[int]:
        payload = self.http.get_json(ADOPTIUM_AVAILABLE)
        return sorted(int(v) for v in payload.get("available_releases") or [])


def install_runtime(
    package: AdoptiumPackage, java_root: Path, *, download, verify_checksum: bool = True
) -> JavaRuntime:
    """Download, verify and unpack a Temurin package; return the runtime.

    ``download`` is a callable taking a URL, destination and expected SHA-256,
    which keeps this function testable without network access.
    """
    target_dir = java_root / f"temurin-{package.major}"
    archive = java_root / "downloads" / package.name
    archive.parent.mkdir(parents=True, exist_ok=True)

    if not (target_dir / ".ready").exists():
        log.info("installing Java %d from Adoptium (%s)", package.major, package.name)
        download(package.url, archive, package.checksum if verify_checksum else "")
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        if package.name.endswith(".zip"):
            _safe_extract_zip(archive, target_dir)
        else:
            _safe_extract_tar(archive, target_dir)
        (target_dir / ".ready").write_text(package.version, encoding="utf-8")
        archive.unlink(missing_ok=True)

    executable = find_java_in_tree(target_dir)
    if executable is None:
        raise JavaError(f"downloaded Java {package.major} archive contained no java executable")
    runtime = probe_java(executable)
    return JavaRuntime(
        path=runtime.path,
        major=runtime.major,
        version=runtime.version,
        vendor=runtime.vendor or "Temurin",
        arch=runtime.arch,
        managed=True,
    )


def find_java_in_tree(root: Path) -> Path | None:
    exe = java_executable_name()
    direct = [
        root / "bin" / exe,
        root / "Contents" / "Home" / "bin" / exe,
        root / "jre" / "bin" / exe,
    ]
    for candidate in direct:
        if candidate.is_file():
            return candidate
    for candidate in root.rglob(exe):
        if candidate.is_file() and candidate.parent.name == "bin":
            return candidate
    return None


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:*") as tar:
        members = tar.getmembers()
        for member in members:
            _reject_unsafe_member(member.name, destination)
            if member.issym() or member.islnk():
                link = Path(member.linkname)
                if link.is_absolute() or ".." in link.parts:
                    raise PermissionError_(f"archive contains an unsafe link: {member.name}")
        # Python 3.12+ supports a data filter which also strips absolute paths
        # and unsafe links; use it when available.
        try:
            tar.extractall(destination, filter="data")
        except TypeError:  # pragma: no cover - Python 3.10/3.11
            tar.extractall(destination)


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            _reject_unsafe_member(info.filename, destination)
            mode = info.external_attr >> 16
            if mode and (mode & 0o170000) == 0o120000:
                raise PermissionError_(f"archive contains a symlink: {info.filename}")
        zf.extractall(destination)


def _reject_unsafe_member(name: str, destination: Path) -> None:
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise PermissionError_(f"archive member has an absolute path: {name}")
    parts = Path(name).parts
    if ".." in parts:
        raise PermissionError_(f"archive member escapes the destination: {name}")
    target = (destination / name).resolve()
    root = destination.resolve()
    if root != target and root not in target.parents:
        raise PermissionError_(f"archive member escapes the destination: {name}")


def describe_environment() -> dict[str, Any]:
    return {
        "platform": current_platform_key(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "java_home": os.environ.get("JAVA_HOME", ""),
    }


def json_dump(runtime: JavaRuntime) -> str:
    return json.dumps(runtime.as_dict(), ensure_ascii=False)
