"""Tests for the C11 logging core exposed as ``apeiron._logcore``."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest


@pytest.fixture()
def core():
    from apeiron import _logcore

    return _logcore


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_extension_imports_and_exposes_levels(core):
    assert core.DEBUG == 10
    assert core.INFO == 20
    assert core.WARNING == 30
    assert core.ERROR == 40
    assert core.CRITICAL == 50
    assert isinstance(core.__version__, str)


def test_write_flush_and_read(core, tmp_path: Path):
    log_file = tmp_path / "a.log"
    core.init(str(log_file), core.DEBUG, 1 << 20, 2, False)
    try:
        core.write(core.INFO, "test", "hello native", None)
        core.write(core.ERROR, "test", "something failed", "Traceback: boom")
        core.flush()
        text = _read(log_file)
        assert "hello native" in text
        assert "INFO" in text and "ERROR" in text
        assert "Traceback: boom" in text
        # pid and tid are part of every line
        assert f"[{os.getpid()}:" in text
    finally:
        core.shutdown()


def test_level_filtering(core, tmp_path: Path):
    log_file = tmp_path / "b.log"
    core.init(str(log_file), core.WARNING, 1 << 20, 2, False)
    try:
        core.write(core.DEBUG, "t", "debug-hidden", None)
        core.write(core.INFO, "t", "info-hidden", None)
        core.write(core.ERROR, "t", "error-visible", None)
        core.flush()
        text = _read(log_file)
        assert "debug-hidden" not in text
        assert "info-hidden" not in text
        assert "error-visible" in text
        stats = core.stats()
        assert stats["filtered"] >= 2
    finally:
        core.shutdown()


def test_unicode_roundtrip(core, tmp_path: Path):
    log_file = tmp_path / "c.log"
    core.init(str(log_file), core.DEBUG, 1 << 20, 2, False)
    payload = "中文日志 — Ω ✓ 日本語"
    try:
        core.write(core.INFO, "i18n", payload, None)
        core.flush()
        assert payload in _read(log_file)
    finally:
        core.shutdown()


def test_rotation_and_retention(core, tmp_path: Path):
    log_file = tmp_path / "d.log"
    core.init(str(log_file), core.DEBUG, 2048, 2, False)
    try:
        for index in range(200):
            core.write(core.INFO, "rot", f"record {index:04d} " + "x" * 80, None)
        core.flush()
        stats = core.stats()
        assert stats["rotations"] >= 1
        assert log_file.exists()
        assert (tmp_path / "d.log.1").exists()
    finally:
        core.shutdown()


def test_threads_are_safe(core, tmp_path: Path):
    log_file = tmp_path / "e.log"
    core.init(str(log_file), core.DEBUG, 0, 0, False, 16384)
    errors: list[Exception] = []

    def worker(worker_id: int) -> None:
        try:
            for index in range(150):
                core.write(core.INFO, f"w{worker_id}", f"line {worker_id}-{index}", None)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    try:
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        core.flush()
        assert not errors
        stats = core.stats()
        assert stats["written"] == 900
        assert stats["dropped"] == 0
        text = _read(log_file)
        assert "w5" in text and "line 5-149" in text
    finally:
        core.shutdown()


def test_redaction_hides_credentials(core):
    masked, count = core.redact("Authorization: Bearer abcdef1234567890")
    assert "abcdef1234567890" not in masked
    assert count >= 1

    masked, _ = core.redact('{"access_token":"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"}')
    assert "eyJhbGciOiJIUzI1NiJ9" not in masked

    masked, count = core.redact("nothing sensitive here")
    assert masked == "nothing sensitive here"
    assert count == 0


def test_redaction_applies_before_writing(core, tmp_path: Path):
    log_file = tmp_path / "f.log"
    core.init(str(log_file), core.DEBUG, 1 << 20, 1, False)
    try:
        core.write(core.INFO, "auth", "refresh_token=SUPERSECRET", None)
        core.flush()
        assert "SUPERSECRET" not in _read(log_file)
    finally:
        core.shutdown()


def test_invalid_utf8_is_rejected(core, tmp_path: Path):
    log_file = tmp_path / "g.log"
    core.init(str(log_file), core.DEBUG, 1 << 20, 1, False)
    try:
        with pytest.raises(UnicodeError):
            core.write(core.INFO, "bad", "\udcff", None)
    finally:
        core.shutdown()


def test_state_errors(core, tmp_path: Path):
    core.shutdown()
    with pytest.raises(RuntimeError):
        core.write(core.INFO, "x", "y", None)
    with pytest.raises(RuntimeError):
        core.flush()
    # unsafe path surfaces as OSError
    with pytest.raises(OSError):
        core.init("/nonexistent-directory-for-apeiron/x.log", core.INFO, 0, 0, False)


def test_reinitialise_closes_previous_file(core, tmp_path: Path):
    first = tmp_path / "h1.log"
    second = tmp_path / "h2.log"
    core.init(str(first), core.DEBUG, 1 << 20, 1, False)
    core.write(core.INFO, "t", "first", None)
    core.close() if hasattr(core, "close") else core.shutdown()
    core.init(str(second), core.DEBUG, 1 << 20, 1, False)
    try:
        core.write(core.INFO, "t", "second", None)
        core.flush()
        assert "first" in _read(first)
        assert "second" in _read(second)
    finally:
        core.shutdown()


def test_large_message_is_truncated_not_dropped(core, tmp_path: Path):
    log_file = tmp_path / "i.log"
    core.init(str(log_file), core.DEBUG, 1 << 20, 1, False)
    try:
        core.write(core.INFO, "big", "z" * 100_000, None)
        core.flush()
        text = _read(log_file)
        assert len(text) > 1000  # truncated but present
        assert core.stats()["written"] >= 1
    finally:
        core.shutdown()


def test_flush_is_durable(core, tmp_path: Path):
    log_file = tmp_path / "j.log"
    core.init(str(log_file), core.DEBUG, 1 << 20, 1, False)
    try:
        for index in range(50):
            core.write(core.INFO, "f", f"durable-{index}", None)
        core.flush()
        assert "durable-49" in _read(log_file)
    finally:
        core.shutdown()


def test_python_logger_facade(native_log):
    from apeiron.core.logging import get_logger

    logger = get_logger("unit")
    logger.info("facade %s", "works")
    logger.warning("with value %d", 42)
    try:
        raise ValueError("expected")
    except ValueError:
        logger.exception("caught it")
    from apeiron.core import logging as native

    native.flush()
    text = native_log.read_text(encoding="utf-8")
    assert "facade works" in text
    assert "with value 42" in text
    assert "ValueError: expected" in text
