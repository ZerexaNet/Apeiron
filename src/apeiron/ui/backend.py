"""Qt bridge between the QML UI and the Python services.

All long-running work (network, disk, process start) is executed on a thread
pool; results are delivered to the GUI thread through queued signals.  No
service object is ever touched from a worker thread other than through these
helpers, and every failure is converted into a structured, translated error
dictionary instead of leaking a traceback into QML.
"""

from __future__ import annotations

import json
import platform
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QFontDatabase

from apeiron.core import __version__
from apeiron.core.errors import ApeironError
from apeiron.core.logging import get_logger
from apeiron.core.logging import stats as log_stats
from apeiron.core.paths import current_platform_key
from apeiron.i18n.languages import LANGUAGES, available_choices, resolve_language
from apeiron.instances.manager import instance_summary
from apeiron.services.app_context import AppContext, platform_support_matrix

log = get_logger("ui")

DISCLAIMER_SHORT = (
    "Apeiron is an independent third-party launcher. It is not an official Minecraft "
    "product and is not affiliated with, endorsed by, or approved by Mojang Studios or "
    "Microsoft."
)


class _Signals(QObject):
    done = Signal(str, object)
    failed = Signal(str, object)
    progress = Signal(str, object)


class _Task(QRunnable):
    def __init__(self, task_id: str, fn: Callable[[], Any], signals: _Signals) -> None:
        super().__init__()
        self.task_id = task_id
        self.fn = fn
        self.signals = signals

    def run(self) -> None:
        try:
            result = self.fn()
        except ApeironError as exc:
            self._emit(self.signals.failed, exc.to_dict())
        except Exception as exc:
            log.exception("background task %s failed", self.task_id)
            self._emit(
                self.signals.failed,
                {
                    "code": "apeiron.unexpected",
                    "message": str(exc),
                    "messageKey": "error.generic",
                    "detail": traceback.format_exc(limit=6),
                },
            )
        else:
            self._emit(self.signals.done, result)

    def _emit(self, signal, payload) -> None:
        """Emit unless the application is already tearing down."""
        try:
            signal.emit(self.task_id, payload)
        except RuntimeError:
            # The C++ receiver was destroyed while this task was still running
            # (application exit). Dropping the result is correct here.
            pass


