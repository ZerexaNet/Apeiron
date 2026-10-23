"""Command line entry point.

``apeiron``               start the GUI
``apeiron --self-check``  verify the installed environment (used by packaging
                          tests: native extension, QML, translations, SQLite)
``apeiron --version``     print the version
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _self_check() -> int:
    """Run a real startup without showing a window."""
    # Headless verification only; do not force QT_QUICK_BACKEND=software.
    # The software scene-graph backend cannot instantiate the shader-effect
    # based types used by the Material style, which produces misleading QML
    # errors even though the application is healthy on a real backend.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle

    from apeiron.core import __version__

    report: dict[str, object] = {"version": __version__, "checks": {}, "ok": False}
    checks: dict[str, object] = report["checks"]  # type: ignore[assignment]

    app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])
    app.setApplicationName("Apeiron")
    QQuickStyle.setStyle("Material")

    try:
        from apeiron import _logcore

        checks["nativeLogCore"] = {"loaded": True, "version": _logcore.__version__}
    except Exception as exc:
        checks["nativeLogCore"] = {"loaded": False, "error": str(exc)}
        print(json.dumps(report, indent=2))
        return 1

    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    from apeiron.services.app_context import build_context
    from apeiron.ui.app import create_engine

    # Collect Qt/QML diagnostics emitted while the interface is constructed so
    # that a broken QML binding fails the check instead of scrolling past.
    messages: list[str] = []

    def _handler(mode, context_, message):
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            messages.append(message)

    qInstallMessageHandler(_handler)

    context = build_context()
    checks["database"] = {
        "path": str(context.database.path),
        "schema": context.database.current_version(),
        "integrity": context.database.integrity_check(),
    }

    engine, backend = create_engine(context)
    roots = engine.rootObjects()
    checks["qml"] = {"loaded": bool(roots), "objects": len(roots)}
    if roots:
        window = roots[0]
        checks["qml"]["title"] = window.property("title")
        checks["qml"]["size"] = [window.property("width"), window.property("height")]
    checks["instances"] = len(backend.instances)
    checks["accounts"] = len(backend.accounts)
    checks["languages"] = [c["code"] for c in backend.languageChoices]
    checks["credentialBackend"] = context.accounts.backend().name
    checks["dataRoot"] = str(context.paths.root)
    checks["translations"] = backend.translationState
    checks["qtWarnings"] = messages[:20]
    checks["qtWarningCount"] = len(messages)

    from apeiron.core import logging as native

    native.flush()
    stats = native.stats()
    checks["logStats"] = stats

    report["ok"] = (
        bool(roots)
        and bool(checks["database"]["integrity"])  # type: ignore[index]
        and len(messages) == 0
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    # The engine is intentionally kept alive until process exit: destroying it
    # re-evaluates bindings against a torn-down context property and emits
    # misleading "App is null" warnings after the report has been produced.
    _ = engine
    qInstallMessageHandler(None)
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apeiron", description="Apeiron Minecraft launcher")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument(
        "--self-check", action="store_true", help="verify the installation and exit"
    )
    parser.add_argument("--data-dir", type=Path, help="override the data directory")
    args = parser.parse_args(argv)

    if args.version:
        from apeiron.core import __version__

        print(f"Apeiron {__version__}")
        return 0

    if args.data_dir:
        os.environ["APEIRON_HOME"] = str(args.data_dir)

    if args.self_check:
        return _self_check()

    from apeiron.ui.app import run

    return run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
