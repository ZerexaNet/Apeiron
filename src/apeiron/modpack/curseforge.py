"""CurseForge modpack support.

CurseForge does not permit third-party downloads without an API key, and its
terms restrict automated distribution.  Apeiron therefore:

* imports the *manifest* of a CurseForge pack locally (no key needed to read a
  zip the user already has);
* resolves each file through the official API **only when the user has
  configured their own API key**, and
* never mirrors or re-hosts CurseForge files.

Without a key the import fails with an explicit, actionable message instead of
silently downloading from an unauthorised mirror.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apeiron.core.archive import extract_zip, is_within
from apeiron.core.errors import ModpackError, SecurityError
from apeiron.core.http import HttpClient
from apeiron.core.logging import get_logger
from apeiron.net.downloader import DownloadManager, DownloadSpec

log = get_logger("modpack.curseforge")

MANIFEST_NAME = "manifest.json"
API_BASE = "https://api.curseforge.com/v1"


@dataclass
class CurseForgeFile:
    project_id: int
    file_id: int
    required: bool = True


@dataclass
class CurseForgeManifest:
    name: str
    version: str
    mc_version: str
    loader: str
    loader_version: str
    files: list[CurseForgeFile] = field(default_factory=list)
    overrides: str = "overrides"
    author: str = ""


def parse_manifest(raw: dict[str, Any]) -> CurseForgeManifest:
    minecraft = raw.get("minecraft") or {}
    mc_version = str(minecraft.get("version") or "")
    if not mc_version:
        raise ModpackError("the CurseForge manifest does not declare a Minecraft version")
    loaders = minecraft.get("modLoaders") or []
    primary = next(
        (entry for entry in loaders if entry.get("primary")),
        loaders[0] if loaders else {},
    )
    loader_id = str(primary.get("id") or "")  # e.g. "forge-47.2.0"
    loader, _, loader_version = loader_id.partition("-")
    mapping = {"forge": "forge", "neoforge": "neoforge", "fabric": "fabric", "quilt": "quilt"}
    return CurseForgeManifest(
        name=str(raw.get("name") or "CurseForge pack"),
        version=str(raw.get("version") or ""),
        mc_version=mc_version,
        loader=mapping.get(loader, "vanilla"),
        loader_version=loader_version,
        files=[
            CurseForgeFile(
                project_id=int(item.get("projectID") or 0),
                file_id=int(item.get("fileID") or 0),
                required=bool(item.get("required", True)),
            )
            for item in raw.get("files") or []
            if item.get("projectID") and item.get("fileID")
        ],
        overrides=str(raw.get("overrides") or "overrides"),
        author=str(raw.get("author") or ""),
    )


def read_manifest(archive: Path) -> CurseForgeManifest:
    try:
        with zipfile.ZipFile(archive) as zf:
            try:
                payload = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
            except KeyError as exc:
                raise ModpackError(
                    f"{archive.name} is not a CurseForge pack (no {MANIFEST_NAME})"
                ) from exc
    except zipfile.BadZipFile as exc:
        raise ModpackError(f"{archive.name} is not a valid zip archive", detail=str(exc)) from exc
    return parse_manifest(payload)


def extract_overrides(archive: Path, manifest: CurseForgeManifest, instance_dir: Path) -> int:
    return len(extract_zip(archive, instance_dir, prefixes=(manifest.overrides,)))


class CurseForgeClient:
    """Minimal, key-gated client for the official API."""

    def __init__(self, http: HttpClient, api_key: str = "") -> None:
        self.http = http
        self.api_key = api_key.strip()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.enabled:
            raise ModpackError(
                "CurseForge downloads require an API key",
                hint=(
                    "Set the APEIRON_CURSEFORGE_API_KEY environment variable. "
                    "Apeiron does not ship a key "
                    "and does not download CurseForge files from unofficial mirrors."
                ),
            )
        return {"x-api-key": self.api_key, "Accept": "application/json"}

    def file_url(self, project_id: int, file_id: int) -> tuple[str, str, int]:
        """Return (download_url, filename, size) for a file id."""
        payload = self.http.get_json(
            f"{API_BASE}/mods/{project_id}/files/{file_id}", headers=self._headers()
        )
        data = payload.get("data") or {}
        url = data.get("downloadUrl")
        if not url:
            raise ModpackError(
                f"CurseForge does not expose a download for file {file_id}",
                hint="The author disabled third-party downloads for this file.",
            )
        return (
            str(url),
            str(data.get("fileName") or f"{file_id}.jar"),
            int(data.get("fileLength") or 0),
        )

    def resolve_specs(self, manifest: CurseForgeManifest, instance_dir: Path) -> list[DownloadSpec]:
        specs: list[DownloadSpec] = []
        for entry in manifest.files:
            if not entry.required:
                continue
            url, filename, size = self.file_url(entry.project_id, entry.file_id)
            target = (instance_dir / "mods" / filename).resolve()
            if not is_within(target, instance_dir.resolve()):
                raise SecurityError(f"unsafe mod filename: {filename}")
            specs.append(DownloadSpec(url=url, destination=target, size=size, category="modpack"))
        return specs

    def install_files(
        self, manifest: CurseForgeManifest, instance_dir: Path, downloader: DownloadManager
    ) -> int:
        specs = self.resolve_specs(manifest, instance_dir)
        if specs:
            downloader.run(specs)
        return len(specs)
