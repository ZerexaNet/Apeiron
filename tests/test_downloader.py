"""Download manager behaviour against a real local HTTP server."""

from __future__ import annotations

import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from apeiron.core.errors import (
    DownloadError,
    NetworkError,
)
from apeiron.core.http import HttpClient, HttpConfig
from apeiron.net.downloader import DownloadManager, DownloadSpec

PAYLOAD = b"Apeiron" * 4096  # 28 KiB
PAYLOAD_SHA1 = hashlib.sha1(PAYLOAD).hexdigest()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    failures: ClassVar[dict[str, int]] = {}

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/ok":
            self._send(PAYLOAD)
        elif path == "/big":
            self._send(PAYLOAD * 4)
        elif path == "/other":
            self._send(b"different payload")
        elif path == "/flaky":
            count = self.failures.get(path, 0)
            self.failures[path] = count + 1
            if count == 0:
                self._send(b"server error", status=500)
            else:
                self._send(PAYLOAD)
        elif path == "/slow":
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            for offset in range(0, len(PAYLOAD), 1024):
                if self.server.stop_flag.is_set():  # type: ignore[attr-defined]
                    return
                try:
                    self.wfile.write(PAYLOAD[offset : offset + 1024])
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(0.05)
        elif path == "/hang":
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            time.sleep(3)
        else:
            self._send(b"not found", status=404)

    def _send(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.stop_flag = threading.Event()  # type: ignore[attr-defined]
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.stop_flag.set()  # type: ignore[attr-defined]
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def http():
    return HttpClient(HttpConfig(timeout=10, retries=0, allow_insecure=True))


def _manager(http, **kwargs) -> DownloadManager:
    kwargs.setdefault("concurrency", 3)
    kwargs.setdefault("retries", 1)
    return DownloadManager(http, **kwargs)


def test_download_success_is_atomic(server, http, tmp_path: Path):
    target = tmp_path / "out" / "file.bin"
    spec = DownloadSpec(
        url=f"{server}/ok", destination=target, sha1=PAYLOAD_SHA1, size=len(PAYLOAD)
    )
    results = _manager(http).run([spec])
    assert results[0].path == target
    assert target.read_bytes() == PAYLOAD
    leftovers = list(target.parent.glob("*.part"))
    assert leftovers == []


def test_duplicate_download_is_skipped(server, http, tmp_path: Path):
    target = tmp_path / "file.bin"
    target.write_bytes(PAYLOAD)
    spec = DownloadSpec(
        url=f"{server}/ok", destination=target, sha1=PAYLOAD_SHA1, size=len(PAYLOAD)
    )
    result = _manager(http).run([spec])[0]
    assert result.skipped is True


def test_checksum_mismatch_fails_and_leaves_nothing(server, http, tmp_path: Path):
    target = tmp_path / "bad.bin"
    spec = DownloadSpec(url=f"{server}/ok", destination=target, sha1="0" * 40, size=len(PAYLOAD))
    with pytest.raises(DownloadError):
        _manager(http).run([spec])
    assert not target.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_retry_on_server_error(server, http, tmp_path: Path):
    Handler.failures.pop("/flaky", None)
    target = tmp_path / "flaky.bin"
    spec = DownloadSpec(
        url=f"{server}/flaky", destination=target, sha1=PAYLOAD_SHA1, size=len(PAYLOAD)
    )
    manager = _manager(http, retries=2)
    result = manager.run([spec])[0]
    assert result.path.read_bytes() == PAYLOAD


def test_404_is_reported(server, http, tmp_path: Path):
    spec = DownloadSpec(url=f"{server}/missing", destination=tmp_path / "x.bin")
    with pytest.raises(DownloadError):
        _manager(http).run([spec])
    assert not (tmp_path / "x.bin").exists()


def test_timeout_raises(server, tmp_path: Path):
    slow_http = HttpClient(HttpConfig(timeout=0.3, retries=0, allow_insecure=True))
    spec = DownloadSpec(url=f"{server}/hang", destination=tmp_path / "hang.bin")
    with pytest.raises(DownloadError):
        DownloadManager(slow_http, retries=0).run([spec])


def test_cancel_stops_download(server, http, tmp_path: Path):
    target = tmp_path / "slow.bin"
    manager = _manager(http, retries=0)
    spec = DownloadSpec(url=f"{server}/slow", destination=target)

    def cancel_soon() -> None:
        time.sleep(0.25)
        manager.cancel()

    threading.Thread(target=cancel_soon, daemon=True).start()
    with pytest.raises(DownloadError):
        manager.run([spec])
    assert not target.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_disk_space_is_checked(monkeypatch, server, http, tmp_path: Path):
    class Usage:
        total = 100
        used = 95
        free = 5

    monkeypatch.setattr("shutil.disk_usage", lambda _path: Usage)
    spec = DownloadSpec(url=f"{server}/ok", destination=tmp_path / "big.bin", size=1_000_000)
    with pytest.raises(DownloadError) as excinfo:
        _manager(http).run([spec])
    assert "space" in str(excinfo.value).lower() or "space" in (excinfo.value.detail or "").lower()


def test_unwritable_destination(server, http, tmp_path: Path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    spec = DownloadSpec(url=f"{server}/ok", destination=blocker / "child.bin")
    with pytest.raises(DownloadError):
        _manager(http).run([spec])


def test_concurrent_downloads(server, http, tmp_path: Path):
    specs = [
        DownloadSpec(
            url=f"{server}/ok?i={index}",
            destination=tmp_path / f"f{index}.bin",
            sha1=PAYLOAD_SHA1,
            size=len(PAYLOAD),
        )
        for index in range(10)
    ]
    results = _manager(http, concurrency=4).run(specs)
    assert len(results) == 10
    assert all(path.path.read_bytes() == PAYLOAD for path in results)


def test_progress_is_structured(server, http, tmp_path: Path):
    events = []
    manager = _manager(http, progress_callback=events.append)
    spec = DownloadSpec(url=f"{server}/ok", destination=tmp_path / "p.bin", size=len(PAYLOAD))
    manager.run([spec])
    assert events
    payload = events[-1].as_dict()
    assert payload["completedTasks"] == 1
    assert payload["percent"] <= 100
    assert "bytesDone" in payload


def test_task_repository_transitions(server, http, tmp_path: Path, repos):
    repo = repos["downloads"]
    spec = DownloadSpec(
        url=f"{server}/ok",
        destination=tmp_path / "tracked.bin",
        sha1=PAYLOAD_SHA1,
        size=len(PAYLOAD),
        task_id="task-1",
    )
    _manager(http, repo=repo).run([spec])
    stored = repo.get("task-1")
    assert stored is not None
    assert stored.status == "completed"
    assert stored.bytes_done == len(PAYLOAD)


def test_http_rejects_plain_http_for_remote_hosts():
    client = HttpClient()
    with pytest.raises(NetworkError):
        client.request("http://example.com/file")


def test_http_allows_loopback_over_http(server):
    client = HttpClient(HttpConfig(allow_insecure=True, retries=0))
    response = client.request(f"{server}/ok")
    assert response.status == 200
    assert response.body == PAYLOAD


def test_partial_file_removed_on_network_failure(server, http, tmp_path: Path):
    target = tmp_path / "partial.bin"
    # Content-Length larger than the body we send -> connection closes early.
    spec = DownloadSpec(url=f"{server}/ok", destination=target, size=len(PAYLOAD) + 5000)
    with pytest.raises(DownloadError):
        _manager(http).run([spec])
    assert not target.exists()
    assert list(tmp_path.glob("*.part")) == []