class Backend(QObject):
    """The single object exposed to QML as ``App``."""

    contentChanged = Signal()
    instancesChanged = Signal()
    accountsChanged = Signal()
    downloadsChanged = Signal()
    versionsChanged = Signal()
    loadersChanged = Signal()
    javaChanged = Signal()
    selectionChanged = Signal()
    statusChanged = Signal()
    themeChanged = Signal()
    languageChanged = Signal()
    consoleChanged = Signal()
    notify = Signal(str, str, str)  # level, title, message
    deviceCodeChanged = Signal()
    busyChanged = Signal()
    bedrockChanged = Signal()
    logChanged = Signal()
    settingsChanged = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.ctx = context
        self._pool = QThreadPool.globalInstance()
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)
        self._signals.failed.connect(self._on_failed)
        self._signals.progress.connect(self._on_progress)

        self._instances: list[dict[str, Any]] = []
        self._accounts: list[dict[str, Any]] = []
        self._downloads: list[dict[str, Any]] = []
        self._versions: list[dict[str, Any]] = []
        self._loaders: list[dict[str, Any]] = []
        self._java: list[dict[str, Any]] = []
        self._selected_id = ""
        self._console = ""
        self._busy = ""
        self._status = {"kind": "idle", "text": ""}
        self._engine: Any = None
        self._qml_url: Any = None
        self._device_code: dict[str, Any] = {}
        self._pending_login: Any = None
        self._loader_cache: dict[str, list[dict[str, Any]]] = {}
        self._bedrock: dict[str, Any] = {"supported": False, "installed": False, "running": False}
        self._log_text = ""
        self._pending: dict[str, Callable[[Any], None]] = {}

        self.ctx.downloader.progress_callback = self._download_progress

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------
    @Property("QVariantList", notify=instancesChanged)
    def instances(self) -> list[dict[str, Any]]:
        return self._instances

    @Property("QVariantList", notify=accountsChanged)
    def accounts(self) -> list[dict[str, Any]]:
        return self._accounts

    @Property("QVariantList", notify=downloadsChanged)
    def downloads(self) -> list[dict[str, Any]]:
        return self._downloads

    @Property("QVariantList", notify=versionsChanged)
    def versions(self) -> list[dict[str, Any]]:
        return self._versions

    @Property("QVariantList", notify=loadersChanged)
    def loaders(self) -> list[dict[str, Any]]:
        return self._loaders

    @Property("QVariantList", notify=javaChanged)
    def javaRuntimes(self) -> list[dict[str, Any]]:
        return self._java

    @Property("QVariantMap", notify=selectionChanged)
    def selectedInstance(self) -> dict[str, Any]:
        if not self._selected_id:
            return {}
        for item in self._instances:
            if item["id"] == self._selected_id:
                return item
        return {}

    @Property(str, notify=selectionChanged)
    def selectedInstanceId(self) -> str:
        return self._selected_id

    @Property(str, constant=True)
    def fixedFontFamily(self) -> str:
        families = QFontDatabase.families()
        preferred = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
        if preferred in families:
            return preferred
        # Offscreen platforms may return a generic, uninstalled family even
        # from systemFont(). Select an installed fixed-pitch face explicitly.
        return next(
            (family for family in families if QFontDatabase.isFixedPitch(family)),
            families[0] if families else "",
        )

    @Property(str, notify=consoleChanged)
    def consoleText(self) -> str:
        return self._console

    @Property("QVariantMap", notify=statusChanged)
    def status(self) -> dict[str, Any]:
        return self._status

    @Property(str, notify=busyChanged)
    def busy(self) -> str:
        return self._busy

    @Property("QVariantMap", notify=deviceCodeChanged)
    def deviceCode(self) -> dict[str, Any]:
        return self._device_code

    @Property("QVariantMap", notify=bedrockChanged)
    def bedrockStatus(self) -> dict[str, Any]:
        return self._bedrock

    @Property(str, notify=logChanged)
    def logText(self) -> str:
        return self._log_text

    @Property(str, notify=themeChanged)
    def theme(self) -> str:
        return self.ctx.settings.theme

    @Property(str, notify=languageChanged)
    def language(self) -> str:
        return self.ctx.settings.language

    @Property(str, notify=languageChanged)
    def effectiveLanguage(self) -> str:
        return resolve_language(self.ctx.settings.language)

    @Property("QVariantList", notify=languageChanged)
    def languageChoices(self) -> list[dict[str, str]]:
        return available_choices()

    @Property("QVariantMap", notify=languageChanged)
    def translationState(self) -> dict[str, Any]:
        manager = getattr(self, "_translation_manager", None)
        if manager is None:
            return {"language": resolve_language(self.ctx.settings.language), "translated": False}
        state = manager.state
        return {
            "language": state.language,
            "translated": state.translated,
            "missing": state.missing,
            "resource": state.resource,
        }

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self) -> dict[str, Any]:
        current = self.ctx.settings
        return {
            "language": current.language,
            "theme": current.theme,
            "memoryMb": current.default_memory_mb,
            "concurrency": current.max_concurrent_downloads,
            "timeout": current.download_timeout,
            "retries": current.download_retries,
            "javaPath": current.java_path,
            "jvmArgs": current.default_jvm_args,
            "checkUpdates": current.check_updates,
            "logLevel": current.log_level,
            "showSnapshots": current.show_snapshots,
        }

    @Property("QVariantMap", notify=statusChanged)
    def about(self) -> dict[str, Any]:
        backend = self.ctx.accounts.backend()
        return {
            "version": __version__,
            "company": "Zerexa Ltd",
            "contact": "admin@zerexa.net",
            "platform": current_platform_key(),
            "python": platform.python_version(),
            "qt": _qt_version(),
            "dataRoot": str(self.ctx.paths.root),
            "credentialBackend": backend.name,
            "credentialSecure": backend.secure,
            "disclaimer": DISCLAIMER_SHORT,
            "support": platform_support_matrix(),
            "logStats": log_stats(),
        }

    @Property("QVariantList", notify=statusChanged)
    def recentLaunches(self) -> list[dict[str, Any]]:
        return [
            {
                "id": item.id,
                "instanceId": item.instance_id,
                "startedAt": item.started_at,
                "status": item.status,
                "exitCode": item.exit_code if item.exit_code is not None else "",
                "duration": item.duration_seconds,
            }
            for item in self.ctx.launches_repo.recent(15)
        ]

    def set_translation_manager(self, manager: Any) -> None:
        self._translation_manager = manager
        self.languageChanged.emit()

    def _reload_interface(self) -> None:
        """Rebuild the QML tree so every string is resolved again."""
        engine = self._engine
        if engine is None or self._qml_url is None:
            return
        engine.clearComponentCache()
        engine.load(self._qml_url)
        log.info("interface rebuilt for language %s", self.ctx.settings.language)

    def attach_engine(self, engine: Any, qml_url: Any = None) -> None:
        """Remember the engine so a language change can rebuild the interface."""
        self._engine = engine
        self._qml_url = qml_url

    # ------------------------------------------------------------------
    # task plumbing
    # ------------------------------------------------------------------
    def _run(
        self, task_id: str, fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None
    ) -> None:
        if on_done is not None:
            self._pending[task_id] = on_done
        self._pool.start(_Task(task_id, fn, self._signals))

    def _on_done(self, task_id: str, result: Any) -> None:
        callback = self._pending.pop(task_id, None)
        self._busy = ""
        self.busyChanged.emit()
        if callback is not None:
            try:
                callback(result)
            except Exception:
                log.exception("result handler for %s failed", task_id)

    def _on_failed(self, task_id: str, error: dict[str, Any]) -> None:
        self._pending.pop(task_id, None)
        self._busy = ""
        self.busyChanged.emit()
        if self._status.get("kind") == "download":
            self._status = {"kind": "idle", "text": ""}
            self.statusChanged.emit()
        self.notify.emit("error", error.get("code", "error"), self._error_text(error))

    def _on_progress(self, task_id: str, payload: Any) -> None:
        if isinstance(payload, dict):
            self._status = {"kind": "progress", **payload}
            self.statusChanged.emit()

    @staticmethod
    def _error_text(error: dict[str, Any]) -> str:
        message = str(error.get("message") or "")
        hint = error.get("hint")
        return f"{message}\n{hint}" if hint else message

    def _refresh_instances(self) -> None:
        self._instances = [instance_summary(i) for i in self.ctx.instances.list()]
        self.instancesChanged.emit()
        self.selectionChanged.emit()

    def _refresh_accounts(self) -> None:
        accounts = self.ctx.accounts.list()
        self._accounts = [
            {
                "id": account.id,
                "name": account.name,
                "kind": account.kind,
                "uuid": account.uuid,
                "verified": account.verified,
                "skinModel": account.skin_model,
                "statusKey": "account.verified" if account.verified else "account.offlineLocal",
                "lastUsed": account.last_used_at or "",
            }
            for account in accounts
        ]
        self.accountsChanged.emit()

    def _refresh_downloads(self) -> None:
        self._downloads = [
            {
                "id": task.id,
                "url": task.url,
                "destination": task.destination,
                "status": task.status,
                "bytesDone": task.bytes_done,
                "bytesTotal": task.bytes_total,
                "attempts": task.attempts,
                "error": task.error,
                "category": task.category,
                "updatedAt": task.updated_at,
            }
            for task in self.ctx.downloads_repo.list(limit=100)
        ]
        self.downloadsChanged.emit()

    def _download_progress(self, progress: Any) -> None:
        payload = progress.as_dict() if hasattr(progress, "as_dict") else dict(progress)
        terminal = payload.get("cancelled") or (
            payload.get("totalTasks", 0) > 0
            and payload.get("completedTasks", 0) + payload.get("failedTasks", 0)
            >= payload["totalTasks"]
        )
        self._status = {"kind": "idle" if terminal else "download", **payload}
        self.statusChanged.emit()

    # ------------------------------------------------------------------
    # bootstrap
    # ------------------------------------------------------------------
    @Slot()
    def bootstrap(self) -> None:
        self._refresh_instances()
        self._refresh_accounts()
        self._refresh_downloads()
        self._console = ""
        self.consoleChanged.emit()
        self._refresh_logs()
        self._status = {"kind": "idle", "text": ""}
        self.statusChanged.emit()
        if current_platform_key() == "windows":
            self._bedrock = self.ctx.bedrock.status()
            self.bedrockChanged.emit()
        self.load_versions(initial=True)
        self.detect_java()

    @Slot(bool)
    def load_versions(self, initial: bool = False) -> None:
        def work() -> dict[str, Any]:
            versions = self.ctx.manifest.load(force_refresh=not initial)
            releases = [v for v in versions if v.is_release][:120]
            snapshots = (
                [v for v in versions if v.is_snapshot][:40]
                if self.ctx.settings.show_snapshots
                else []
            )
            entries = [
                {"id": v.id, "type": v.type, "releaseTime": v.release_time}
                for v in (releases + snapshots)
            ]
            return {"latest": self.ctx.manifest.latest("release"), "versions": entries}

        def done(result: dict[str, Any]) -> None:
            self._versions = list(result.get("versions") or [])
            self.versionsChanged.emit()
            self._status = {"kind": "idle", "latest": result.get("latest", "")}
            self.statusChanged.emit()

        self._run("versions", work, done)

    @Slot()
    def detect_java(self) -> None:
        def work() -> dict[str, Any]:
            runtimes = self.ctx.java_runtimes()
            return {"runtimes": [rt.as_dict() for rt in runtimes]}

        def done(result: dict[str, Any]) -> None:
            self._java = list(result.get("runtimes") or [])
            self.javaChanged.emit()

        self._run("java", work, done)

    @Slot(str, str, str, str, str)
    @Slot(str, str, str, str, str, bool)
    def createInstance(
        self,
        name: str,
        mc_version: str,
        loader: str,
        loader_version: str,
        account_id: str,
        allow_execution: bool = False,
    ) -> None:
        def work() -> dict[str, Any]:
            instance = self.ctx.instances.create(
                name=name,
                mc_version=mc_version,
                loader=loader or "vanilla",
                loader_version=loader_version,
                account_id=account_id or None,
                memory_mb=self.ctx.settings.default_memory_mb,
                width=854,
                height=480,
            )
            try:
                if loader and loader != "vanilla":
                    self.ctx.modpacks.java_path = self.ctx.settings.java_path
                    self.ctx.modpacks._install_loader(
                        instance, loader, loader_version, allow_execution=allow_execution
                    )
            except BaseException:
                self.ctx.instances.delete(instance.id)
                raise
            return {"id": instance.id, "name": instance.name}

        def done(result: dict[str, Any]) -> None:
            self._refresh_instances()
            self.selectInstance(result["id"])
            self.notify.emit("success", "instance.created", result["name"])

        self._busy = "instance"
        self.busyChanged.emit()
        self._run("create-instance", work, done)

    @Slot(str)
    def deleteInstance(self, instance_id: str) -> None:
        def work() -> dict[str, Any]:
            instance = self.ctx.instances.get(instance_id)
            self.ctx.launcher.delete_instance(instance_id, self.ctx.instances.delete)
            return {"name": instance.name}

        def done(result: dict[str, Any]) -> None:
            if self._selected_id == instance_id:
                self._selected_id = ""
            self._refresh_instances()
            self.notify.emit("success", "instance.deleted", result["name"])

        self._run("delete-instance", work, done)

    @Slot(str)
    def duplicateInstance(self, instance_id: str) -> None:
        def work() -> dict[str, Any]:
            clone = self.ctx.instances.duplicate(instance_id)
            return {"id": clone.id, "name": clone.name}

        def done(result: dict[str, Any]) -> None:
            self._refresh_instances()
            self.selectInstance(result["id"])

        self._run("duplicate-instance", work, done)

    @Slot(str, str)
    def renameInstance(self, instance_id: str, name: str) -> None:
        def work() -> dict[str, Any]:
            instance = self.ctx.instances.rename(instance_id, name)
            return {"id": instance.id, "name": instance.name}

        def done(result: dict[str, Any]) -> None:
            self._refresh_instances()

        self._run("rename-instance", work, done)

    @Slot(str, str, str, str, str, int, int, int)
    def updateInstance(
        self,
        instance_id: str,
        name: str,
        account_id: str,
        jvm_args: str,
        game_args: str,
        memory_mb: int,
        width: int,
        height: int,
    ) -> None:
        def work() -> dict[str, Any]:
            instance = self.ctx.instances.get(instance_id)
            instance.name = name or instance.name
            instance.account_id = account_id or None
            instance.jvm_args = jvm_args
            instance.game_args = game_args
            instance.memory_mb = max(512, int(memory_mb))
            instance.width = max(320, int(width))
            instance.height = max(240, int(height))
            self.ctx.instances.update(instance)
            return {"id": instance.id}

        def done(result: dict[str, Any]) -> None:
            self._refresh_instances()

        self._run("update-instance", work, done)

    @Slot(str)
    def selectInstance(self, instance_id: str) -> None:
        self._selected_id = instance_id
        self.selectionChanged.emit()

    # ------------------------------------------------------------------
    # launching
    # ------------------------------------------------------------------
    @Slot(str)
    def installInstance(self, instance_id: str) -> None:
        def work() -> dict[str, Any]:
            instance = self.ctx.instances.get(instance_id)
            installed = self.ctx.launcher.ensure_installed(
                instance,
                on_stage=lambda stage: self._signals.progress.emit(
                    "install",
                    {"stage": stage.name, "detail": stage.detail, "percent": stage.percent},
                ),
            )
            return {"version": installed.version_id, "classpath": len(installed.classpath)}

        def done(result: dict[str, Any]) -> None:
            self._refresh_instances()
            self.notify.emit("success", "instance.installed", str(result.get("version", "")))

        self._busy = "install"
        self.busyChanged.emit()
        self._run("install", work, done)

    @Slot(str)
    def launchInstance(self, instance_id: str) -> None:
        def work() -> dict[str, Any]:
            instance = self.ctx.instances.get(instance_id)
            account_id = instance.account_id or ""
            if not account_id:
                default_account = self.ctx.accounts.default_account()
                if default_account is None:
                    raise ApeironError(
                        "no account is available",
                        hint="Create an offline profile or sign in with Microsoft first.",
                    )
                account_id = default_account.id
                instance.account_id = account_id
                self.ctx.instances.update(instance)
            session = self.ctx.accounts.session_for(account_id)
            self.ctx.launcher.launch(
                instance,
                player_name=session.player_name,
                uuid=session.uuid,
                access_token=session.access_token,
                user_type=session.user_type,
                xuid=session.xuid,
                client_id=session.client_id,
                on_line=self._game_line,
                on_exit=self._game_exit,
                on_stage=lambda stage: self._signals.progress.emit(
                    "install",
                    {"stage": stage.name, "detail": stage.detail, "percent": stage.percent},
                ),
            )
            return {"player": session.player_name, "verified": session.verified}

        def done(result: dict[str, Any]) -> None:
            self._status = {
                "kind": "running",
                "player": result.get("player", ""),
                "verified": result.get("verified", False),
            }
            self.statusChanged.emit()
            self._refresh_instances()

        self._busy = "launch"
        self.busyChanged.emit()
        self._run("launch", work, done)

    def _game_line(self, line: str) -> None:
        self._console = (self._console + line + "\n")[-60000:]
        self.consoleChanged.emit()

    def _game_exit(self, code: int) -> None:
        self._status = {"kind": "exited", "code": code}
        self.statusChanged.emit()
        self._refresh_instances()
        self._refresh_logs()
        self.notify.emit(
            "info" if code == 0 else "warning",
            "game.exited",
            f"exit code {code}",
        )

    @Slot()
    def stopGame(self) -> None:
        def work() -> dict[str, Any]:
            return {"code": self.ctx.launcher.stop()}

        self._run("stop", work)

    # ------------------------------------------------------------------
    # accounts
    # ------------------------------------------------------------------
    @Slot(str)
    def createOfflineAccount(self, name: str) -> None:
        def work() -> dict[str, Any]:
            account = self.ctx.accounts.add_offline(name)
            return {"id": account.id, "name": account.name}

        def done(result: dict[str, Any]) -> None:
            self._refresh_accounts()
            self.notify.emit("success", "account.created", result["name"])

        self._run("offline-account", work, done)

    @Slot(str)
    def removeAccount(self, account_id: str) -> None:
        def work() -> dict[str, Any]:
            self.ctx.accounts.remove(account_id)
            return {}

        def done(result: dict[str, Any]) -> None:
            self._refresh_accounts()
            self._refresh_instances()

        self._run("remove-account", work, done)

    @Slot()
    def beginMicrosoftLogin(self) -> None:
        def work() -> dict[str, Any]:
            pending = self.ctx.accounts.begin_microsoft_login()
            self._pending_login = pending
            return pending.public()

        def done(result: dict[str, Any]) -> None:
            self._device_code = dict(result)
            self._device_code["status"] = "waiting"
            self.deviceCodeChanged.emit()
            self._start_login_poll()

        self._run("ms-begin", work, done)

    def _start_login_poll(self) -> None:
        pending = self._pending_login
        if pending is None:
            return

        def work() -> dict[str, Any]:
            account = self.ctx.accounts.complete_microsoft_login(pending)
            return {"id": account.id, "name": account.name}

        def done(result: dict[str, Any]) -> None:
            self._device_code = {"status": "done", "account": result.get("name", "")}
            self.deviceCodeChanged.emit()
            self._pending_login = None
            self._refresh_accounts()
            self.notify.emit("success", "account.signedIn", str(result.get("name", "")))

        self._busy = "login"
        self.busyChanged.emit()
        self._run("ms-complete", work, done)

    @Slot()
    def cancelMicrosoftLogin(self) -> None:
        pending = self._pending_login
        if pending is not None:
            pending.cancel.set()
        self._device_code = {"status": "cancelled"}
        self.deviceCodeChanged.emit()

    @Slot(str, str)
    def selectAccount(self, account_id: str, instance_id: str = "") -> None:
        """Bind an account to an instance (or to the current selection)."""

        def work() -> dict[str, Any]:
            target = instance_id or self._selected_id
            if not target:
                return {}
            instance = self.ctx.instances.get(target)
            instance.account_id = account_id or None
            self.ctx.instances.update(instance)
            return {"id": target}

        def done(result: dict[str, Any]) -> None:
            self._refresh_instances()

        self._run("select-account", work, done)

    # ------------------------------------------------------------------
    # settings, theme, language
    # ------------------------------------------------------------------
    @Slot(str)
    def setLanguage(self, code: str) -> None:
        if code not in LANGUAGES and code != "system":
            return
        self.ctx.settings_store.update(language=code)
        manager = getattr(self, "_translation_manager", None)
        if manager is not None:
            manager.apply(code)
        # qsTr() results are cached inside the compiled bindings, and
        # QQmlEngine.retranslate() does not reliably refresh strings that were
        # resolved during component construction.  Rebuilding the component
        # tree guarantees every string is looked up again; all UI state lives in
        # this backend, so the visible selection survives the reload.
        if self._engine is not None and self._qml_url is not None:
            for obj in self._engine.rootObjects():
                obj.deleteLater()
            QTimer.singleShot(0, self._reload_interface)
        self.languageChanged.emit()
        self.settingsChanged.emit()

    @Slot(str)
    def setTheme(self, mode: str) -> None:
        if mode not in ("system", "light", "dark"):
            return
        self.ctx.settings_store.update(theme=mode)
        self.themeChanged.emit()
        self.settingsChanged.emit()

    @Slot(str, "QVariant")
    def setSetting(self, key: str, value: Any) -> None:
        allowed = {
            "max_concurrent_downloads": int,
            "download_timeout": float,
            "download_retries": int,
            "default_memory_mb": int,
            "java_path": str,
            "default_jvm_args": str,
            "check_updates": bool,
            "show_snapshots": bool,
            "log_level": str,
        }
        caster = allowed.get(key)
        if caster is None:
            return
        try:
            cast = caster(value)
        except (TypeError, ValueError):
            return
        self.ctx.settings_store.update(**{key: cast})
        if key == "max_concurrent_downloads":
            self.ctx.downloader.concurrency = max(1, min(16, cast))
        if key == "show_snapshots":
            self.load_versions()
        self.settingsChanged.emit()

    # ------------------------------------------------------------------
    # content, modpacks, files
    # ------------------------------------------------------------------
    @Slot(str)
    def importInstance(self, path: str) -> None:
        def work() -> dict[str, Any]:
            instance = self.ctx.instances.import_instance(local_path(path))
            return {"id": instance.id}

        def done(result: dict[str, Any]) -> None:
            self._refresh_instances()
            self.selectInstance(result["id"])

        self._run("import-instance", work, done)

    @Slot(str)
    @Slot(str, bool)
    def importModpack(self, path: str, allow_execution: bool = False) -> None:
        def work() -> dict[str, Any]:
            self.ctx.modpacks.java_path = self.ctx.settings.java_path
            result = self.ctx.modpacks.import_archive(
                local_path(path), allow_execution=allow_execution
            )
            return result.as_dict()

        def done(result: dict[str, Any]) -> None:
            self._refresh_instances()
            self.notify.emit("success", "modpack.imported", result.get("instanceName", ""))

        self._busy = "modpack"
        self.busyChanged.emit()
        self._run("import-modpack", work, done)

    @Slot(str, str)
    def exportInstance(self, instance_id: str, target: str) -> None:
        def work() -> dict[str, Any]:
            instance = self.ctx.instances.get(instance_id)
            path = local_path(target)
            if path.suffix.lower() == ".mrpack":
                out = self.ctx.modpacks.export_mrpack(instance, path)
            else:
                out = self.ctx.instances.export(instance_id, path)
            return {"path": str(out)}

        def done(result: dict[str, Any]) -> None:
            self.notify.emit("success", "instance.exported", result["path"])

        self._run("export-instance", work, done)

    @Slot(str, str, str)
    def installContent(self, instance_id: str, path: str, kind: str) -> None:
        def work() -> dict[str, Any]:
            instance = self.ctx.instances.get(instance_id)
            record = self.ctx.modpacks.install_content_file(instance, local_path(path), kind)
            return {"filename": record.filename}

        def done(result: dict[str, Any]) -> None:
            self.contentChanged.emit()
            self.notify.emit("success", "content.installed", result["filename"])

        self._run("install-content", work, done)

    @Slot(str, str, bool)
    def toggleContent(self, instance_id: str, record_id: str, enabled: bool) -> None:
        def work() -> dict[str, Any]:
            instance = self.ctx.instances.get(instance_id)
            records = self.ctx.mods_repo.list_for_instance(instance_id)
            record = next((r for r in records if r.id == record_id), None)
            if record is None:
                return {}
            self.ctx.modpacks.set_content_enabled(instance, record, enabled)
            return {}

        self._run("toggle-content", work, lambda result: self.contentChanged.emit())

    @Slot(str, str)
    def removeContent(self, instance_id: str, record_id: str) -> None:
        def work() -> dict[str, Any]:
            instance = self.ctx.instances.get(instance_id)
            records = self.ctx.mods_repo.list_for_instance(instance_id)
            record = next((r for r in records if r.id == record_id), None)
            if record is not None:
                self.ctx.modpacks.remove_content(instance, record)
            return {}

        self._run("remove-content", work, lambda result: self.contentChanged.emit())

    @Slot(str)
    def contentFor(self, instance_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": record.id,
                "filename": record.filename,
                "kind": record.kind,
                "enabled": record.enabled,
                "size": record.size,
                "source": record.source,
            }
            for record in self.ctx.mods_repo.list_for_instance(instance_id)
        ]

    @Slot(str, result="QVariantList")
    def contentList(self, instance_id: str) -> list[dict[str, Any]]:
        return self.contentFor(instance_id)

    @Slot(str)
    def openPath(self, kind: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        mapping = {
            "root": self.ctx.paths.root,
            "logs": self.ctx.paths.logs,
            "instances": self.ctx.paths.instances,
            "backups": self.ctx.paths.backups,
            "instance": Path(self.selectedInstance.get("directory", ""))
            if self.selectedInstance
            else None,
        }
        target = mapping.get(kind)
        if target is None:
            for item in self._instances:
                if item["id"] == self._selected_id:
                    target = self.ctx.paths.instance_dir(item["id"])
                    break
        if target is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    @Slot()
    def exportDiagnostics(self) -> None:
        def work() -> dict[str, Any]:
            target = self.ctx.paths.logs / f"diagnostics-{int(time.time())}.txt"
            info = self.about
            lines = [
                "Apeiron diagnostics",
                f"version: {info['version']}",
                f"platform: {info['platform']}",
                f"python: {info['python']}",
                f"qt: {info['qt']}",
                f"data root: {info['dataRoot']}",
                f"credential backend: {info['credentialBackend']}"
                f" (secure={info['credentialSecure']})",
                "",
                "log statistics:",
                json.dumps(info["logStats"], indent=2),
                "",
                "recent launches:",
                json.dumps(self.recentLaunches, indent=2),
                "",
                "last 200 log lines:",
                self._tail_log(200),
            ]
            target.write_text("\n".join(lines), encoding="utf-8")
            return {"path": str(target)}

        def done(result: dict[str, Any]) -> None:
            self.notify.emit("success", "diagnostics.exported", result["path"])

        self._run("diagnostics", work, done)

    def _tail_log(self, lines: int) -> str:
        path = self.ctx.paths.log_file
        if not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        from apeiron.core.logging import redact

        text = "\n".join(content[-lines:])
        masked, _ = redact(text)
        return masked

    @Slot()
    def refreshLogs(self) -> None:
        self._refresh_logs()

    def _refresh_logs(self) -> None:
        self._log_text = self._tail_log(400)
        self.logChanged.emit()

    # ------------------------------------------------------------------
    # java, updates, bedrock
    # ------------------------------------------------------------------
    @Slot(int)
    def installJava(self, major: int) -> None:
        def work() -> dict[str, Any]:
            from apeiron.mc.java import AdoptiumClient, install_runtime

            client = AdoptiumClient(self.ctx.http)
            package = client.latest(major)

            def download(url: str, target: Path, sha256: str) -> None:
                from apeiron.net.downloader import DownloadSpec

                self.ctx.downloader.run(
                    [DownloadSpec(url=url, destination=target, category="java")]
                )
                if sha256:
                    import hashlib

                    digest = hashlib.sha256()
                    with target.open("rb") as handle:
                        for block in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(block)
                    if digest.hexdigest() != sha256:
                        target.unlink(missing_ok=True)
                        raise ApeironError("the downloaded Java package failed verification")

            runtime = install_runtime(package, self.ctx.paths.java, download=download)
            return runtime.as_dict()

        def done(result: dict[str, Any]) -> None:
            self.ctx.settings_store.update(java_path=result["path"])
            self.ctx.modpacks.java_path = result["path"]
            self.settingsChanged.emit()
            self.detect_java()
            self.notify.emit("success", "java.installed", str(result.get("version", "")))

        self._busy = "java"
        self.busyChanged.emit()
        self._run("install-java", work, done)

    @Slot()
    def checkUpdates(self) -> None:
        def work() -> dict[str, Any]:
            info = self.ctx.updates.check()
            return {"update": info.as_dict() if info else None}

        def done(result: dict[str, Any]) -> None:
            update = result.get("update")
            if update:
                self.notify.emit("info", "update.available", str(update.get("version")))
            else:
                self.notify.emit("info", "update.none", "")

        self._run("update-check", work, done)

    @Slot()
    def refreshBedrock(self) -> None:
        def work() -> dict[str, Any]:
            return self.ctx.bedrock.status()

        def done(result: dict[str, Any]) -> None:
            self._bedrock = dict(result)
            self.bedrockChanged.emit()

        self._run("bedrock", work, done)

    @Slot(str)
    def launchBedrock(self, family_name: str) -> None:
        def work() -> dict[str, Any]:
            installations = self.ctx.bedrock.detect()
            target = next((i for i in installations if i.family_name == family_name), None)
            if target is None and installations:
                target = installations[0]
            uri = self.ctx.bedrock.launch(target)
            return {"uri": uri}

        self._run("bedrock-launch", work)

    @Slot(str, result="QVariantList")
    def loaderVersions(self, mc_version: str) -> list[dict[str, Any]]:
        return self._loader_cache.get(mc_version, [])

    @Slot(str, str)
    def fetchLoaderVersions(self, mc_version: str, loader: str) -> None:
        """Fetch versions for one loader; results land in ``App.loaders``."""
        if not mc_version or not loader or loader == "vanilla":
            self._loaders = []
            self.loadersChanged.emit()
            return

        def work() -> dict[str, Any]:
            provider = self.ctx.modpacks.loaders.get(loader)
            if provider is None:
                return {"loaders": []}
            entries: list[dict[str, Any]] = []
            for version in provider.versions(mc_version)[:40]:
                entry = version.as_dict()
                entry["kind"] = loader
                entries.append(entry)
            return {"loaders": entries}

        def done(result: dict[str, Any]) -> None:
            self._loaders = list(result.get("loaders") or [])
            self.loadersChanged.emit()

        self._run("loader-versions", work, done)

    @Slot(str)
    def backupInstance(self, instance_id: str) -> None:
        def work() -> dict[str, Any]:
            info = self.ctx.instances.backup(instance_id)
            return {"name": info.name, "path": str(info.path)}

        def done(result: dict[str, Any]) -> None:
            self.notify.emit("success", "instance.backedUp", str(result.get("name", "")))

        self._run("backup", work, done)

    def shutdown(self, timeout_ms: int = 5000) -> None:
        """Stop background work before the engine is destroyed."""
        try:
            if self.ctx.settings.close_game_on_launcher_exit:
                self.ctx.launcher.stop()
        except Exception:
            pass
        self._pool.waitForDone(timeout_ms)

    @Slot()
    def quit(self) -> None:
        self.shutdown()
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.quit()


def _qt_version() -> str:
    from PySide6.QtCore import qVersion

    return qVersion()


def local_path(value: str) -> Path:
    url = QUrl(value)
    if url.isLocalFile():
        return Path(url.toLocalFile())
    if url.scheme() and not (len(value) > 1 and value[1] == ":"):
        raise ApeironError("a local file path is required")
    return Path(value)
