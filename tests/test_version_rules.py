"""Version metadata: rules, inheritance, argument expansion, classpath."""

from __future__ import annotations

from pathlib import Path

import pytest

from apeiron.core.errors import ConfigError
from apeiron.mc.version import (
    Library,
    Rule,
    VersionDescriptor,
    check_java_compatibility,
    classpath_entries,
    resolve_arguments,
    rules_allow,
    split_legacy_arguments,
)


def test_rules_without_conditions_allow():
    assert rules_allow([]) is True
    assert rules_allow(None) is True


def test_os_rule_allow_only_on_windows():
    rules = [Rule(action="allow", os_name="windows")]
    assert rules_allow(rules, platform_key="windows") is True
    assert rules_allow(rules, platform_key="linux") is False


def test_disallow_beats_allow_when_last():
    rules = [
        Rule(action="allow"),
        Rule(action="disallow", os_name="linux"),
    ]
    assert rules_allow(rules, platform_key="linux") is False
    assert rules_allow(rules, platform_key="windows") is True


def test_feature_rules():
    rules = [Rule(action="allow", features={"is_demo_user": True})]
    assert rules_allow(rules, features={"is_demo_user": True}) is True
    assert rules_allow(rules, features={"is_demo_user": False}) is False


def test_arch_rule_uses_platform_machine(monkeypatch):
    import platform as platform_module

    monkeypatch.setattr(platform_module, "machine", lambda: "aarch64")
    from apeiron.mc import version as version_module

    monkeypatch.setattr(version_module.platform, "machine", lambda: "aarch64")
    rules = [Rule(action="allow", os_arch="x86_64")]
    assert rules_allow(rules, platform_key="linux") is False
    rules = [Rule(action="allow", os_arch="arm64")]
    assert rules_allow(rules, platform_key="linux") is True


def test_inheritance_merges_libraries_and_arguments():
    parent = VersionDescriptor.from_dict(
        {
            "id": "1.20.1",
            "mainClass": "net.minecraft.client.main.Main",
            "assets": "5",
            "assetIndex": {"id": "5", "sha1": "abc", "size": 10},
            "libraries": [
                {
                    "name": "a:b:1",
                    "downloads": {"artifact": {"path": "a/b/1/b-1.jar", "url": "https://x/1"}},
                },
                {
                    "name": "a:c:1",
                    "downloads": {"artifact": {"path": "a/c/1/c-1.jar", "url": "https://x/2"}},
                },
            ],
            "arguments": {
                "game": ["--username", "${auth_player_name}"],
                "jvm": ["-cp", "${classpath}"],
            },
            "javaVersion": {"majorVersion": 17},
        }
    )
    child = VersionDescriptor.from_dict(
        {
            "id": "fabric-loader-0.16.5-1.20.1",
            "inheritsFrom": "1.20.1",
            "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
            "libraries": [
                {
                    "name": "a:c:2",
                    "downloads": {"artifact": {"path": "a/c/2/c-2.jar", "url": "https://x/3"}},
                },
            ],
        }
    )
    child.parent = parent
    merged = child.merged()

    assert merged.main_class.startswith("net.fabricmc")
    assert merged.java_major == 17
    assert merged.asset_index["id"] == "5"
    names = [library.name for library in merged.libraries]
    assert "a:c:1" not in names  # overridden by the child
    assert "a:c:2" in names
    assert "a:b:1" in names
    assert merged.arguments_game[0] == "--username"
    assert "-cp" in merged.arguments_jvm


def test_child_without_assets_inherits_parent():
    parent = VersionDescriptor.from_dict(
        {"id": "p", "assets": "legacy", "assetIndex": {"id": "legacy", "url": "https://a"}}
    )
    child = VersionDescriptor.from_dict({"id": "c", "inheritsFrom": "p"})
    child.parent = parent
    merged = child.merged()
    assert merged.assets == "legacy"
    assert merged.asset_index["id"] == "legacy"


def test_arguments_expansion_and_rules():
    template = [
        "-Xmx2G",
        {"rules": [{"action": "allow", "os": {"name": "windows"}}], "value": "-Dwin=1"},
        {"rules": [{"action": "allow", "os": {"name": "linux"}}], "value": ["-Dlinux=1"]},
        "--username",
        "${auth_player_name}",
        "-DlibraryDirectory=${library_directory}",
    ]
    values = {"auth_player_name": "Steve", "library_directory": "/libs"}
    assert resolve_arguments(template, values, platform_key="linux") == [
        "-Xmx2G",
        "-Dlinux=1",
        "--username",
        "Steve",
        "-DlibraryDirectory=/libs",
    ]
    assert "-Dwin=1" in resolve_arguments(template, values, platform_key="windows")


def test_unknown_placeholder_is_kept():
    assert resolve_arguments(["${unknown_key}"], {}) == ["${unknown_key}"]


