"""Launch command construction and process handling."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from apeiron.core.errors import LaunchError
from apeiron.mc.installer import InstalledVersion
from apeiron.mc.java import JavaRuntime
from apeiron.mc.launch import (
    GameProcess,
    LaunchOptions,
    _redacted_argv,
    build_command,
    create_launch_log,
)
from apeiron.mc.version import VersionDescriptor

MODERN_VERSION = {
    "id": "1.20.1",
    "type": "release",
    "mainClass": "net.minecraft.client.main.Main",
    "assets": "5",
    "assetIndex": {"id": "5", "sha1": "s", "size": 1},
    "downloads": {"client": {"url": "https://x/client.jar", "sha1": "c", "size": 10}},
    "javaVersion": {"majorVersion": 17},
    "logging": {
        "client": {
            "argument": "-Dlog4j.configurationFile=${path}",
            "file": {"id": "client-1.12.xml"},
        }
    },
    "arguments": {
        "game": [
            "--username",
            "${auth_player_name}",
            "--version",
            "${version_name}",
            "--gameDir",
            "${game_directory}",
            "--assetsDir",
            "${assets_root}",
            "--assetIndex",
            "${assets_index_name}",
            "--uuid",
            "${auth_uuid}",
            "--accessToken",
            "${auth_access_token}",
            "--userType",
            "${user_type}",
            {
                "rules": [{"action": "allow", "features": {"has_custom_resolution": True}}],
                "value": ["--width", "${resolution_width}", "--height", "${resolution_height}"],
            },
        ],
        "jvm": [
            {
                "rules": [{"action": "allow", "os": {"name": "osx"}}],
                "value": ["-XstartOnFirstThread"],
            },
            "-Djava.library.path=${natives_directory}",
            "-Dminecraft.launcher.brand=${launcher_name}",
            "-cp",
            "${classpath}",
        ],
    },
    "libraries": [
        {
            "name": "org.lwjgl:lwjgl:3.3.1",
            "downloads": {
                "artifact": {
                    "path": "org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1.jar",
                    "url": "u",
                    "sha1": "a",
                }
            },
        }
    ],
}


@pytest.fixture()
def installed(tmp_path: Path) -> InstalledVersion:
    descriptor = VersionDescriptor.from_dict(MODERN_VERSION)
    client = tmp_path / "versions/1.20.1/1.20.1.jar"
    client.parent.mkdir(parents=True)
    client.write_bytes(b"jar")
    library = tmp_path / "libraries/org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1.jar"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"jar")
    return InstalledVersion(
        version_id="1.20.1",
        descriptor=descriptor,
        client_jar=client,
        natives_dir=tmp_path / "versions/1.20.1/natives",
        libraries_root=tmp_path / "libraries",
        assets_root=tmp_path / "assets",
        asset_index="5",
        asset_index_path=tmp_path / "assets/indexes/5.json",
        classpath=[str(library), str(client)],
    )


def _options(tmp_path: Path, **kwargs) -> LaunchOptions:
    defaults = dict(  # noqa: C408 - keyword form keeps the mapping readable
        instance_name="Test",
        game_directory=tmp_path / "instance",
        java=JavaRuntime(path=Path("/usr/bin/java"), major=17, version="17.0.9"),
        player_name="Steve",
        uuid="00000000-0000-0000-0000-000000000001",
        access_token="token-value",
        user_type="msa",
    )
    defaults.update(kwargs)
    return LaunchOptions(**defaults)


def test_modern_command_contains_required_tokens(installed, tmp_path: Path):
    command = build_command(installed, _options(tmp_path))
    assert command[0] == str(Path("/usr/bin/java"))
    assert "-Xmx4096M" in command
    assert "--username" in command and "Steve" in command
    assert "--accessToken" in command and "token-value" in command
    assert "--assetIndex" in command and "5" in command
    assert "--uuid" in command and "0000000000000000000000000000000 1".replace(" ", "") in command
    assert "--width" in command and "854" in command
    assert "net.minecraft.client.main.Main" in command
    # exactly one classpath flag, placed before the main class
    assert command.count("-cp") == 1
    assert command.index("-cp") < command.index("net.minecraft.client.main.Main")
    # log4j configuration is passed
    assert any(arg.startswith("-Dlog4j.configurationFile=") for arg in command)


def test_custom_resolution_and_memory(installed, tmp_path: Path):
    command = build_command(
        installed, _options(tmp_path, memory_mb=2048, min_memory_mb=1024, width=1280, height=720)
    )
    assert "-Xmx2048M" in command
    assert "-Xms1024M" in command
    assert "1280" in command and "720" in command


def test_extra_arguments_are_appended(installed, tmp_path: Path):
    command = build_command(
        installed,
        _options(tmp_path, extra_jvm_args="-XX:+UseG1GC", extra_game_args="--fullscreen"),
    )
    assert "-XX:+UseG1GC" in command
    assert "--fullscreen" in command


def test_legacy_version_uses_synthetic_jvm_args(tmp_path: Path):
    legacy = VersionDescriptor.from_dict(
        {
            "id": "1.8.9",
            "mainClass": "net.minecraft.client.main.Main",
            "assets": "legacy",
            "assetIndex": {"id": "legacy", "url": "u"},
            "minecraftArguments": "--username ${auth_player_name} --gameDir ${game_directory}",
            "libraries": [],
        }
    )
    client = tmp_path / "versions/1.8.9/1.8.9.jar"
    client.parent.mkdir(parents=True)
    client.write_bytes(b"jar")
    installed = InstalledVersion(
        version_id="1.8.9",
        descriptor=legacy,
        client_jar=client,
        natives_dir=tmp_path / "natives",
        libraries_root=tmp_path / "libraries",
        assets_root=tmp_path / "assets",
        asset_index="legacy",
        asset_index_path=tmp_path / "assets/indexes/legacy.json",
        classpath=[str(client)],
    )
    command = build_command(installed, _options(tmp_path, user_type="legacy", access_token="0"))
    assert "-Djava.library.path=" + str(tmp_path / "natives") in command
    assert "--username" in command and "Steve" in command
    assert "--width" in command and "--height" in command
    assert "net.minecraft.client.main.Main" in command


def test_access_token_redaction_in_argv_log():
    argv = ["java", "-cp", "a.jar", "Main", "--accessToken", "SECRET", "--uuid", "u"]
    rendered = _redacted_argv(argv)
    assert "SECRET" not in rendered
    assert "--accessToken ***" in rendered


def test_launch_log_path(tmp_path: Path):
    path = create_launch_log(tmp_path)
    assert path.parent == tmp_path / "logs"
    assert path.name.startswith("game-")
    assert path.suffix == ".log"


def test_game_process_captures_output(tmp_path: Path):
    script = tmp_path / "child.py"
    script.write_text(
        "import sys, time\n"
        "print('hello from game', flush=True)\n"
        "print('second line', flush=True)\n"
        "time.sleep(0.1)\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    lines: list[str] = []
    exited: list[int] = []
    process = GameProcess(
        [sys.executable, str(script)],
        cwd=tmp_path,
        log_path=tmp_path / "game.log",
        on_line=lines.append,
        on_exit=exited.append,
    )
    process.start()
    code = process.wait(timeout=20)
    process.join_reader(timeout=5)
    assert code == 3
    assert any("hello from game" in line for line in lines)
    assert exited == [3]
    log_text = (tmp_path / "game.log").read_text(encoding="utf-8")
    assert "hello from game" in log_text
    assert "second line" in log_text


def test_game_process_handles_missing_executable(tmp_path: Path):
    process = GameProcess(
        [str(tmp_path / "does-not-exist")], cwd=tmp_path, log_path=tmp_path / "x.log"
    )
    with pytest.raises(LaunchError):
        process.start()


def test_game_process_stop(tmp_path: Path):
    script = tmp_path / "sleeper.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    process = GameProcess([sys.executable, str(script)], cwd=tmp_path, log_path=tmp_path / "s.log")
    process.start()
    assert process.running
    code = process.stop(grace=5)
    process.join_reader(timeout=5)
    assert code is not None
    assert not process.running
