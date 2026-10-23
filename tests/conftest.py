"""Shared pytest fixtures."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APEIRON_DISABLE_TELEMETRY", "1")


@pytest.fixture()
def app_paths(tmp_path: Path):
    from apeiron.core.paths import AppPaths

    paths = AppPaths(tmp_path / "apeiron-root")
    paths.ensure()
    return paths


@pytest.fixture()
def settings_store(app_paths):
    from apeiron.core.config import SettingsStore

    return SettingsStore(app_paths.config_file)


@pytest.fixture()
def database(tmp_path: Path):
    from apeiron.db.database import Database

    db = Database(tmp_path / "test.db")
    db.migrate()
    yield db
    db.close()


@pytest.fixture()
def repos(database):
    from apeiron.db.repositories import (
        AccountRepository,
        AppStateRepository,
        DownloadRepository,
        InstanceRepository,
        LaunchRepository,
        ModRepository,
    )

    return {
        "instances": InstanceRepository(database),
        "accounts": AccountRepository(database),
        "downloads": DownloadRepository(database),
        "mods": ModRepository(database),
        "launches": LaunchRepository(database),
        "state": AppStateRepository(database),
    }


@pytest.fixture()
def native_log(tmp_path: Path):
    """Configure the native log core against a temporary file."""
    from apeiron.core import logging as native

    log_file = tmp_path / "logs" / "test.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    native.configure(log_file, level=native.DEBUG, max_bytes=1024 * 1024, backup_count=2)
    yield log_file
    native.flush()
    native.shutdown()


@pytest.fixture()
def fake_http():
    """A stub HTTP client returning canned responses."""
    from apeiron.core.http import Response

    class FakeHttp:
        def __init__(self) -> None:
            self.routes: dict[str, Response] = {}
            self.calls: list[str] = []

        def add(self, url: str, body: bytes = b"", status: int = 200) -> None:
            self.routes[url] = Response(url=url, status=status, headers={}, body=body)

        def add_json(self, url: str, payload) -> None:
            import json

            self.add(url, json.dumps(payload).encode("utf-8"))

        def request(self, url, **kwargs):
            self.calls.append(url)
            if url not in self.routes:
                from apeiron.core.errors import NetworkError

                raise NetworkError(f"HTTP 404 for {url}")
            return self.routes[url]

        def get_json(self, url, **kwargs):
            return self.request(url).json()

        def get_bytes(self, url, **kwargs):
            return self.request(url).body

        def stream(self, url, chunk_size=262144, timeout=None):
            body = self.get_bytes(url)
            for start in range(0, len(body), chunk_size):
                yield body[start : start + chunk_size]

    return FakeHttp()
