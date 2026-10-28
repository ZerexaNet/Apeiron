"""Update checks against a signed-manifest style JSON feed.

Security properties implemented here:

* HTTPS only, host allow-list (no redirect to an arbitrary CDN);
* the remote manifest must be a JSON object with a semantic version;
* the downloaded asset is verified against the SHA-256 published in the
  manifest before it is offered to the user;
* Apeiron **never executes** a downloaded installer automatically — the user is
  shown the release notes and the verified file path.

Detached cryptographic signatures are not implemented yet; the manifest is
therefore only as trustworthy as its TLS origin.  This is recorded as a known
limitation in ``docs/PLATFORM_SUPPORT.md`` and ``SECURITY.md`` rather than
being described as full signature verification.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from apeiron.core import __version__
from apeiron.core.errors import UpdateError
from apeiron.core.http import HttpClient
from apeiron.core.logging import get_logger
from apeiron.core.paths import current_platform_key

log = get_logger("updates")

DEFAULT_MANIFEST_URL = "https://github.com/ZerexaNet/Apeiron/releases/latest/download/update.json"
ALLOWED_HOSTS = frozenset(
    {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
)
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def parse_version(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.match((value or "").strip())
    if not match:
        raise UpdateError(f"invalid version string: {value!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


@dataclass
class UpdateInfo:
    version: str
    notes: str
    url: str
    sha256: str
    size: int
    mandatory: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "notes": self.notes,
            "url": self.url,
            "sha256": self.sha256,
            "size": self.size,
            "mandatory": self.mandatory,
        }


class UpdateChecker:
    def __init__(self, http: HttpClient, manifest_url: str = DEFAULT_MANIFEST_URL) -> None:
        self.http = http
        self.manifest_url = manifest_url

    def _validate_host(self, url: str) -> None:
        host = (urlsplit(url).hostname or "").lower()
        if host not in ALLOWED_HOSTS:
            raise UpdateError(f"update source host is not allow-listed: {host or '(none)'}")

    def check(self) -> UpdateInfo | None:
        """Return an update when a newer version exists, else ``None``."""
        self._validate_host(self.manifest_url)
        try:
            payload = self.http.get_json(self.manifest_url, max_bytes=2 * 1024 * 1024)
        except Exception as exc:
            log.warning("update check failed: %s", exc)
            return None
        if not isinstance(payload, dict):
            raise UpdateError("the update manifest is not a JSON object")

        version = str(payload.get("version") or "")
        if not version:
            raise UpdateError("the update manifest has no version")
        assets = payload.get("assets") or {}
        platform_key = current_platform_key()
        asset = assets.get(platform_key) if isinstance(assets, dict) else None
        if not isinstance(asset, dict):
            log.info("no update asset published for %s", platform_key)
            return None

        url = str(asset.get("url") or "")
        sha256 = str(asset.get("sha256") or "").lower()
        if not url or len(sha256) != 64:
            raise UpdateError("the update asset entry is incomplete")
        self._validate_host(url)

        if parse_version(version) <= parse_version(__version__):
            return None
        return UpdateInfo(
            version=version,
            notes=str(payload.get("notes") or ""),
            url=url,
            sha256=sha256,
            size=int(asset.get("size") or 0),
            mandatory=bool(payload.get("mandatory", False)),
        )

    def download(self, info: UpdateInfo, destination: Path) -> Path:
        """Download and verify the update asset; never executes it."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        size = 0
        with tmp.open("wb") as handle:
            for chunk in self.http.stream(info.url):
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if info.size and size != info.size:
            tmp.unlink(missing_ok=True)
            raise UpdateError(f"update size mismatch: expected {info.size}, got {size}")
        actual = digest.hexdigest()
        if actual != info.sha256:
            tmp.unlink(missing_ok=True)
            raise UpdateError(f"update checksum mismatch: expected {info.sha256}, got {actual}")
        tmp.replace(destination)
        log.info("verified update %s at %s", info.version, destination)
        return destination


def read_manifest_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
