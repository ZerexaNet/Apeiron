"""Official Mojang version manifest client with an on-disk cache.

Only the official ``piston-meta.mojang.com`` endpoints are used; there is no
fallback to a mirror, because silently switching metadata sources is exactly
the kind of supply-chain substitution a launcher must not do.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apeiron.core.errors import IntegrityError, NetworkError
from apeiron.core.http import HttpClient
from apeiron.core.logging import get_logger

log = get_logger("manifest")

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
RESOURCES_URL = "https://resources.download.minecraft.net/{prefix}/{name}"
ASSET_INDEX_URL = "https://piston-meta.mojang.com/v1/packages/{sha1}/{id}.json"
CACHE_TTL_SECONDS = 3600


@dataclass(frozen=True)
class VersionSummary:
    id: str
    type: str
    url: str
    sha1: str
    release_time: str
    compliance_level: int = 0

    @property
    def is_release(self) -> bool:
        return self.type == "release"

    @property
    def is_snapshot(self) -> bool:
        return self.type == "snapshot"


class VersionManifest:
    """Fetches, caches and queries the official version list."""

    def __init__(self, http: HttpClient, cache_dir: Path) -> None:
        self.http = http
        self.cache_dir = cache_dir
        self.cache_file = cache_dir / "version_manifest_v2.json"
        self._versions: list[VersionSummary] = []
        self._latest: dict[str, str] = {}

    # -- loading ------------------------------------------------------------
    def load(
        self, *, force_refresh: bool = False, allow_stale: bool = True
    ) -> list[VersionSummary]:
        if not force_refresh and self._versions:
            return self._versions

        cached = self._read_cache()
        if cached is not None and not force_refresh and self._cache_fresh():
            self._apply(cached)
            return self._versions

        try:
            payload = self.http.get_json(MANIFEST_URL)
            self._write_cache(payload)
        except NetworkError as exc:
            if cached is not None and allow_stale:
                log.warning("manifest refresh failed (%s); using cached copy", exc.message)
                self._apply(cached)
                return self._versions
            raise

        self._apply(payload)
        return self._versions

    def latest(self, channel: str = "release") -> str:
        self.load()
        return self._latest.get(channel, "")

    def releases(self) -> list[VersionSummary]:
        return [v for v in self.load() if v.is_release]

    def snapshots(self) -> list[VersionSummary]:
        return [v for v in self.load() if v.is_snapshot]

    def find(self, version_id: str) -> VersionSummary:
        for version in self.load():
            if version.id == version_id:
                return version
        raise NetworkError(f"unknown Minecraft version: {version_id}")

    # -- version JSON -------------------------------------------------------
    def fetch_version_json(self, version_id: str) -> dict[str, Any]:
        summary = self.find(version_id)
        body = self.http.get_bytes(summary.url, expected_sha1=summary.sha1)
        if summary.sha1 and hashlib.sha1(body).hexdigest() != summary.sha1.lower():
            raise IntegrityError(f"version JSON checksum mismatch: {version_id}")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NetworkError(f"invalid version JSON: {version_id}") from exc
        if not isinstance(payload, dict):
            raise NetworkError(f"version JSON for {version_id} is not an object")
        if payload.get("id") != version_id:
            # A mismatched id means the CDN served the wrong document.
            raise IntegrityError(
                f"version JSON id mismatch: expected {version_id}, got {payload.get('id')}"
            )
        return payload

    def download_version_json(self, version_id: str, versions_dir: Path) -> Path:
        """Persist ``<versions>/<id>/<id>.json`` and return its path."""
        target_dir = versions_dir / version_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{version_id}.json"
        payload = self.fetch_version_json(version_id)
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        tmp = target.with_suffix(".json.part")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(target)
        return target

    # -- cache --------------------------------------------------------------
    def _read_cache(self) -> dict[str, Any] | None:
        if not self.cache_file.exists():
            return None
        try:
            return json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, payload: dict[str, Any]) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_file.with_suffix(".json.part")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self.cache_file)
        except OSError as exc:
            log.warning("cannot cache version manifest: %s", exc)

    def _cache_fresh(self) -> bool:
        try:
            age = time.time() - self.cache_file.stat().st_mtime
        except OSError:
            return False
        return age < CACHE_TTL_SECONDS

    def _apply(self, payload: dict[str, Any]) -> None:
        versions: list[VersionSummary] = []
        for entry in payload.get("versions") or []:
            try:
                versions.append(
                    VersionSummary(
                        id=str(entry["id"]),
                        type=str(entry.get("type", "release")),
                        url=str(entry["url"]),
                        sha1=str(entry.get("sha1") or ""),
                        release_time=str(entry.get("releaseTime") or ""),
                        compliance_level=int(entry.get("complianceLevel") or 0),
                    )
                )
            except (KeyError, TypeError):
                continue
        if not versions:
            raise NetworkError("version manifest contains no versions")
        self._versions = versions
        self._latest = {k: str(v) for k, v in (payload.get("latest") or {}).items()}
        log.info("version manifest loaded: %d versions, latest=%s", len(versions), self._latest)


def sha1_of(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resource_url(sha1: str) -> str:
    """Object URL for the legacy asset layout (hash-prefixed)."""
    return RESOURCES_URL.format(prefix=sha1[:2], name=sha1)
