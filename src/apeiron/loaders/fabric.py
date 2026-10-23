"""Fabric and Quilt loader installation via their official metadata APIs.

Both projects publish a ready-made *profile* JSON that declares
``inheritsFrom`` the vanilla version, its own main class and a library list.
No loader code is executed at install time; the profile is validated and
stored, and the normal installer then downloads the declared libraries.
"""

from __future__ import annotations

from typing import Any

from apeiron.core.errors import LoaderError, NetworkError
from apeiron.core.logging import get_logger
from apeiron.core.paths import AppPaths
from apeiron.loaders.base import LoaderInstallResult, LoaderVersion, write_version_json

log = get_logger("loaders")

FABRIC_META = "https://meta.fabricmc.net/v2"
QUILT_META = "https://meta.quiltmc.org/v3"


class _MetaLoader:
    name = "loader"
    base_url = ""
    main_class = ""

    def __init__(self, http, paths: AppPaths) -> None:
        self.http = http
        self.paths = paths

    # -- versions -----------------------------------------------------------
    def versions(self, mc_version: str) -> list[LoaderVersion]:
        payload = self.http.get_json(f"{self.base_url}/versions/loader/{mc_version}")
        if not isinstance(payload, list):
            raise LoaderError(f"{self.name} returned an unexpected version list")
        result: list[LoaderVersion] = []
        for entry in payload:
            loader = (entry or {}).get("loader") or {}
            version = str(loader.get("version") or "")
            if not version:
                continue
            result.append(
                LoaderVersion(
                    loader=self.name,
                    version=version,
                    mc_version=mc_version,
                    stable=bool(loader.get("stable", True)),
                )
            )
        return result

    def game_versions(self) -> list[str]:
        payload = self.http.get_json(f"{self.base_url}/versions/game")
        return [str(item.get("version")) for item in payload if item.get("stable")]

    # -- install ------------------------------------------------------------
    def profile(self, mc_version: str, loader_version: str) -> dict[str, Any]:
        url = f"{self.base_url}/versions/loader/{mc_version}/{loader_version}/profile/json"
        try:
            payload = self.http.get_json(url)
        except NetworkError as exc:
            raise LoaderError(
                f"{self.name} has no {loader_version} build for Minecraft {mc_version}",
                detail=exc.message,
            ) from exc
        if not isinstance(payload, dict):
            raise LoaderError(f"{self.name} profile is not an object")
        return payload

    def install(
        self, mc_version: str, loader_version: str, *, java_path: str = ""
    ) -> LoaderInstallResult:
        payload = self.profile(mc_version, loader_version)
        if str(payload.get("inheritsFrom")) != mc_version:
            raise LoaderError(
                f"{self.name} profile inherits from {payload.get('inheritsFrom')}, "
                f"expected {mc_version}"
            )
        expected_main = self.main_class
        if expected_main and payload.get("mainClass") != expected_main:
            raise LoaderError(
                f"unexpected main class in {self.name} profile: {payload.get('mainClass')}"
            )
        version_id = str(payload.get("id") or f"{self.name}-loader-{loader_version}-{mc_version}")
        json_path = write_version_json(self.paths, version_id, payload)
        log.info(
            "installed %s %s for MC %s as %s", self.name, loader_version, mc_version, version_id
        )
        return LoaderInstallResult(
            version_id=version_id,
            mc_version=mc_version,
            loader=self.name,
            loader_version=loader_version,
            json_path=json_path,
            libraries_added=len(payload.get("libraries") or []),
        )


class FabricLoader(_MetaLoader):
    name = "fabric"
    base_url = FABRIC_META
    main_class = "net.fabricmc.loader.impl.launch.knot.KnotClient"


class QuiltLoader(_MetaLoader):
    name = "quilt"
    base_url = QUILT_META
    main_class = "org.quiltmc.loader.impl.launch.knot.KnotClient"
