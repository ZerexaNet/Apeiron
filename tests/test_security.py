"""Security-sensitive behaviour: archives, paths, updates, URLs."""

from __future__ import annotations

import hashlib
import tarfile
import zipfile
from pathlib import Path

import pytest

from apeiron.core.archive import extract_tar, extract_zip, is_within, zip_directory
from apeiron.core.errors import ArchiveError, NetworkError, PermissionError_, UpdateError
from apeiron.core.http import HttpClient
from apeiron.core.paths import current_platform_key
from apeiron.core.updates import UpdateChecker, parse_version


def _zip_with(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return path


def test_zip_extraction_blocks_traversal(tmp_path: Path):
    archive = _zip_with(tmp_path / "evil.zip", {"../../escaped.txt": b"pwned"})
    with pytest.raises(PermissionError_):
        extract_zip(archive, tmp_path / "out")
    assert not (tmp_path / "escaped.txt").exists()


def test_zip_extraction_blocks_absolute_paths(tmp_path: Path):
    archive = _zip_with(tmp_path / "abs.zip", {"/etc/evil": b"x"})
    with pytest.raises(PermissionError_):
        extract_zip(archive, tmp_path / "out")


def test_zip_extraction_blocks_windows_style_traversal(tmp_path: Path):
    archive = _zip_with(tmp_path / "win.zip", {"..\\..\\evil.txt": b"x"})
    with pytest.raises(PermissionError_):
        extract_zip(archive, tmp_path / "out")


def test_zip_extraction_rejects_total_size_bomb(tmp_path: Path):
    archive = _zip_with(tmp_path / "bomb.zip", {"big.bin": b"0" * 4096})
    with pytest.raises(ArchiveError):
        extract_zip(archive, tmp_path / "out", max_total_bytes=1024)


def test_tar_extraction_blocks_symlink(tmp_path: Path):
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises((PermissionError_, ArchiveError)):
        extract_tar(archive, tmp_path / "out")


def test_zip_directory_roundtrip(tmp_path: Path):
    source = tmp_path / "src"
    (source / "sub").mkdir(parents=True)
    (source / "a.txt").write_text("hello")
    (source / "sub" / "b.txt").write_text("world")
    target = tmp_path / "out.zip"
    zip_directory(source, target, exclude=["sub/b.txt"])
    with zipfile.ZipFile(target) as zf:
        names = set(zf.namelist())
    assert names == {"a.txt"}


def test_is_within(tmp_path: Path):
    assert is_within(tmp_path / "a" / "b", tmp_path)
    assert not is_within(Path("/etc"), tmp_path)


def test_http_rejects_unsupported_scheme():
    with pytest.raises(NetworkError):
        HttpClient.validate_url("ftp://example.com/x")


def test_http_rejects_remote_plain_http():
    with pytest.raises(NetworkError):
        HttpClient.validate_url("http://example.com/x")


def test_http_allows_loopback_when_permitted():
    assert HttpClient.validate_url("http://127.0.0.1:8080/x", allow_insecure=True)


def test_update_version_parsing():
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("2.0.0-beta.1") == (2, 0, 0)
    with pytest.raises(UpdateError):
        parse_version("nightly")


def test_update_rejects_non_allowlisted_host(fake_http):
    checker = UpdateChecker(fake_http, manifest_url="https://evil.example.com/update.json")
    with pytest.raises(UpdateError):
        checker.check()


def test_update_returns_none_when_current(fake_http):
    good = "https://github.com/ZerexaNet/Apeiron/releases/latest/download/update.json"
    fake_http.add_json(
        good,
        {
            "version": "0.0.1",
            "assets": {
                current_platform_key(): {"url": "https://github.com/x/y", "sha256": "a" * 64}
            },
        },
    )
    assert UpdateChecker(fake_http, good).check() is None


def test_update_detects_newer_version(fake_http):
    good = "https://github.com/ZerexaNet/Apeiron/releases/latest/download/update.json"
    fake_http.add_json(
        good,
        {
            "version": "99.0.0",
            "notes": "big release",
            "assets": {
                current_platform_key(): {
                    "url": "https://github.com/x/y",
                    "sha256": "b" * 64,
                    "size": 3,
                }
            },
        },
    )
    info = UpdateChecker(fake_http, good).check()
    assert info is not None
    assert info.version == "99.0.0"


def test_update_download_verifies_checksum(fake_http, tmp_path: Path):
    good = "https://github.com/ZerexaNet/Apeiron/releases/latest/download/update.json"
    payload = b"installer-bytes"
    digest = hashlib.sha256(payload).hexdigest()
    fake_http.add_json(
        good,
        {
            "version": "99.0.0",
            "assets": {current_platform_key(): {"url": "https://github.com/x/y", "sha256": digest}},
        },
    )
    checker = UpdateChecker(fake_http, good)
    info = checker.check()
    fake_http.add("https://github.com/x/y", payload)
    target = checker.download(info, tmp_path / "update.bin")
    assert target.read_bytes() == payload

    fake_http.routes["https://github.com/x/y"].body = b"tampered"
    with pytest.raises(UpdateError):
        checker.download(info, tmp_path / "update2.bin")
    assert not (tmp_path / "update2.bin").exists()


def test_no_plaintext_secrets_in_native_log(native_log):
    """End-to-end: a token written through the facade never reaches disk."""
    from apeiron.core import logging as native
    from apeiron.core.logging import get_logger

    logger = get_logger("auth")
    logger.info("authorization: Bearer %s", "abcdef1234567890abcdef")
    logger.info("refresh_token=%s", "REFRESH-SECRET-VALUE")
    native.flush()
    text = native_log.read_text(encoding="utf-8")
    assert "abcdef1234567890abcdef" not in text
    assert "REFRESH-SECRET-VALUE" not in text
