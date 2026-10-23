"""Language detection, fallback rules and translation loading."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from apeiron.i18n.languages import (
    DEFAULT_LANGUAGE,
    available_choices,
    is_manual,
    normalise_locale,
    resolve_language,
    system_language,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("zh_CN.UTF-8", "zh_CN"),
        ("zh-Hans-CN", "zh_CN"),
        ("zh_TW", "zh_CN"),
        ("en_US", "en_US"),
        ("en-GB", "en_US"),
        ("C", ""),
        ("fr_FR", ""),
        ("", ""),
    ],
)
def test_normalise_locale(raw, expected):
    assert normalise_locale(raw) == expected


def test_chinese_system_resolves_to_simplified():
    assert system_language("zh_CN.UTF-8") == "zh_CN"


def test_english_system_resolves_to_english():
    assert system_language("en_US.UTF-8") == "en_US"


def test_unsupported_system_falls_back_to_chinese():
    assert system_language("de_DE.UTF-8") == DEFAULT_LANGUAGE
    assert system_language("ja_JP") == DEFAULT_LANGUAGE


def test_manual_choice_overrides_system():
    assert resolve_language("en_US", "zh_CN.UTF-8") == "en_US"
    assert resolve_language("zh_CN", "en_US.UTF-8") == "zh_CN"
    assert is_manual("en_US")
    assert not is_manual("system")


def test_system_setting_follows_locale():
    assert resolve_language("system", "en_US") == "en_US"
    assert resolve_language("system", "zh_CN") == "zh_CN"


def test_available_choices_include_system_first():
    choices = available_choices()
    assert choices[0]["code"] == "system"
    assert {c["code"] for c in choices} >= {"zh_CN", "en_US"}


def test_translation_manager_reports_missing_file(tmp_path: Path):
    from PySide6.QtCore import QCoreApplication

    from apeiron.i18n.translator import TranslationManager

    app = QCoreApplication.instance() or QCoreApplication([])
    manager = TranslationManager(tmp_path)
    state = manager.apply("en_US")
    assert state.translated is False
    assert state.missing is True
    assert state.language == "en_US"
    assert app is not None


def test_translation_manager_loads_compiled_file(tmp_path: Path):
    """Compile a tiny .ts with lrelease when available, then load it."""
    import shutil
    import subprocess

    from PySide6.QtCore import QCoreApplication

    from apeiron.i18n.translator import TranslationManager

    candidate = Path(sys.executable).parent / "pyside6-lrelease"
    lrelease = shutil.which("pyside6-lrelease") or shutil.which("lrelease")
    if not lrelease and candidate.exists():
        lrelease = str(candidate)
    if not lrelease:
        pytest.skip("Qt Linguist compiler is not available")

    ts = tmp_path / "apeiron_zh_CN.ts"
    ts.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="zh_CN">
<context>
    <name>Main</name>
    <message>
        <source>Hello</source>
        <translation>你好</translation>
    </message>
</context>
</TS>
""",
        encoding="utf-8",
    )
    result = subprocess.run([lrelease, str(ts)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        pytest.skip(f"lrelease failed: {result.stderr[:200]}")

    QCoreApplication.instance() or QCoreApplication([])
    manager = TranslationManager(tmp_path)
    state = manager.apply("zh_CN")
    assert state.translated is True
    assert manager.available()
