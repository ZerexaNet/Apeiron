"""Composition root: wires settings, database, services and the UI together."""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apeiron.auth.service import AccountService
from apeiron.auth.store import default_store
from apeiron.bedrock.windows import WindowsBedrockAdapter
from apeiron.core import __version__
from apeiron.core.config import Settings, SettingsStore
from apeiron.core.http import HttpClient, HttpConfig
from apeiron.core.logging import configure as configure_logging
from apeiron.core.logging import get_logger, install_stdlib_bridge
from apeiron.core.paths import AppPaths, current_platform_key, default_root
from apeiron.core.updates import UpdateChecker
from apeiron.db.database import Database
from apeiron.db.repositories import (
    AccountRepository,
    AppStateRepository,
    DownloadRepository,
    InstanceRepository,
    LaunchRepository,
    ModRepository,
)
from apeiron.i18n.languages import resolve_language
from apeiron.instances.manager import InstanceManager
from apeiron.mc.installer import JavaInstaller
from apeiron.mc.java import JavaRuntime, detect_runtimes
from apeiron.mc.manifest import VersionManifest
from apeiron.modpack.service import ModpackService
from apeiron.net.downloader import DownloadManager
from apeiron.services.launcher import LaunchService

log = get_logger("app")


@dataclass
class AppContext:
    """Everything the UI needs, constructed once per process."""

    paths: AppPaths
    settings_store: SettingsStore
    database: Database
    http: HttpClient
    downloader: DownloadManager
    manifest: VersionManifest
    installer: JavaInstaller
    accounts: AccountService
    instances: InstanceManager
    instances_repo: InstanceRepository
    accounts_repo: AccountRepository
    downloads_repo: DownloadRepository
    mods_repo: ModRepository
    launches_repo: LaunchRepository
    app_state: AppStateRepository
    modpacks: ModpackService
    launcher: LaunchService
    bedrock: WindowsBedrockAdapter
    updates: UpdateChecker
    started_at: float = 0.0

    # -- convenience --------------------------------------------------------
    @property
    def settings(self) -> Settings:
        return self.settings_store.current

    @property
    def language(self) -> str:
        return resolve_language(self.settings.language)

    def java_runtimes(self) -> list[JavaRuntime]:
        extra = [self.settings.java_path] if self.settings.java_path else []
        return detect_runtimes(Path(p) for p in extra if p)

    def workspace_summary(self) -> dict[str, Any]:
        return {
            "root": str(self.paths.root),
            "platform": current_platform_key(),
            "python": platform.python_version(),
            "version": __version__,
            "credentialBackend": self.accounts.backend().name,
            "credentialSecure": self.accounts.backend().secure,
        }


def build_context(
    *,
    root: Path | None = None,
    console_logging: bool = False,
    progress_callback: Callable[[Any], None] | None = None,
    overrides: dict[str, Any] | None = None,
) -> AppContext:
    """Create the full object graph (used by the app, the CLI and tests)."""
    paths = AppPaths(root or default_root())
    paths.ensure()

    settings_store = SettingsStore(paths.config_file)
    settings = settings_store.load()

    configure_logging(
        paths.log_file,
        level=_log_level(settings.log_level),
        max_bytes=8 * 1024 * 1024,
        backup_count=5,
        console=console_logging,
    )
    install_stdlib_bridge()
    log.info("Apeiron %s starting on %s", __version__, current_platform_key())
    log.info("data root: %s", paths.root)

    database = Database(paths.database)
    database.migrate()
    database.integrity_check()

    http = HttpClient(
        HttpConfig(
            use_mirrors=True,
            timeout=settings.download_timeout,
            retries=settings.download_retries,
        )
    )
    downloads_repo = DownloadRepository(database)
    downloads_repo.mark_interrupted_as_failed()

    downloader = DownloadManager(
        http,
        concurrency=settings.max_concurrent_downloads,
        retries=settings.download_retries,
        repo=downloads_repo,
        progress_callback=progress_callback,
    )
    manifest = VersionManifest(http, paths.meta)
    installer = JavaInstaller(http, paths, downloader, manifest)

    accounts_repo = AccountRepository(database)
    store = default_store(paths.data)
    accounts = AccountService(accounts_repo, store, http, microsoft_client_id=_ms_client_id())

    instances_repo = InstanceRepository(database)
    instances = InstanceManager(instances_repo, paths)

    launcher = LaunchService(
        paths,
        http,
        manifest,
        installer,
        instances_repo,
        LaunchRepository(database),
        java_runtimes=lambda: detect_runtimes(),
        default_java=lambda: settings_store.current.java_path,
    )

    modpacks = ModpackService(
        instances,
        ModRepository(database),
        downloader,
        http=http,
        paths=paths,
        curseforge_key=os.environ.get("APEIRON_CURSEFORGE_API_KEY", ""),
        java_path=settings_store.current.java_path,
    )

    context = AppContext(
        paths=paths,
        settings_store=settings_store,
        database=database,
        http=http,
        downloader=downloader,
        manifest=manifest,
        installer=installer,
        accounts=accounts,
        instances=instances,
        instances_repo=instances_repo,
        accounts_repo=accounts_repo,
        downloads_repo=downloads_repo,
        mods_repo=ModRepository(database),
        launches_repo=LaunchRepository(database),
        app_state=AppStateRepository(database),
        modpacks=modpacks,
        launcher=launcher,
        bedrock=WindowsBedrockAdapter(),
        updates=UpdateChecker(http),
    )
    if overrides:
        for key, value in overrides.items():
            setattr(context, key, value)
    return context


def _ms_client_id() -> str:
    return ""


def _log_level(name: str) -> int:
    from apeiron.core import logging as native

    return {
        "DEBUG": native.DEBUG,
        "INFO": native.INFO,
        "WARNING": native.WARNING,
        "ERROR": native.ERROR,
        "CRITICAL": native.CRITICAL,
    }.get(name.upper(), native.INFO)


def platform_support_matrix() -> list[dict[str, Any]]:
    """What this build claims to support, per platform (also shown in About)."""
    return [
        {
            "platform": "windows",
            "java": True,
            "bedrock": True,
            "credentialStore": "DPAPI",
        },
        {
            "platform": "macos",
            "java": True,
            "bedrock": False,
            "credentialStore": "Keychain",
        },
        {
            "platform": "linux",
            "java": True,
            "bedrock": False,
            "credentialStore": "libsecret (file fallback)",
        },
    ]


def current_support() -> dict[str, bool]:
    key = current_platform_key()
    return {
        "java": True,
        "bedrock": key == "windows" and sys.platform.startswith("win"),
    }