def test_legacy_arguments_split():
    tokens = split_legacy_arguments(
        "--username ${auth_player_name} --gameDir ${game_directory}", {"auth_player_name": "A"}
    )
    assert tokens[0] == "--username"
    assert tokens[1] == "A"


def test_library_parsing_and_paths():
    library = Library.from_dict(
        {
            "name": "org.lwjgl:lwjgl:3.3.1",
            "downloads": {
                "artifact": {
                    "path": "org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1.jar",
                    "url": "https://libraries/x.jar",
                    "sha1": "deadbeef",
                    "size": 100,
                }
            },
        }
    )
    assert library.group == "org.lwjgl"
    assert library.version == "3.3.1"
    assert library.artifact().sha1 == "deadbeef"
    assert library.relative_path() == "org/lwjgl/lwjgl/3.3.1/lwjgl-3.3.1.jar"


def test_library_with_maven_base_url_is_resolved():
    library = Library.from_dict(
        {
            "name": "org.ow2.asm:asm:9.6",
            "url": "https://maven.fabricmc.net/",
            "sha1": "abc",
            "size": 42,
        }
    )
    resolved = library.resolved_artifact()
    assert resolved is not None
    assert resolved.url == "https://maven.fabricmc.net/org/ow2/asm/asm/9.6/asm-9.6.jar"
    assert resolved.path == "org/ow2/asm/asm/9.6/asm-9.6.jar"
    assert resolved.sha1 == "abc"


def test_natives_selection_per_platform():
    library = Library.from_dict(
        {
            "name": "org.lwjgl:lwjgl:3.3.1",
            "natives": {
                "linux": "natives-linux",
                "windows": "natives-windows",
                "osx": "natives-macos",
            },
            "downloads": {
                "classifiers": {
                    "natives-linux": {"path": "p/linux.jar", "url": "https://x/linux.jar"},
                    "natives-windows": {"path": "p/win.jar", "url": "https://x/win.jar"},
                }
            },
        }
    )
    assert library.native_artifact("linux").path == "p/linux.jar"
    assert library.native_artifact("windows").path == "p/win.jar"
    assert library.native_artifact("macos") is None


def test_classpath_deduplicates(tmp_path: Path):
    first = Library.from_dict(
        {"name": "a:b:1", "downloads": {"artifact": {"path": "a/b/1.jar", "url": "u"}}}
    )
    duplicate = Library.from_dict(
        {"name": "a:b:1", "downloads": {"artifact": {"path": "a/b/1-dup.jar", "url": "u"}}}
    )
    second = Library.from_dict(
        {"name": "a:c:1", "downloads": {"artifact": {"path": "a/c/1.jar", "url": "u"}}}
    )
    entries = classpath_entries([first, duplicate, second], tmp_path, tmp_path / "client.jar")
    assert entries == [
        str(tmp_path / "a/b/1.jar"),
        str(tmp_path / "a/c/1.jar"),
        str(tmp_path / "client.jar"),
    ]


def test_invalid_version_json():
    with pytest.raises(ConfigError):
        VersionDescriptor.from_dict({"mainClass": "X"})


def test_java_compatibility_guard():
    check_java_compatibility(17, 21)
    from apeiron.core.errors import UnsupportedError

    with pytest.raises(UnsupportedError):
        check_java_compatibility(17, 8)


def test_client_download_without_maven_path():
    """The real Mojang JSON has no `path` on downloads.client."""
    descriptor = VersionDescriptor.from_dict(
        {
            "id": "1.20.1",
            "downloads": {
                "client": {
                    "sha1": "0c3ec587af28e5a785c0b4a7b8a30f9a8f78f838",
                    "size": 23028853,
                    "url": "https://piston-data.mojang.com/v1/objects/x/client.jar",
                }
            },
        }
    )
    artifact = descriptor.client_download()
    assert artifact is not None
    assert artifact.url.endswith("client.jar")
    assert artifact.sha1.startswith("0c3ec5")
    assert artifact.path == "1.20.1.jar"


def test_logging_config_without_maven_path():
    descriptor = VersionDescriptor.from_dict(
        {
            "id": "1.20.1",
            "logging": {
                "client": {
                    "argument": "-Dlog4j.configurationFile=${path}",
                    "file": {"id": "client-1.12.xml", "sha1": "abc", "size": 10, "url": "https://x/y"},
                }
            },
        }
    )
    artifact = descriptor.logging_config()
    assert artifact is not None
    assert artifact.path == "client-1.12.xml"
    assert artifact.url == "https://x/y"


def test_missing_client_download_returns_none():
    descriptor = VersionDescriptor.from_dict({"id": "x"})
    assert descriptor.client_download() is None
    assert descriptor.logging_config() is None
