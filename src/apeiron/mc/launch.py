"""Launch command construction and game process management.

Command building is a pure function of the resolved version descriptor, the
chosen runtime and the account, which makes it fully unit-testable and keeps
"one hard-coded launch command for every version" impossible.

Process management runs Minecraft as a separate process, drains both output
pipes on dedicated threads (so the child can never block on a full pipe), mirrors
output into a per-launch log file and reports a structured exit status.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from apeiron.core import __version__ as apeiron_version
from apeiron.core.errors import LaunchError
from apeiron.core.logging import get_logger
from apeiron.core.paths import current_platform_key
from apeiron.mc.installer import InstalledVersion
from apeiron.mc.java import JavaRuntime
from apeiron.mc.version import resolve_arguments, split_legacy_arguments

log = get_logger("launch")

LAUNCHER_NAME = "Apeiron"
MAX_CONSOLE_LINES = 4000


@dataclass
class LaunchOptions:
    instance_name: str
    game_directory: Path
    java: JavaRuntime
    player_name: str
    uuid: str
    access_token: str = "0"
    user_type: str = "legacy"  # msa for Microsoft accounts, legacy for offline
    xuid: str = ""
    client_id: str = ""
    memory_mb: int = 4096
    min_memory_mb: int = 0
    width: int = 854
    height: int = 480
    fullscreen: bool = False
    extra_jvm_args: str = ""
    extra_game_args: str = ""
    demo: bool = False
    quick_play_singleplayer: str = ""
    quick_play_multiplayer: str = ""
    icon_path: str = ""

    def features(self) -> dict[str, bool]:
        features = {
            "is_demo_user": self.demo,
            "has_custom_resolution": self.width > 0 and self.height > 0,
            "has_quick_plays_support": bool(
                self.quick_play_singleplayer or self.quick_play_multiplayer
            ),
            "is_quick_play_singleplayer": bool(self.quick_play_singleplayer),
            "is_quick_play_multiplayer": bool(self.quick_play_multiplayer),
            "is_quick_play_realms": False,
        }
        return features


def _split_extra(value: str) -> list[str]:
    """Split user-provided arguments honouring double quotes."""
    if not value or not value.strip():
        return []
    import shlex

    try:
        lexer = shlex.shlex(value, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        lexer.escape = ""
        return list(lexer)
    except ValueError:
        return value.split()


def build_command(installed: InstalledVersion, options: LaunchOptions) -> list[str]:
    """Return the full argv for the game process."""
    merged = installed.descriptor
    platform_key = current_platform_key()
    features = options.features()

    classpath = installed.jvm_classpath()
    values: dict[str, str] = {
        "auth_player_name": options.player_name,
        "version_name": merged.id,
        "game_directory": str(options.game_directory),
        "assets_root": str(installed.assets_root),
        "assets_index_name": installed.asset_index,
        "auth_uuid": options.uuid.replace("-", ""),
        "auth_access_token": options.access_token,
        "auth_session": f"token:{options.access_token}:{options.uuid.replace('-', '')}",
        "user_type": options.user_type,
        "version_type": merged.type or "release",
        "user_properties": "{}",
        "clientid": options.client_id,
        "auth_xuid": options.xuid,
        "natives_directory": str(installed.natives_dir),
        "launcher_name": LAUNCHER_NAME,
        "launcher_version": apeiron_version,
        "classpath": classpath,
        "classpath_separator": os.pathsep,
        "library_directory": str(installed.libraries_root),
        "resolution_width": str(options.width),
        "resolution_height": str(options.height),
        "game_assets": str(installed.assets_root / "virtual" / installed.asset_index),
        "quickPlayPath": "",
        "quickPlaySingleplayer": options.quick_play_singleplayer,
        "quickPlayMultiplayer": options.quick_play_multiplayer,
    }

    jvm_args: list[str] = []
    if merged.arguments_jvm:
        jvm_args.extend(
            resolve_arguments(
                merged.arguments_jvm, values, platform_key=platform_key, features=features
            )
        )
    else:
        # Legacy versions have no structured JVM arguments at all.
        jvm_args.extend([f"-Djava.library.path={installed.natives_dir}", "-cp", classpath])

    memory_flags = [f"-Xmx{options.memory_mb}M"]
    if options.min_memory_mb:
        memory_flags.append(f"-Xms{options.min_memory_mb}M")

    base_jvm = [
        *memory_flags,
        f"-Djava.library.path={installed.natives_dir}",
        "-Dfile.encoding=UTF-8",
        "-Dstdout.encoding=UTF-8",
        "-Dstderr.encoding=UTF-8",
        f"-Dminecraft.launcher.brand={LAUNCHER_NAME}",
        f"-Dminecraft.launcher.version={apeiron_version}",
    ]

    log_config = _log_config_argument(merged, installed)
    if log_config:
        base_jvm.append(log_config)

    # The structured JVM arguments contain $-substitutions that must be applied
    # *before* we append user flags, otherwise a user flag could be swallowed by
    # the classpath expansion.
    command = [str(options.java.path), *base_jvm]
    command.extend(_drop_conflicting(jvm_args, memory_flags))
    command.extend(_split_extra(options.extra_jvm_args))

    command.extend(_main_class_args(merged, installed))

    if merged.arguments_game:
        game_args = resolve_arguments(
            merged.arguments_game, values, platform_key=platform_key, features=features
        )
    else:
        game_args = split_legacy_arguments(merged.legacy_arguments, values)

    # Legacy servers/versions expect resolution flags appended after the class.
    if merged.arguments_game and features["has_custom_resolution"]:
        pass  # already present through rules
    elif not merged.arguments_game:
        game_args.extend(["--width", str(options.width), "--height", str(options.height)])

    command.extend(game_args)
    command.extend(_split_extra(options.extra_game_args))
    return [token for token in command if token != ""]


def _main_class_args(merged, installed: InstalledVersion) -> list[str]:
    """Ensure ``-cp <classpath> <mainClass>`` appears exactly once."""
    return ["-cp", installed.jvm_classpath(), merged.main_class]


def _drop_conflicting(jvm_args: Sequence[str], memory_flags: Sequence[str]) -> list[str]:
    """Drop flags Apeiron owns, so its validated values always win.

    Structured JVM arguments usually end with ``-cp ${classpath}``; the
    classpath is re-added immediately before the main class, which keeps the
    argument order valid while guaranteeing exactly one ``-cp`` pair.
    """
    result: list[str] = []
    skip_next = False
    for token in jvm_args:
        if skip_next:
            skip_next = False
            continue
        if token == "-cp" or token == "-classpath":
            skip_next = True
            continue
        if token in memory_flags:
            continue
        if token.startswith("-Xmx") or token.startswith("-Xms"):
            continue
        if token.startswith("-Djava.library.path="):
            continue
        result.append(token)
    return result


def _log_config_argument(merged, installed: InstalledVersion) -> str:
    logging_block = merged.logging or {}
    client = logging_block.get("client") or {}
    argument = client.get("argument")
    artifact = client.get("file") or {}
    if not argument or not artifact.get("id"):
        return ""
    path = installed.assets_root / "log_configs" / str(artifact.get("id"))
    return str(argument).replace("${path}", str(path))


class GameProcess:
    """A running Minecraft process with drained pipes and a bounded console."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_path: Path,
        on_line: Callable[[str], None] | None = None,
        on_exit: Callable[[int], None] | None = None,
    ) -> None:
        self.command = list(command)
        self.cwd = Path(cwd)
        self.log_path = Path(log_path)
        self.on_line = on_line
        self.on_exit = on_exit
        self.console: deque[str] = deque(maxlen=MAX_CONSOLE_LINES)
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._log_handle: Any = None
        self._lock = threading.Lock()
        self._exit_code: int | None = None
        self.started_at: float = 0.0
        self._stop_requested = False

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> int:
        if self._process is not None:
            raise LaunchError("process already started")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self.log_path.open("w", encoding="utf-8", errors="replace")
        self._log_handle.write(
            f"# Apeiron {apeiron_version} launch at {datetime.now().isoformat()}\n"
        )
        self._log_handle.write(f"# cwd: {self.cwd}\n")
        self._log_handle.write(f"# argv: {_redacted_argv(self.command)}\n\n")
        self._log_handle.flush()

        env = os.environ.copy()
        env.setdefault("JAVA_TOOL_OPTIONS", "")
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._process = subprocess.Popen(
                self.command,
                cwd=str(self.cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
        except (OSError, ValueError) as exc:
            if self._log_handle:
                self._log_handle.close()
                self._log_handle = None
            raise LaunchError("cannot start the Java process", detail=str(exc)) from exc

        self.started_at = time.monotonic()
        log.info("game started pid=%s", self._process.pid)
        self._thread = threading.Thread(target=self._pump, name="apeiron-game-io", daemon=True)
        self._thread.start()
        return self._process.pid

    def _pump(self) -> None:
        assert self._process is not None
        process = self._process
        try:
            if process.stdout is not None:
                buffered = 0
                partial = ""
                for line in process.stdout:
                    buffered += 1
                    partial = line.rstrip("\n")
                    self._append(partial)
                    if buffered % 32 == 0:
                        self._flush_log()
            code: int | None = process.wait()
        except Exception as exc:
            log.error("game pipe reader failed: %s", exc)
            code = process.poll()
        finally:
            self._exit_code = code if code is not None else -1
            self._flush_log()
            if self._log_handle is not None:
                self._log_handle.flush()
                self._log_handle.close()
                self._log_handle = None
            log.info("game exited with code %s", self._exit_code)
            if self.on_exit is not None:
                try:
                    self.on_exit(self._exit_code)
                except Exception:
                    log.exception("exit callback failed")

    def _append(self, line: str) -> None:
        with self._lock:
            self.console.append(line)
        if self._log_handle is not None:
            try:
                self._log_handle.write(line + "\n")
            except (OSError, ValueError):
                pass
        if self.on_line is not None:
            try:
                self.on_line(line)
            except Exception:
                log.exception("line callback failed")
        if "Exception" in line or "ERROR" in line or "Caused by" in line:
            log.warning("game: %s", line[:500])

    def _flush_log(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.flush()
            except (OSError, ValueError):
                pass

    # -- control ------------------------------------------------------------
    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    def wait(self, timeout: float | None = None) -> int | None:
        if self._process is None:
            return self._exit_code
        try:
            self._exit_code = self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        return self._exit_code

    def stop(self, grace: float = 8.0) -> int | None:
        """Terminate politely, then forcefully; never leave an orphan."""
        if self._process is None or self._process.poll() is not None:
            return self._exit_code
        self._stop_requested = True
        log.info("stopping game pid=%s", self._process.pid)
        try:
            if os.name == "nt":
                self._process.terminate()
            else:
                self._process.send_signal(signal.SIGTERM)
        except OSError:
            pass
        try:
            return self._process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            log.warning("game did not exit within %.1fs; killing", grace)
            try:
                self._process.kill()
            except OSError:
                pass
            try:
                return self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                return None

    def join_reader(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def snapshot(self) -> str:
        with self._lock:
            return "\n".join(self.console)


def _redacted_argv(argv: Sequence[str]) -> str:
    """Never write the access token into the launch log."""
    redacted: list[str] = []
    hide_next = False
    for token in argv:
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        if token in ("--accessToken", "--session", "--clientId"):
            redacted.append(token)
            hide_next = True
            continue
        if token.startswith("--accessToken="):
            redacted.append("--accessToken=***")
            continue
        redacted.append(token)
    return " ".join(redacted)


def create_launch_log(instance_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return instance_dir / "logs" / f"game-{stamp}.log"
