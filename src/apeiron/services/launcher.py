"""Launch orchestration: install-if-needed, pick Java, start, monitor, stop."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from apeiron.core.errors import JavaError, LaunchError
from apeiron.core.http import HttpClient
from apeiron.core.logging import get_logger
from apeiron.core.paths import AppPaths
from apeiron.db.models import Instance
from apeiron.db.repositories import InstanceRepository, LaunchRepository
from apeiron.mc.installer import InstalledVersion, InstallStage, JavaInstaller
from apeiron.mc.java import JavaRuntime, select_runtime
from apeiron.mc.launch import GameProcess, LaunchOptions, build_command, create_launch_log
from apeiron.mc.manifest import VersionManifest

log = get_logger("launcher")


@dataclass
class LaunchState:
    instance_id: str = ""
    phase: str = "idle"  # idle|preparing|starting|running|exited|failed
    detail: str = ""
    pids: int = 0
    exit_code: int | None = None
    started_at: float = 0.0


class LaunchService:
    def __init__(
        self,
        paths: AppPaths,
        http: HttpClient,
        manifest: VersionManifest,
        installer: JavaInstaller,
        instances: InstanceRepository,
        launches: LaunchRepository,
        *,
        java_runtimes: Callable[[], list[JavaRuntime]],
        default_java: Callable[[], str],
    ) -> None:
        self.paths = paths
        self.http = http
        self.manifest = manifest
        self.installer = installer
        self.instances_repo = instances
        self.launches = launches
        self._java_runtimes = java_runtimes
        self._default_java = default_java
        self.state = LaunchState()
        self.process: GameProcess | None = None
        self._lock = threading.Lock()

    # -- preparation --------------------------------------------------------
    def resolve_version_id(self, instance: Instance) -> str:
        if instance.loader and instance.loader != "vanilla":
            version_id = str(instance.metadata.get("loaderVersionId") or "")
            if not version_id:
                raise LaunchError(
                    f"the {instance.loader} installation for this instance is incomplete",
                    hint="Re-apply the loader from the instance settings.",
                )
            return version_id
        return instance.mc_version

    def ensure_installed(
        self, instance: Instance, on_stage: Callable[[InstallStage], None] | None = None
    ) -> InstalledVersion:
        version_id = self.resolve_version_id(instance)
        self.state = LaunchState(instance_id=instance.id, phase="preparing", detail=version_id)
        installed = self.installer.install(version_id, on_stage=on_stage)
        self.instances_repo.set_installed(instance.id, True)
        return installed

    def choose_java(self, instance: Instance, installed: InstalledVersion) -> JavaRuntime:
        required = installed.descriptor.java_major or 8
        if instance.java_path:
            for runtime in self._java_runtimes():
                if str(runtime.path) == instance.java_path:
                    return runtime
            from apeiron.mc.java import probe_java

            runtime = probe_java(Path(instance.java_path))
            if runtime.major < required:
                raise JavaError(
                    f"the configured Java is {runtime.major}, but Minecraft {instance.mc_version} "
                    f"requires {required}"
                )
            return runtime

        default_java = self._default_java()
        if default_java:
            from apeiron.mc.java import probe_java

            runtime = probe_java(Path(default_java))
            if runtime.major >= required:
                return runtime

        selected = select_runtime(required, self._java_runtimes())
        if selected is None:
            raise JavaError(
                f"no Java {required}+ runtime was found",
                hint=(
                    "Install a runtime from Settings > Java, or point Apeiron at an existing one."
                ),
            )
        return selected

    # -- launching ----------------------------------------------------------
    def launch(
        self,
        instance: Instance,
        *,
        player_name: str,
        uuid: str,
        access_token: str = "0",
        user_type: str = "legacy",
        xuid: str = "",
        client_id: str = "",
        on_line: Callable[[str], None] | None = None,
        on_exit: Callable[[int], None] | None = None,
        on_stage: Callable[[InstallStage], None] | None = None,
    ) -> GameProcess:
        with self._lock:
            if self.process is not None and self.process.running:
                raise LaunchError("a game is already running")

            installed = self.ensure_installed(instance, on_stage)
            runtime = self.choose_java(instance, installed)

            instance_dir = Path(instance.directory)
            if not instance_dir.exists():
                raise LaunchError(f"the instance directory is missing: {instance_dir}")

            self.installer.materialize_resources(installed, instance_dir)

            options = LaunchOptions(
                instance_name=instance.name,
                game_directory=instance_dir,
                java=runtime,
                player_name=player_name,
                uuid=uuid,
                access_token=access_token,
                user_type=user_type,
                xuid=xuid,
                client_id=client_id,
                memory_mb=instance.memory_mb,
                width=instance.width,
                height=instance.height,
                fullscreen=instance.fullscreen,
                extra_jvm_args=instance.jvm_args,
                extra_game_args=instance.game_args,
            )
            command = build_command(installed, options)
            log_path = create_launch_log(instance_dir)
            record = self.launches.start(instance.id, str(log_path))

            self.state = LaunchState(
                instance_id=instance.id,
                phase="starting",
                detail=str(runtime.path),
                started_at=time.time(),
            )

            started_at = self.state.started_at

            def _exit(code: int) -> None:
                duration = max(0, int(time.time() - started_at))
                status = "exited" if code == 0 else "crashed"
                self.launches.finish(record.id, code, status, duration)
                self.instances_repo.mark_played(instance.id, duration)
                self.state = LaunchState(
                    instance_id=instance.id, phase="exited", exit_code=code, detail=status
                )
                if on_exit is not None:
                    on_exit(code)

            process = GameProcess(
                command,
                cwd=instance_dir,
                log_path=log_path,
                on_line=on_line,
                on_exit=_exit,
            )
            try:
                process.start()
            except Exception:
                self.launches.finish(record.id, -1, "failed", 0)
                self.state = LaunchState(instance_id=instance.id, phase="exited", exit_code=-1)
                raise
            self.process = process
            if process.running:
                self.state = LaunchState(
                    instance_id=instance.id,
                    phase="running",
                    detail=str(runtime.path),
                    pids=process.pid or 0,
                    started_at=started_at,
                )
            return process

    def stop(self) -> int | None:
        if self.process is None:
            return None
        code = self.process.stop()
        return code

    def delete_instance(self, instance_id: str, delete: Callable[[str], None]) -> None:
        with self._lock:
            if self.state.instance_id == instance_id and self.running:
                raise LaunchError("cannot delete an instance while its game is running")
            delete(instance_id)

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.running

    def console_snapshot(self) -> str:
        return self.process.snapshot() if self.process else ""

    # -- verification helpers ----------------------------------------------
    def verify_instance(self, instance: Instance) -> list[str]:
        problems: list[str] = []
        try:
            version_id = self.resolve_version_id(instance)
        except LaunchError as exc:
            return [exc.message]
        problems.extend(self.installer.verify(version_id))
        if instance.account_id:
            pass  # account validity is checked by the account service
        return problems
