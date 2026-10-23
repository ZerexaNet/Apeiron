"""Common contract for mod loader installers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from apeiron.core.errors import LoaderError
from apeiron.core.paths import AppPaths


@dataclass(frozen=True)
class LoaderVersion:
    loader: str
    version: str
    mc_version: str
    stable: bool = True
    label: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "loader": self.loader,
            "version": self.version,
            "mcVersion": self.mc_version,
            "stable": self.stable,
            "label": self.label or f"{self.loader.capitalize()} {self.version}",
        }


@dataclass
class LoaderInstallResult:
    """Outcome of installing a loader into the shared version store."""

    version_id: str  # id of the generated version JSON (used to launch)
    mc_version: str
    loader: str
    loader_version: str
    json_path: Path
    libraries_added: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "versionId": self.version_id,
            "mcVersion": self.mc_version,
            "loader": self.loader,
            "loaderVersion": self.loader_version,
            "jsonPath": str(self.json_path),
            "librariesAdded": self.libraries_added,
        }


class LoaderProvider(Protocol):
    name: str
    requires_vanilla: bool

    def versions(self, mc_version: str) -> list[LoaderVersion]: ...

    def install(
        self, mc_version: str, loader_version: str, *, java_path: str = ""
    ) -> LoaderInstallResult: ...


def write_version_json(paths: AppPaths, version_id: str, payload: dict[str, Any]) -> Path:
    """Persist a generated version JSON after validating its essentials."""
    if not version_id or any(c in version_id for c in "/\\"):
        raise LoaderError(f"invalid generated version id: {version_id!r}")
    parent = str(payload.get("inheritsFrom") or "")
    if not parent:
        raise LoaderError("loader profile does not declare inheritsFrom")
    if not payload.get("mainClass"):
        raise LoaderError("loader profile does not declare a main class")
    if not isinstance(payload.get("libraries"), list) or not payload["libraries"]:
        raise LoaderError("loader profile declares no libraries")

    directory = paths.versions / version_id
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{version_id}.json"
    tmp = target.with_suffix(".json.part")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    return target


def is_version_installed(paths: AppPaths, version_id: str) -> bool:
    return (paths.versions / version_id / f"{version_id}.json").exists()
