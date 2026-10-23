"""Python facade over the native (C11) logging core.

All Apeiron logging goes through ``apeiron._logcore``: a bounded queue on the
calling thread, and a dedicated writer thread that performs rotation and I/O.
Nothing here re-implements logging in Python — this module only adapts the
native API to a convenient, typed interface and forwards ``logging`` module
records (including Qt's) into the same sink.
"""

from __future__ import annotations

import logging as _stdlib
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Final

from apeiron.core.errors import ApeironError

try:  # the extension is built by ``pip install -e .`` / the packaging script
    from apeiron import _logcore  # type: ignore[attr-defined]
except ImportError as exc:  # pragma: no cover - build error path
    raise ImportError(
        "The native logging extension 'apeiron._logcore' is not built. "
        "Run 'python setup.py build_ext --inplace' (or reinstall the package)."
    ) from exc

DEBUG: Final = _logcore.DEBUG
INFO: Final = _logcore.INFO
WARNING: Final = _logcore.WARNING
ERROR: Final = _logcore.ERROR
CRITICAL: Final = _logcore.CRITICAL

LEVEL_NAMES: Final[dict[int, str]] = {
    DEBUG: "DEBUG",
    INFO: "INFO",
    WARNING: "WARNING",
    ERROR: "ERROR",
    CRITICAL: "CRITICAL",
}

_configured = threading.Event()
_config_lock = threading.Lock()


def configure(
    log_file: Path | str | None,
    *,
    level: int = INFO,
    max_bytes: int = 8 * 1024 * 1024,
    backup_count: int = 5,
    console: bool = False,
    queue_capacity: int = 0,
) -> None:
    """Initialise the native sink. Safe to call repeatedly (reconfiguration)."""
    with _config_lock:
        _logcore.init(
            str(log_file) if log_file is not None else None,
            level,
            max_bytes,
            backup_count,
            bool(console),
            queue_capacity,
        )
        _configured.set()


def is_configured() -> bool:
    return _configured.is_set()


def flush() -> None:
    if _configured.is_set():
        _logcore.flush()


def shutdown() -> None:
    with _config_lock:
        if _configured.is_set():
            _logcore.shutdown()
            _configured.clear()


def set_level(level: int) -> None:
    _logcore.set_level(level)


def get_level() -> int:
    return _logcore.get_level()


def stats() -> dict[str, Any]:
    return _logcore.stats()


def redact(text: str) -> tuple[str, int]:
    """Mask credentials in ``text``; returns (masked, span_count)."""
    return _logcore.redact(text)


def _format(message: str, args: tuple[Any, ...]) -> str:
    if not args:
        return message
    try:
        return message % args
    except (TypeError, ValueError):
        return message + " " + " ".join(str(a) for a in args)


class Logger:
    """A named logger writing to the native core."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        # Module identifiers are part of the log line; keep them short and
        # free of user data.
        self.name = name[:64]

    def _log(self, level: int, message: str, args: tuple[Any, ...], exc: str | None) -> None:
        if not _configured.is_set():
            # Before configuration (very early startup) fall back to stderr so
            # the message is not lost, but never create a second file sink.
            if level >= WARNING:
                print(f"{self.name}: {_format(message, args)}", file=sys.stderr)
            return
        try:
            _logcore.write(level, self.name, _format(message, args), exc)
        except (OSError, RuntimeError, UnicodeError):
            # Logging must never take the application down.
            pass

    def debug(self, message: str, *args: Any) -> None:
        self._log(DEBUG, message, args, None)

    def info(self, message: str, *args: Any) -> None:
        self._log(INFO, message, args, None)

    def warning(self, message: str, *args: Any) -> None:
        self._log(WARNING, message, args, None)

    def error(self, message: str, *args: Any) -> None:
        self._log(ERROR, message, args, None)

    def critical(self, message: str, *args: Any) -> None:
        self._log(CRITICAL, message, args, None)

    def exception(self, message: str, *args: Any) -> None:
        """Log at ERROR with the active exception's traceback."""
        exc_text = traceback.format_exc()
        self._log(ERROR, message, args, exc_text if exc_text.strip() else None)

    def error_from(self, error: BaseException | ApeironError, context: str = "") -> None:
        """Log a structured error without leaking credentials."""
        if isinstance(error, ApeironError):
            detail = f"{context}: {error.code}: {error.message}"
            if error.detail:
                detail += f" | {error.detail}"
            self._log(ERROR, detail, (), None)
        else:
            exc_text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            self._log(ERROR, context or type(error).__name__, (), exc_text)


_loggers: dict[str, Logger] = {}


def get_logger(name: str) -> Logger:
    logger = _loggers.get(name)
    if logger is None:
        logger = Logger(name)
        _loggers[name] = logger
    return logger


class NativeLogHandler(_stdlib.Handler):
    """Forwards stdlib (and therefore Qt) log records into the native sink."""

    def emit(self, record: _stdlib.LogRecord) -> None:
        try:
            exc = None
            if record.exc_info:
                exc = "".join(traceback.format_exception(*record.exc_info))
            text = record.getMessage()
            if _configured.is_set():
                _logcore.write(record.levelno, record.name[:64], text, exc)
            elif record.levelno >= _stdlib.WARNING:
                print(f"{record.name}: {text}", file=sys.stderr)
        except Exception:
            self.handleError(record)


def install_stdlib_bridge(level: int = WARNING) -> None:
    """Route Python warnings/errors (PySide6 included) to the native sink."""
    root = _stdlib.getLogger()
    if any(isinstance(h, NativeLogHandler) for h in root.handlers):
        return
    handler = NativeLogHandler()
    handler.setLevel(level)
    root.addHandler(handler)
    if root.level > level or root.level == _stdlib.NOTSET:
        root.setLevel(level)
