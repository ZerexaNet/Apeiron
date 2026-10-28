"""Windows Bedrock adapter (platform-independent parts)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from apeiron.bedrock.windows import (
    BedrockInstallation,
    WindowsBedrockAdapter,
    parse_appx_packages,
)


def test_parse_single_package_object():
    raw = json.dumps(
        {
            "Name": "Microsoft.MinecraftUWP",
            "PackageFamilyName": "Microsoft.MinecraftUWP_8wekyb3d8bbwe",
            "Version": "1.20.1.0",
            "InstallLocation": "C:/Program Files/WindowsApps/x",
        }
    )
    result = parse_appx_packages(raw)
    assert len(result) == 1
    assert result[0].channel == "release"
    assert result[0].activation_uri == "shell:appsFolder\\Microsoft.MinecraftUWP_8wekyb3d8bbwe!App"


def test_parse_multiple_packages_and_channels():
    raw = json.dumps(
        [
            {"Name": "Microsoft.MinecraftUWP", "PackageFamilyName": "Fam1", "Version": "1.0"},
            {
                "Name": "Microsoft.MinecraftWindowsBeta",
                "PackageFamilyName": "Fam2",
                "Version": "2.0",
            },
        ]
    )
    result = parse_appx_packages(raw)
    assert [item.channel for item in result] == ["release", "preview"]
    assert result[1].version == "2.0"


def test_parse_empty_and_garbage():
    assert parse_appx_packages("") == []
    assert parse_appx_packages("   ") == []
    assert parse_appx_packages("not json") == []
    assert parse_appx_packages(json.dumps([{"Name": "x"}])) == []


def test_unsupported_platform_reports_clearly(monkeypatch):
    adapter = WindowsBedrockAdapter(
        runner=lambda args: subprocess.CompletedProcess(list(args), 0, "", "")
    )
    if sys.platform.startswith("win"):
        pytest.skip("this test asserts the non-Windows behaviour")
    from apeiron.core.errors import UnsupportedError

    with pytest.raises(UnsupportedError):
        adapter.launch()
    status = adapter.status()
    assert status["supported"] is False
    assert status["installations"] == []


def test_launch_raises_when_not_installed(monkeypatch):
    responses = {"powershell": subprocess.CompletedProcess(["powershell"], 0, "", "")}

    def runner(args):
        return responses.get(args[0], subprocess.CompletedProcess(list(args), 0, "", ""))

    adapter = WindowsBedrockAdapter(runner=runner)
    monkeypatch.setattr(sys, "platform", "win32")
    from apeiron.core.errors import BedrockError

    with pytest.raises(BedrockError):
        adapter.launch()


def test_launch_uses_shell_activation(monkeypatch):
    calls = []

    def runner(args):
        calls.append(list(args))
        if args[0] == "powershell":
            payload = json.dumps(
                {
                    "Name": "Microsoft.MinecraftUWP",
                    "PackageFamilyName": "Microsoft.MinecraftUWP_8wekyb3d8bbwe",
                    "Version": "1.21.0",
                }
            )
            return subprocess.CompletedProcess(list(args), 0, payload, "")
        return subprocess.CompletedProcess(list(args), 1, "", "")

    adapter = WindowsBedrockAdapter(runner=runner)
    monkeypatch.setattr(sys, "platform", "win32")
    uri = adapter.launch()
    assert uri.startswith("shell:appsFolder\\")
    assert ["explorer.exe", uri] in calls


def test_installation_dict_has_no_secret():
    item = BedrockInstallation(
        package_name="Microsoft.MinecraftUWP",
        family_name="Fam",
        version="1.0",
        install_location="C:/x",
    )
    payload = item.as_dict()
    assert set(payload) == {
        "packageName",
        "familyName",
        "version",
        "installLocation",
        "channel",
        "activationUri",
    }
