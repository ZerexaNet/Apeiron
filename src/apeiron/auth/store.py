"""Credential storage backed by the operating system where possible.

Backend selection (first available wins):

* **Windows** — DPAPI (``CryptProtectData``) bound to the current user, stored
  as an encrypted blob.
* **macOS** — the login keychain via ``/usr/bin/security``.
* **Linux** — ``secret-tool`` (libsecret / GNOME Keyring, KWallet bridge).
* **Fallback** — a ``0600`` JSON file with an explicit ``secure=False`` flag so
  the UI can warn the user that tokens are not hardware-protected.

Only opaque credential blobs are stored here; account metadata lives in SQLite.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from apeiron.core.errors import ApeironError
from apeiron.core.logging import get_logger

log = get_logger("credentials")

SERVICE_NAME = "Apeiron"


class CredentialStore(Protocol):
    name: str
    secure: bool

    def get(self, key: str) -> dict[str, Any] | None: ...

    def set(self, key: str, payload: dict[str, Any]) -> None: ...

    def delete(self, key: str) -> None: ...

    def available(self) -> bool: ...


@dataclass
class StoreInfo:
    name: str
    secure: bool


# ---------------------------------------------------------------------------
# Windows DPAPI
# ---------------------------------------------------------------------------


class WindowsDpapiStore:
    name = "windows-dpapi"
    secure = True

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def available(self) -> bool:
        return sys.platform.startswith("win")

    def _path(self, key: str) -> Path:
        safe = "".join(c for c in key if c.isalnum() or c in "-_.")[:80]
        return self.directory / f"{safe}.bin"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            blob = self._unprotect(path.read_bytes())
            return json.loads(blob.decode("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            log.error("cannot read credential %s: %s", key, exc)
            return None

    def set(self, key: str, payload: dict[str, Any]) -> None:
        path = self._path(key)
        data = json.dumps(payload).encode("utf-8")
        try:
            path.write_bytes(self._protect(data))
            os.chmod(path, 0o600)
        except OSError as exc:
            raise ApeironError("cannot store credentials securely", detail=str(exc)) from exc

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    # -- DPAPI helpers ------------------------------------------------------
    @staticmethod
    def _dpapi():
        import ctypes
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        return ctypes, DataBlob, crypt32, kernel32

    def _protect(self, data: bytes) -> bytes:
        ctypes, blob_type, crypt32, kernel32 = self._dpapi()
        blob_in = blob_type(
            len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char))
        )
        blob_out = blob_type()
        if not crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)
        ):
            raise OSError("CryptProtectData failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)

    def _unprotect(self, data: bytes) -> bytes:
        ctypes, blob_type, crypt32, kernel32 = self._dpapi()
        blob_in = blob_type(
            len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char))
        )
        blob_out = blob_type()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)
        ):
            raise OSError("CryptUnprotectData failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)


# ---------------------------------------------------------------------------
# macOS Keychain
# ---------------------------------------------------------------------------


class MacKeychainStore:
    name = "macos-keychain"
    secure = True

    def available(self) -> bool:
        return sys.platform == "darwin" and shutil.which("security") is not None

    @staticmethod
    def _run(args: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["security", *args],
            capture_output=True,
            text=True,
            input=stdin,
            timeout=20,
            check=False,
        )

    def get(self, key: str) -> dict[str, Any] | None:
        result = self._run(["find-generic-password", "-s", SERVICE_NAME, "-a", key, "-w"])
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return None

    def set(self, key: str, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload)
        result = self._run(
            ["add-generic-password", "-U", "-s", SERVICE_NAME, "-a", key, "-w", encoded]
        )
        if result.returncode != 0:
            raise ApeironError("keychain rejected the credential", detail=result.stderr.strip())

    def delete(self, key: str) -> None:
        self._run(["delete-generic-password", "-s", SERVICE_NAME, "-a", key])


# ---------------------------------------------------------------------------
# Linux libsecret
# ---------------------------------------------------------------------------


class LibsecretStore:
    name = "linux-libsecret"
    secure = True

    def available(self) -> bool:
        return shutil.which("secret-tool") is not None and bool(
            os.environ.get("DISPLAY") or os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        )

    def _run(
        self, args: list[str], *, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["secret-tool", *args],
            capture_output=True,
            text=True,
            input=stdin,
            timeout=20,
            check=False,
        )

    def get(self, key: str) -> dict[str, Any] | None:
        result = self._run(["lookup", "service", SERVICE_NAME, "account", key])
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

    def set(self, key: str, payload: dict[str, Any]) -> None:
        result = self._run(
            [
                "store",
                "--label",
                f"{SERVICE_NAME} credential",
                "service",
                SERVICE_NAME,
                "account",
                key,
            ],
            stdin=json.dumps(payload),
        )
        if result.returncode != 0:
            raise ApeironError(
                "secret service rejected the credential", detail=result.stderr.strip()
            )

    def delete(self, key: str) -> None:
        self._run(["clear", "service", SERVICE_NAME, "account", key])


# ---------------------------------------------------------------------------
# Fallback: file with restrictive permissions
# ---------------------------------------------------------------------------


class FileCredentialStore:
    """Last-resort store. Clearly marked insecure so the UI can warn."""

    name = "file-fallback"
    secure = False

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.directory, 0o700)
        self.index = self.directory / "credentials.json"

    def available(self) -> bool:
        return True

    def _read(self) -> dict[str, str]:
        if not self.index.exists():
            return {}
        try:
            data = json.loads(self.index.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict[str, str]) -> None:
        tmp = self.index.with_suffix(".json.part")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        tmp.replace(self.index)

    def get(self, key: str) -> dict[str, Any] | None:
        raw = self._read().get(key)
        if not raw:
            return None
        try:
            return json.loads(base64.b64decode(raw).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return None

    def set(self, key: str, payload: dict[str, Any]) -> None:
        data = self._read()
        data[key] = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        self._write(data)

    def delete(self, key: str) -> None:
        data = self._read()
        if data.pop(key, None) is not None:
            self._write(data)


def default_store(data_dir: Path) -> CredentialStore:
    """Pick the most secure available backend for this machine."""
    candidates: list[CredentialStore] = []
    if sys.platform.startswith("win"):
        candidates.append(WindowsDpapiStore(data_dir / "credentials"))
    elif sys.platform == "darwin":
        candidates.append(MacKeychainStore())
    else:
        candidates.append(LibsecretStore())
    for candidate in candidates:
        try:
            if candidate.available():
                log.info("credential backend: %s (secure=%s)", candidate.name, candidate.secure)
                return candidate
        except Exception as exc:
            log.warning("credential backend %s unavailable: %s", candidate.name, exc)
    fallback = FileCredentialStore(data_dir / "credentials")
    log.warning(
        "no OS credential service available; using the file fallback (not hardware protected)"
    )
    return fallback


def store_info(store: CredentialStore) -> StoreInfo:
    return StoreInfo(name=store.name, secure=store.secure)
