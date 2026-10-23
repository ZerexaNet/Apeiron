"""Windows Bedrock Edition adapter.

Design constraints (verified against Microsoft's public documentation and the
absence of any supported launcher API):

* Bedrock is a Microsoft Store (MSIX/UWP) application.  It is **not** started by
  building a command line like Java Edition; the only supported entry points are
  the ``shell:appsFolder`` activation and the ``minecraft:`` URI protocol.
* Both entry points hand control to Windows, which enforces the Store licence
  and Xbox Live sign-in.  Apeiron therefore never patches, repackages or
  re-signs game files, and never attempts to bypass entitlement checks.
* There is no public API to query or control a running Bedrock session; process
  presence is observed via ``tasklist`` only for status display.

Consequently Apeiron offers detection, version display and activation on
Windows, and clearly reports that Bedrock is unsupported elsewhere.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from apeiron.core.errors import BedrockError, UnsupportedError
from apeiron.core.logging import get_logger

log = get_logger("bedrock")

STORE_PACKAGE = "Microsoft.MinecraftUWP"
PREVIEW_PACKAGE = "Microsoft.MinecraftWindowsBeta"
EDUCATION_PACKAGE = "Microsoft.MinecraftEducationEdition"
KNOWN_PACKAGES = (STORE_PACKAGE, PREVIEW_PACKAGE, EDUCATION_PACKAGE)
PROCESS_NAMES = ("Minecraft.Windows.exe", "MinecraftEducationEdition.exe")
LAUNCH_TIMEOUT = 60

PowerShellRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class BedrockInstallation:
    package_name: str
    family_name: str
    version: str
    install_location: str = ""
    channel: str = "release"

    @property
    def app_id(self) -> str:
        """Application id used by ``shell:appsFolder``."""
        return "App"

    @property
    def activation_uri(self) -> str:
        return f"shell:appsFolder\\{self.family_name}!{self.app_id}"

    def as_dict(self) -> dict[str, str]:
        return {
            "packageName": self.package_name,
            "familyName": self.family_name,
            "version": self.version,
            "installLocation": self.install_location,
            "channel": self.channel,
            "activationUri": self.activation_uri,
        }


def parse_appx_packages(raw: str) -> list[BedrockInstallation]:
    """Parse ``Get-AppxPackage | ConvertTo-Json`` output defensively."""
    text = (raw or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        log.warning("could not parse Appx package list")
        return []
    if isinstance(payload, dict):
        payload = [payload]
    installations: list[BedrockInstallation] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        family = str(entry.get("PackageFamilyName") or "")
        name = str(entry.get("Name") or "")
        if not family:
            continue
        channel = (
            "preview"
            if name == PREVIEW_PACKAGE
            else "education"
            if name == EDUCATION_PACKAGE
            else "release"
        )
        installations.append(
            BedrockInstallation(
                package_name=name,
                family_name=family,
                version=str(entry.get("Version") or ""),
                install_location=str(entry.get("InstallLocation") or ""),
                channel=channel,
            )
        )
    return installations


class WindowsBedrockAdapter:
    """Detection and activation for Minecraft Bedrock on Windows."""

    platform = "windows"

    def __init__(self, runner: PowerShellRunner | None = None) -> None:
        self._runner = runner or _run_powershell

    @property
    def supported(self) -> bool:
        return sys.platform.startswith("win") and self._runner is not None

    def _require_support(self) -> None:
        if not sys.platform.startswith("win"):
            raise UnsupportedError(
                "Minecraft Bedrock Edition is only available on Windows",
                hint="On macOS and Linux, Apeiron launches Minecraft: Java Edition only.",
            )

    # -- detection ----------------------------------------------------------
    def detect(self) -> list[BedrockInstallation]:
        if not sys.platform.startswith("win"):
            return []
        script = (
            "$pkgs = Get-AppxPackage | Where-Object { $_.Name -match "
            "'MinecraftUWP|MinecraftWindowsBeta|MinecraftEducationEdition' }; "
            "if ($pkgs) { $pkgs | Select-Object Name,PackageFamilyName,Version,InstallLocation "
            "| ConvertTo-Json -Compress }"
        )
        completed = self._runner(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
        )
        if completed.returncode != 0:
            log.warning("Appx query failed: %s", (completed.stderr or "").strip()[:300])
            return []
        return parse_appx_packages(completed.stdout)

    def is_installed(self) -> bool:
        return bool(self.detect())

    # -- launching ----------------------------------------------------------
    def launch(self, installation: BedrockInstallation | None = None) -> str:
        """Activate Bedrock through Windows; returns the URI that was used.

        Raises :class:`BedrockError` when the game is missing, so the UI can
        offer installation guidance instead of a silent no-op.
        """
        self._require_support()
        target = installation
        if target is None:
            installations = self.detect()
            if not installations:
                raise BedrockError(
                    "Minecraft Bedrock Edition is not installed",
                    hint="Install it from the Microsoft Store, then refresh this page.",
                )
            target = installations[0]
        uri = target.activation_uri
        completed = self._runner(["explorer.exe", uri])
        # explorer.exe returns 1 even on success; only a hard failure is fatal.
        if completed.returncode not in (0, 1):
            raise BedrockError(
                "Windows could not activate Minecraft Bedrock",
                detail=(completed.stderr or "").strip()[:300],
            )
        log.info("requested Bedrock activation via %s", uri)
        return uri

    # -- process state ------------------------------------------------------
    def is_running(self) -> bool:
        if not sys.platform.startswith("win"):
            return False
        completed = self._runner(["tasklist", "/FO", "CSV", "/NH"])
        if completed.returncode != 0:
            return False
        output = (completed.stdout or "").lower()
        return any(name.lower() in output for name in PROCESS_NAMES)

    def status(self) -> dict[str, object]:
        if not sys.platform.startswith("win"):
            return {"supported": False, "installed": False, "installations": [], "running": False}
        installations = self.detect()
        return {
            "supported": True,
            "installed": bool(installations),
            "installations": [i.as_dict() for i in installations],
            "running": self.is_running(),
        }


def _run_powershell(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(args), capture_output=True, text=True, timeout=LAUNCH_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - Windows only
        log.error("cannot run %s: %s", args[0] if args else "command", exc)
        return subprocess.CompletedProcess(list(args), 1, "", str(exc))
