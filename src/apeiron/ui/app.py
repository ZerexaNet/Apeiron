"""Qt application bootstrap: fonts, style, QML engine and translations."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine
from PySide6.QtQuickControls2 import QQuickStyle

from apeiron.core import __version__
from apeiron.core.logging import get_logger
from apeiron.i18n.translator import TranslationManager
from apeiron.services.app_context import AppContext, build_context
from apeiron.ui.backend import Backend

log = get_logger("app.ui")

QML_DIR = Path(__file__).parent / "qml"

# Engines and backends are kept alive for the whole process.  A QObject created
# in Python and exposed to QML is owned by Python; if the last Python reference
# disappears the C++ object is destroyed and every QML binding that reads it
# starts failing with "Cannot read property ... of null".
_LIVE: list[tuple[object, object]] = []
FONT_CANDIDATES = (
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Hiragino Sans GB",
    "WenQuanYi Micro Hei",
    "Segoe UI",
    "Helvetica Neue",
    "Inter",
    "DejaVu Sans",
    "Arial",
)


def _configure_font(app: QGuiApplication) -> None:
    """Prefer a font that can render Simplified Chinese, if one exists.

    Requesting families that are not installed makes Qt build alias tables (and
    warn about the time it takes), so the candidate list is filtered against the
    installed families and the platform default is kept when nothing matches.
    """
    from PySide6.QtGui import QFontDatabase

    installed = set(QFontDatabase.families())
    available = [name for name in FONT_CANDIDATES if name in installed]
    if not available:
        log.info("no preferred UI font installed; keeping the platform default")
        return
    font = QFont()
    font.setFamilies(available)
    font.setPointSize(10)
    app.setFont(font)


def create_engine(
    context: AppContext, *, qml_dir: Path | None = None
) -> tuple[QQmlApplicationEngine, Backend]:
    """Build the engine and backend; shared by the app and the GUI tests."""
    engine = QQmlApplicationEngine()
    # Components are a single URI module ("Apeiron.Components") so that the
    # engine keeps exactly one type registry for them; importing the same
    # directory through different relative paths creates duplicate registries
    # and makes composite types fail to assign as children.
    engine.addImportPath(str(qml_dir or QML_DIR))
    backend = Backend(context)
    manager = TranslationManager(context.paths.root / "i18n")
    # Prefer bundled translations (inside the package) over user data.
    bundled = Path(__file__).parent.parent / "i18n"
    if bundled.exists():
        manager.i18n_dir = bundled
    backend.set_translation_manager(manager)
    state = manager.apply(context.settings.language)
    backend.set_translation_manager(manager)

    # The backend is exposed as a context property with explicit C++ ownership
    # plus a strong Python reference.  Registering it as a QML singleton
    # (qmlRegisterSingletonInstance) is the more idiomatic option, but in the
    # PySide6 build used here that registration corrupts the composite-type
    # registry and makes sibling composite components fail to instantiate, so
    # the context property is the verified-working route.
    QQmlEngine.setObjectOwnership(backend, QQmlEngine.CppOwnership)
    backend.setParent(engine)  # C++ parent keeps the object alive independent of GC
    _LIVE.append((engine, backend))
    engine.rootContext().setContextProperty("App", backend)
    engine.rootContext().setContextProperty("ApeironVersion", __version__)
    engine.rootContext().setContextProperty("TranslationAvailable", state.translated)

    source = (qml_dir or QML_DIR) / "Main.qml"
    url = QUrl.fromLocalFile(str(source))
    backend.attach_engine(engine, url)
    engine.load(url)
    return engine, backend


def run(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    QCoreApplication.setAttribute("Qt.AA_UseHighDpiPixmaps", True) if hasattr(
        QCoreApplication, "AA_UseHighDpiPixmaps"
    ) else None
    app = QGuiApplication(argv)
    app.setApplicationName("Apeiron")
    app.setApplicationDisplayName("Apeiron")
    app.setOrganizationName("Zerexa Ltd")
    app.setOrganizationDomain("zerexa.net")
    app.setApplicationVersion(__version__)

    QQuickStyle.setStyle("Material")
    _configure_font(app)

    console_logging = os.environ.get("APEIRON_CONSOLE_LOG", "") == "1"
    context = build_context(console_logging=console_logging)
    engine, backend = create_engine(context)

    if not engine.rootObjects():
        log.critical("the QML interface failed to load")
        return 2

    app.aboutToQuit.connect(lambda: _shutdown(context, backend))
    exit_code = app.exec()
    return exit_code


def _shutdown(context: AppContext, backend: Backend | None = None) -> None:
    from apeiron.core import logging as native

    if backend is not None:
        # Background tasks hold references to Qt objects; let them finish
        # before the engine and its objects are destroyed.
        backend.shutdown()
    try:
        context.launcher.stop()
    except Exception:
        pass
    try:
        native.flush()
        native.shutdown()
    except Exception:
        pass
    try:
        context.database.close()
    except Exception:
        pass
