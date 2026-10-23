"""Forge and NeoForge installation through their official installer jars.

Neither project publishes a complete "profile JSON" like Fabric does: from
1.13 onwards both require a set of *processors* to patch the vanilla jar, and
the only supported way to perform that is the installer published by the
project itself.  Apeiron therefore:

1. resolves the installer from the project's official Maven repository;
2. **verifies its SHA-1 against the checksum published next to it** and refuses
   to run an unverified executable;
3. runs it with ``--installClient`` inside an isolated temporary launcher
   directory, with a hard timeout and captured output;
4. imports the generated version JSON, libraries and patched client into the
   Apeiron layout.

Because this executes third-party code, :meth:`install` requires
``allow_execution=True``; the UI only sets it after an explicit confirmation
dialog, and the consent text names the exact URL and checksum.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from apeiron.core.errors import LoaderError, NetworkError, PermissionError_
from apeiron.core.logging import get_logger
from apeiron.core.paths import AppPaths
from apeiron.loaders.base import LoaderInstallResult, LoaderVersion, write_version_json
from apeiron.mc.manifest import VersionManifest

log = get_logger("forge")

FORGE_MAVEN = "https://maven.minecraftforge.net"
FORGE_PROMOTIONS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases"
INSTALL_TIMEOUT = 900


class _InstallerJarLoader:
    name = "forge"
    maven_base = ""
    group_path = ""
    artifact = ""

    def __init__(
        self, http, paths: AppPaths, manifest: VersionManifest | None = None, java_path: str = ""
    ) -> None:
        self.http = http
        self.paths = paths
        self.manifest = manifest
        self.java_path = java_path

    # -- coordinates --------------------------------------------------------
    def installer_coordinates(self, mc_version: str, loader_version: str) -> tuple[str, str]:
        """Return (relative maven path, version id used by the installer)."""
        raise NotImplementedError

    def installer_url(self, mc_version: str, loader_version: str) -> str:
        relative, _ = self.installer_coordinates(mc_version, loader_version)
        return f"{self.maven_base}/{relative}"

    # -- versions -----------------------------------------------------------
    def versions(self, mc_version: str) -> list[LoaderVersion]:
        raise NotImplementedError

    # -- install ------------------------------------------------------------
    def install(
        self,
        mc_version: str,
        loader_version: str,
        *,
        java_path: str = "",
        allow_execution: bool = False,
    ) -> LoaderInstallResult:
        if not allow_execution:
            raise PermissionError_(
                f"installing {self.name} runs the official {self.name} installer",
                hint=(
                    "Apeiron must execute the vendor-provided installer to patch the game jar. "
                    "Confirm the action to continue; the download is verified against the "
                    "checksum published by the project."
                ),
            )
        java = java_path or self.java_path
        if not java:
            raise LoaderError(f"a Java runtime is required to run the {self.name} installer")

        url = self.installer_url(mc_version, loader_version)
        checksum = self._fetch_checksum(url + ".sha1")
        if not checksum:
            raise LoaderError(
                f"no checksum is published for {url}; refusing to execute an unverified installer"
            )

        with tempfile.TemporaryDirectory(prefix=f"apeiron-{self.name}-") as tmp:
            work = Path(tmp)
            installer = work / "installer.jar"
            self._download(url, installer)
            actual = _sha1(installer)
            if actual.lower() != checksum.lower():
                raise LoaderError(
                    f"{self.name} installer checksum mismatch",
                    detail=f"expected {checksum}, got {actual}",
                )

            launcher_dir = work / "launcher"
            self._seed_vanilla(mc_version, launcher_dir)
            self._run_installer(java, installer, launcher_dir)

            generated = self._locate_generated_version(launcher_dir, mc_version)
            version_id = generated.name
            payload = self._read_json(generated / f"{version_id}.json")
            result = LoaderInstallResult(
                version_id=version_id,
                mc_version=mc_version,
                loader=self.name,
                loader_version=loader_version,
                json_path=write_version_json(self.paths, version_id, payload),
                libraries_added=len(payload.get("libraries") or []),
            )
            self._import_artifacts(launcher_dir, version_id)
            log.info(
                "installed %s %s for MC %s (version id %s)",
                self.name,
                loader_version,
                mc_version,
                version_id,
            )
            return result

    def _fetch_checksum(self, url: str) -> str:
        try:
            body = self.http.get_bytes(url, max_bytes=4096)
        except NetworkError:
            return ""
        text = body.decode("ascii", errors="ignore").strip()
        candidate = text.split()[0] if text else ""
        return candidate.lower() if len(candidate) == 40 else ""

    def _download(self, url: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".part")
        with tmp.open("wb") as handle:
            for chunk in self.http.stream(url):
                handle.write(chunk)
        tmp.replace(target)

    def _seed_vanilla(self, mc_version: str, launcher_dir: Path) -> None:
        """Installers require the vanilla version JSON to be present."""
        version_dir = launcher_dir / "versions" / mc_version
        version_dir.mkdir(parents=True, exist_ok=True)
        local = self.paths.versions / mc_version / f"{mc_version}.json"
        if local.exists():
            shutil.copy2(local, version_dir / f"{mc_version}.json")
        else:
            if self.manifest is not None:
                self.manifest.download_version_json(mc_version, launcher_dir / "versions")

    def _run_installer(self, java: str, installer: Path, launcher_dir: Path) -> None:
        command = [java, "-jar", str(installer), "--installClient", str(launcher_dir)]
        log.info("running %s installer (java=%s)", self.name, java)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=INSTALL_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LoaderError(f"the {self.name} installer timed out") from exc
        except OSError as exc:
            raise LoaderError(f"cannot start the {self.name} installer", detail=str(exc)) from exc
        if completed.returncode != 0:
            tail = (completed.stdout or "")[-2000:] + (completed.stderr or "")[-2000:]
            raise LoaderError(
                f"the {self.name} installer failed with exit code {completed.returncode}",
                detail=tail,
            )

    def _locate_generated_version(self, launcher_dir: Path, mc_version: str) -> Path:
        versions_dir = launcher_dir / "versions"
        candidates = (
            [
                path
                for path in versions_dir.iterdir()
                if path.is_dir()
                and (path / f"{path.name}.json").exists()
                and path.name != mc_version
            ]
            if versions_dir.exists()
            else []
        )
        if not candidates:
            raise LoaderError(f"the {self.name} installer produced no version JSON")
        generated = max(candidates, key=lambda p: p.stat().st_mtime)
        payload = self._read_json(generated / f"{generated.name}.json")
        if str(payload.get("inheritsFrom")) != mc_version:
            raise LoaderError(
                f"the generated version inherits from {payload.get('inheritsFrom')}, "
                f"expected {mc_version}"
            )
        return generated

    def _import_artifacts(self, launcher_dir: Path, version_id: str) -> None:
        source = launcher_dir / "versions" / version_id
        target = self.paths.versions / version_id
        target.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            destination = target / item.name
            if item.is_file() and not destination.exists():
                shutil.copy2(item, destination)

        # Libraries already downloaded by the installer are kept, which avoids
        # re-downloading several hundred megabytes on slow connections.
        source_libraries = launcher_dir / "libraries"
        if source_libraries.exists():
            for item in source_libraries.rglob("*"):
                if not item.is_file():
                    continue
                destination = self.paths.libraries / item.relative_to(source_libraries)
                if not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(item, destination)
                    except OSError:
                        continue

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        import json

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LoaderError(f"cannot read {path.name}", detail=str(exc)) from exc
        if not isinstance(payload, dict):
            raise LoaderError(f"{path.name} is not a JSON object")
        return payload


class ForgeLoader(_InstallerJarLoader):
    name = "forge"
    maven_base = FORGE_MAVEN

    def installer_coordinates(self, mc_version: str, loader_version: str) -> tuple[str, str]:
        combined = f"{mc_version}-{loader_version}"
        return (
            f"net/minecraftforge/forge/{combined}/forge-{combined}-installer.jar",
            combined,
        )

    def versions(self, mc_version: str) -> list[LoaderVersion]:
        try:
            payload = self.http.get_json(FORGE_PROMOTIONS)
        except NetworkError as exc:
            raise LoaderError("cannot load the Forge version list", detail=exc.message) from exc
        promos: dict[str, str] = payload.get("promos") or {}
        versions: dict[str, bool] = {}
        for key, value in promos.items():
            if not key.startswith(f"{mc_version}-"):
                continue
            channel = key.split("-", 1)[1]
            if channel in ("latest", "recommended"):
                versions[str(value)] = channel == "recommended" or versions.get(str(value), False)
        return [
            LoaderVersion(
                loader=self.name,
                version=version,
                mc_version=mc_version,
                stable=stable,
                label=f"Forge {version}" + (" (recommended)" if stable else ""),
            )
            for version, stable in sorted(versions.items(), reverse=True)
        ]


class NeoForgeLoader(_InstallerJarLoader):
    name = "neoforge"
    maven_base = NEOFORGE_MAVEN

    def installer_coordinates(self, mc_version: str, loader_version: str) -> tuple[str, str]:
        return (
            f"net/neoforged/neoforge/{loader_version}/neoforge-{loader_version}-installer.jar",
            f"neoforge-{loader_version}",
        )

    def versions(self, mc_version: str) -> list[LoaderVersion]:
        url = f"{self.maven_base}/net/neoforged/neoforge/maven-metadata.xml"
        try:
            raw = self.http.get_bytes(url, max_bytes=8 * 1024 * 1024)
        except NetworkError as exc:
            raise LoaderError("cannot load the NeoForge version list", detail=exc.message) from exc
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise LoaderError("the NeoForge maven metadata is malformed") from exc
        all_versions = [node.text.strip() for node in root.iter("version") if node.text]
        # NeoForge 20.4.x targets MC 1.20.4, 21.1.x targets 1.21.1, ...
        parts = mc_version.split(".")
        prefix = f"{parts[1]}.{parts[2]}" if len(parts) >= 3 else ""
        matching = [v for v in all_versions if prefix and v.startswith(prefix + ".")]
        return [
            LoaderVersion(loader=self.name, version=version, mc_version=mc_version)
            for version in sorted(matching, reverse=True)
        ]


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
