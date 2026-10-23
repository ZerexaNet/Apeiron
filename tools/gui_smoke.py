#!/usr/bin/env python3
"""Headless end-to-end GUI smoke test.

Runs the real QML interface on the ``offscreen`` platform, drives it through the
same Python backend the UI uses, asserts observable state and prints a JSON
report.  It lives outside pytest because Qt's teardown ordering inside a test
session is not deterministic; as a standalone process it is reproducible.

Exit code 0 means every check passed.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

RESULT: dict[str, object] = {"checks": [], "ok": False}
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    entry = {"name": name, "passed": bool(condition)}
    if detail:
        entry["detail"] = detail
    RESULT["checks"].append(entry)  # type: ignore[union-attr]
    if not condition:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL {name} {detail}", file=sys.stderr)


def wait_until(app, predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return predicate()


def texts_of(root) -> list[str]:
    """Collect every rendered ``text`` through the visual item tree.

    ``QObject.findChildren`` does not reach Repeater delegates here (their
    QObject parent is the repeater, not the enclosing item), so the tree is
    walked explicitly through ``childItems()``.
    """
    values: list[str] = []

    def walk(item) -> None:
        text = item.property("text")
        if isinstance(text, str):
            values.append(text)
        for child in item.childItems():
            walk(child)

    start = root.contentItem() if hasattr(root, "contentItem") else root
    if start is not None:
        walk(start)
    return values


def main(data_dir: str) -> int:
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickItem
    from PySide6.QtQuickControls2 import QQuickStyle

    warnings: list[str] = []

    # A machine without an installed Chinese locale makes Qt warn about a
    # non-UTF-8 C locale. That is a property of the host, not of the interface,
    # so it is excluded from the "no Qt warnings" gate; everything else still
    # fails the run.
    benign = ("Detected locale",)

    def collect(mode, context, message):
        noisy = mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg)
        if noisy and not any(pattern in message for pattern in benign):
            warnings.append(message)

    qInstallMessageHandler(collect)

    app = QGuiApplication([sys.argv[0]])
    QQuickStyle.setStyle("Material")

    from apeiron.services.app_context import build_context
    from apeiron.ui.app import create_engine

    context = build_context()
    engine, backend = create_engine(context)
    roots = engine.rootObjects()
    check("window.loaded", len(roots) == 1, f"roots={len(roots)}")
    if not roots:
        print(json.dumps(RESULT, ensure_ascii=False))
        return 1
    window = roots[0]
    wait_until(app, lambda: False, timeout=0.5)

    check("window.title", window.property("title") == "Apeiron", str(window.property("title")))
    check("window.visible", window.property("visible") is True)
    check("window.size", window.property("width") >= 1024, str(window.property("width")))

    classes = {child.metaObject().className() for child in window.findChildren(QQuickItem)}
    for page in (
        "HomePage",
        "LibraryPage",
        "AccountsPage",
        "DownloadsPage",
        "ContentPage",
        "SettingsPage",
        "LogsPage",
        "AboutPage",
    ):
        check(f"page.{page}", any(page in name for name in classes))

    # Language: default is Simplified Chinese.
    chinese = texts_of(window)
    check("i18n.default_zh", "首页" in chinese, f"{len(chinese)} texts")
    check("i18n.translated", backend.translationState.get("translated") is True)
    check("i18n.effective", backend.effectiveLanguage == "zh_CN", backend.effectiveLanguage)

    # Changing the language rebuilds the component tree, so the window object
    # is replaced: always take a fresh reference from the engine.
    backend.setLanguage("en_US")

    def english_window():
        roots_now = engine.rootObjects()
        return roots_now[0] if roots_now else None

    wait_until(app, lambda: english_window() is not None and "Home" in texts_of(english_window()))
    window = english_window()
    english = texts_of(window) if window is not None else []
    check("i18n.switch_en", "Home" in english and "设置" not in english, f"{len(english)} texts")

    backend.setLanguage("zh_CN")

    def chinese_window():
        roots_now = engine.rootObjects()
        return roots_now[0] if roots_now else None

    wait_until(app, lambda: chinese_window() is not None and "首页" in texts_of(chinese_window()))
    window = chinese_window()
    check("i18n.switch_back_zh", window is not None and "首页" in texts_of(window))

    # Accounts: create an offline profile through the same path the UI uses.
    backend.createOfflineAccount("SmokeTester")
    ok = wait_until(app, lambda: len(backend.accounts) == 1)
    check("account.create_offline", ok, str(len(backend.accounts)))
    if ok:
        account = backend.accounts[0]
        check("account.unverified", account["verified"] is False)
        check("account.name", account["name"] == "SmokeTester")

    # Instances: create, select, inspect, delete.
    backend.createInstance("SmokeInstance", "1.20.1", "vanilla", "", "")
    ok = wait_until(app, lambda: len(backend.instances) == 1)
    check("instance.create", ok, str(len(backend.instances)))
    if ok:
        instance = backend.instances[0]
        backend.selectInstance(instance["id"])
        check("instance.selected", backend.selectedInstanceId == instance["id"])
        check("instance.summary", instance["loaderLabel"] == "Vanilla", str(instance))
        directory = Path(context.paths.instance_dir(instance["id"]))
        check("instance.directory", directory.is_dir(), str(directory))
        check("instance.mods_dir", (directory / "mods").is_dir())
        backend.deleteInstance(instance["id"])
        check("instance.delete", wait_until(app, lambda: len(backend.instances) == 0))

    # Theme persistence.
    backend.setTheme("dark")
    check("theme.dark", backend.theme == "dark")
    backend.setTheme("system")
    check("theme.system", backend.theme == "system")

    # About/legal payload.
    about = backend.about
    check(
        "about.disclaimer",
        "not an official Minecraft product" in str(about.get("disclaimer", "")),
    )
    check("about.contact", about.get("contact") == "admin@zerexa.net")
    check("about.support_rows", len(about.get("support") or []) == 3)

    # Navigation.
    window.setProperty("currentPage", 3)
    app.processEvents()
    check("nav.page_switch", window.property("currentPage") == 3)

    check("qt.no_warnings", not warnings, "; ".join(warnings[:3]))

    RESULT["ok"] = not FAILURES
    RESULT["dataRoot"] = str(context.paths.root)
    RESULT["uiTextCount"] = len(chinese)
    print(json.dumps(RESULT, ensure_ascii=False, indent=2))

    from apeiron.core import logging as native

    native.flush()
    backend.shutdown(timeout_ms=3000)
    sys.stdout.flush()
    sys.stderr.flush()
    # Skip interpreter/Qt teardown: destroying the engine re-evaluates QML
    # bindings against a removed context property and only produces noise.
    os._exit(0 if RESULT["ok"] else 1)


if __name__ == "__main__":
    directory = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="apeiron-gui-")
    os.environ["APEIRON_HOME"] = directory
    raise SystemExit(main(directory))
