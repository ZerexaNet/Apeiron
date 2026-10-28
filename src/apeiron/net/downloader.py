"""Unified download manager.

Guarantees:

* bounded concurrency (``max_concurrent_downloads`` from settings);
* a hard timeout per attempt and exponential backoff between attempts;
* cancellation that is observed within one chunk (~256 KiB);
* writes to a sibling ``*.part`` file, SHA-1 verification, ``fsync`` and an
  atomic ``os.replace`` so a target file is never half-written;
* pre-flight disk-space and write-permission checks with actionable errors;
* structured progress (dataclasses, not log scraping);
* duplicate suppression: two requests for the same destination run once.

Every failure carries a :class:`DownloadFailure` with the URL, attempt count
and a human readable reason, which the diagnostics page renders verbatim.
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from apeiron.core.errors import (
    CancelledError,
    DiskSpaceError,
    DownloadError,
    IntegrityError,
    NetworkError,
    PermissionError_,
)
from apeiron.core.http import HttpClient
from apeiron.core.logging import get_logger
from apeiron.db.models import DownloadTask, utc_now
from apeiron.db.repositories import DownloadRepository

log = get_logger("download")

CHUNK_SIZE = 262144
DISK_HEADROOM = 64 * 1024 * 1024  # never fill the volume completely


@dataclass(frozen=True)
class DownloadSpec:
    """One file to fetch."""

    url: str
    destination: Path
    sha1: str = ""
    size: int = 0
    category: str = "other"
    task_id: str = ""
    sha512: str = ""
    fallback_urls: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return str(self.destination)


@dataclass
class DownloadResult:
    spec: DownloadSpec
    path: Path
    bytes_written: int = 0
    skipped: bool = False
    seconds: float = 0.0


@dataclass
class DownloadFailure:
    spec: DownloadSpec
    reason: str
    attempts: int = 0
    error_code: str = "apeiron.download"


@dataclass
class Progress:
    """Aggregate, structured progress for the UI."""

    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    current_file: str = ""
    speed_bps: float = 0.0
    cancelled: bool = False

    @property
    def percent(self) -> float:
        if self.bytes_total <= 0:
            return 100.0 if self.total_tasks and self.finished else 0.0
        return min(100.0, self.bytes_done * 100.0 / self.bytes_total)

    @property
    def finished(self) -> bool:
        return self.completed_tasks + self.failed_tasks >= self.total_tasks and self.total_tasks > 0

    def as_dict(self) -> dict[str, object]:
        return {
            "totalTasks": self.total_tasks,
            "completedTasks": self.completed_tasks,
            "failedTasks": self.failed_tasks,
            "bytesDone": self.bytes_done,
            "bytesTotal": self.bytes_total,
            "percent": round(self.percent, 1),
            "currentFile": self.current_file,
            "speedBps": round(self.speed_bps, 1),
            "cancelled": self.cancelled,
        }


ProgressCallback = Callable[[Progress], None]


class DownloadManager:
    """Synchronous batched downloader with a thread pool."""

    def __init__(
        self,
        http: HttpClient,
        *,
        concurrency: int = 4,
        retries: int = 3,
        repo: DownloadRepository | None = None,
        progress_callback: ProgressCallback | None = None,
        chunk_size: int = CHUNK_SIZE,
    ) -> None:
        self.http = http
        self.concurrency = max(1, min(16, concurrency))
        self.retries = max(0, retries)
        self.repo = repo
        self.progress_callback = progress_callback
        self.chunk_size = chunk_size
        self._batch_lock = threading.Lock()
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._progress = Progress()
        self._started_at = 0.0
        self._current_files: set[str] = set()

    # -- public API ---------------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            self._progress.cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def reset_cancel(self) -> None:
        self._cancel.clear()
        with self._lock:
            self._progress.cancelled = False

    def run(self, specs: Sequence[DownloadSpec]) -> list[DownloadResult]:
        # Serialize batches: cancellation and progress belong to the active batch.
        with self._batch_lock:
            return self._run_batch(specs)

    def _run_batch(self, specs: Sequence[DownloadSpec]) -> list[DownloadResult]:
        """Download every spec, returning successes; failures are raised.

        The first failure cancels the remaining work and raises, so callers
        (installers) do not build a half-installed version silently.
        """
        unique: dict[str, DownloadSpec] = {}
        for spec in specs:
            unique.setdefault(spec.key, spec)
        ordered = list(unique.values())

        self.reset_cancel()
        self._started_at = time.monotonic()
        with self._lock:
            self._progress = Progress(
                total_tasks=len(ordered),
                bytes_total=sum(s.size for s in ordered),
            )
        self._emit()

        if not ordered:
            return []

        results: list[DownloadResult] = []
        failures: list[DownloadFailure] = []
        executor = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="apeiron-dl")
        futures: dict[Future[DownloadResult], DownloadSpec] = {}
        try:
            for spec in ordered:
                futures[executor.submit(self._download_one, spec)] = spec
            for future in futures:
                spec = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except CancelledError as exc:
                    failures.append(
                        DownloadFailure(spec, str(exc) or "cancelled", error_code=exc.code)
                    )
                    with self._lock:
                        self._progress.failed_tasks += 1
                    self.cancel()
                except (
                    DownloadError,
                    NetworkError,
                    IntegrityError,
                    PermissionError_,
                    DiskSpaceError,
                ) as exc:
                    failures.append(
                        DownloadFailure(
                            spec,
                            exc.message,
                            attempts=self._attempts_for(spec),
                            error_code=exc.code,
                        )
                    )
                    self.cancel()
                except Exception as exc:
                    log.exception("unexpected download failure for %s", spec.destination.name)
                    failures.append(DownloadFailure(spec, str(exc), error_code="apeiron.download"))
                    self.cancel()
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            with self._lock:
                self._progress.failed_tasks = len(failures)
            self._emit()

        if failures:
            first = failures[0]
            raise DownloadError(
                f"{len(failures)} download(s) failed; first: {first.reason}",
                detail=f"{first.spec.url} -> {first.spec.destination}",
            )
        return results

    def download(self, spec: DownloadSpec) -> DownloadResult:
        return self.run([spec])[0]

    # -- internals ----------------------------------------------------------
    def _attempts_for(self, spec: DownloadSpec) -> int:
        task = self.repo.get(spec.task_id) if (self.repo and spec.task_id) else None
        return task.attempts if task else 0

    def _download_one(self, spec: DownloadSpec) -> DownloadResult:
        started = time.monotonic()
        if self._cancel.is_set():
            raise CancelledError("download cancelled")

        destination = spec.destination
        self._validate_destination(destination, spec.size)
        if self._is_complete(destination, spec):
            log.debug("skip (already present): %s", destination.name)
            with self._lock:
                self._progress.completed_tasks += 1
                self._progress.bytes_done += spec.size
            self._emit()
            return DownloadResult(spec=spec, path=destination, skipped=True, seconds=0.0)

        task = self._task_start(spec)
        part = destination.with_name(destination.name + ".part")
        last_error: Exception | None = None

        for attempt in range(1, self.retries + 2):
            try:
                urls: list[str] = []
                for original in (spec.url, *spec.fallback_urls):
                    candidates = (
                        self.http.candidates(original)
                        if hasattr(self.http, "candidates")
                        else (original,)
                    )
                    urls.extend(u for u in candidates if u not in urls)
                for position, url in enumerate(urls):
                    attempt_spec = replace(spec, url=url)
                    try:
                        written = self._stream_to_part(attempt_spec, part)
                        self._verify(part, spec)
                        break
                    except (NetworkError, IntegrityError, OSError):
                        self._rollback_bytes(part)
                        part.unlink(missing_ok=True)
                        if position == len(urls) - 1:
                            raise
                self._commit(part, destination)
                elapsed = time.monotonic() - started
                with self._lock:
                    self._progress.completed_tasks += 1
                self._emit()
                self._task_finish(task, "completed", written)
                log.info("downloaded %s (%d bytes, %.2fs)", destination.name, written, elapsed)
                return DownloadResult(
                    spec=spec, path=destination, bytes_written=written, seconds=elapsed
                )
            except CancelledError:
                self._rollback_bytes(part)
                part.unlink(missing_ok=True)
                self._task_finish(task, "cancelled", 0)
                raise
            except IntegrityError as exc:
                # A corrupt payload will not become valid by retrying the same
                # URL more than once; retry a single time then fail loudly.
                part.unlink(missing_ok=True)
                last_error = exc
                log.warning("integrity check failed for %s: %s", destination.name, exc.message)
                if attempt >= 2:
                    self._task_finish(task, "failed", 0, exc.message)
                    raise
            except (NetworkError, PermissionError_, DiskSpaceError, OSError) as exc:
                part.unlink(missing_ok=True)
                if isinstance(exc, (PermissionError_, DiskSpaceError)):
                    self._task_finish(task, "failed", 0, str(exc))
                    raise
                last_error = exc
                if attempt > self.retries:
                    message = getattr(exc, "message", str(exc))
                    self._task_finish(task, "failed", 0, message)
                    raise DownloadError(
                        f"failed to download {destination.name} after {attempt} attempts",
                        detail=message,
                    ) from exc
                delay = min(20.0, 0.5 * 2 ** (attempt - 1))
                log.warning(
                    "download attempt %d/%d failed for %s: %s (retrying in %.1fs)",
                    attempt,
                    self.retries + 1,
                    destination.name,
                    exc,
                    delay,
                )
                if self._sleep_cancellable(delay):
                    part.unlink(missing_ok=True)
                    self._task_finish(task, "cancelled", 0)
                    raise CancelledError("download cancelled") from None

        raise DownloadError(f"failed to download {destination.name}", detail=str(last_error))

    def _rollback_bytes(self, part: Path) -> None:
        size = part.stat().st_size if part.exists() else 0
        with self._lock:
            self._progress.bytes_done = max(0, self._progress.bytes_done - size)
            self._update_speed()

    def _stream_to_part(self, spec: DownloadSpec, part: Path) -> int:
        part.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with self._lock:
            self._progress.current_file = spec.destination.name
        self._emit()
        try:
            with part.open("wb") as handle:
                for chunk in self.http.stream(spec.url, chunk_size=self.chunk_size):
                    if self._cancel.is_set():
                        raise CancelledError("download cancelled")
                    handle.write(chunk)
                    written += len(chunk)
                    with self._lock:
                        self._progress.bytes_done += len(chunk)
                        self._update_speed()
                    self._emit()
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
                raise PermissionError_(f"cannot write to {part.parent}", detail=str(exc)) from exc
            if exc.errno == errno.ENOSPC:
                raise DiskSpaceError(f"no space left on device for {part.parent}") from exc
            raise
        return written

    def _verify(self, part: Path, spec: DownloadSpec) -> None:
        if spec.size and part.stat().st_size != spec.size:
            raise IntegrityError(
                f"size mismatch for {spec.destination.name}",
                detail=f"expected {spec.size}, got {part.stat().st_size}",
            )
        for algorithm in ("sha1", "sha512"):
            expected = getattr(spec, algorithm)
            if expected:
                digest = hashlib.new(algorithm)
                with part.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest().lower() != expected.lower():
                    raise IntegrityError(
                        f"{algorithm} checksum mismatch for {spec.destination.name}"
                    )

    @staticmethod
    def _commit(part: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part, destination)
        if os.name == "posix":
            try:
                dir_fd = os.open(destination.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass

    def _is_complete(self, destination: Path, spec: DownloadSpec) -> bool:
        if not destination.exists():
            return False
        size = destination.stat().st_size
        if spec.size and size != spec.size:
            return False
        if spec.sha1 or spec.sha512:
            try:
                self._verify(destination, spec)
            except IntegrityError:
                return False
            return True
        return bool(spec.size)

    def _validate_destination(self, destination: Path, size: int) -> None:
        parent = destination.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PermissionError_(f"cannot create directory {parent}", detail=str(exc)) from exc
        if not os.access(parent, os.W_OK):
            raise PermissionError_(f"directory is not writable: {parent}")
        if size > 0:
            usage = shutil.disk_usage(parent)
            if usage.free < size + DISK_HEADROOM:
                raise DiskSpaceError(
                    f"not enough disk space in {parent}",
                    detail=f"need {size + DISK_HEADROOM} bytes, {usage.free} available",
                )

    def _sleep_cancellable(self, seconds: float) -> bool:
        """Sleep unless cancelled; returns True if cancelled."""
        return self._cancel.wait(seconds)

    def _update_speed(self) -> None:
        elapsed = max(0.001, time.monotonic() - self._started_at)
        self._progress.speed_bps = self._progress.bytes_done / elapsed

    def _emit(self) -> None:
        callback = self.progress_callback
        if callback is None:
            return
        with self._lock:
            snapshot = Progress(**vars(self._progress))
        try:
            callback(snapshot)
        except Exception:
            log.exception("progress callback raised")

    # -- persistence --------------------------------------------------------
    def _task_start(self, spec: DownloadSpec) -> DownloadTask | None:
        if self.repo is None:
            return None
        task = self.repo.get(spec.task_id) if spec.task_id else None
        if task is None:
            task = DownloadTask(
                id=spec.task_id or spec.key,
                url=spec.url,
                destination=str(spec.destination),
                expected_sha1=spec.sha1,
                expected_size=spec.size,
                category=spec.category,
            )
        task.status = "running"
        task.bytes_total = spec.size
        task.attempts += 1
        task.error = ""
        return self.repo.upsert(task)

    def _task_finish(
        self, task: DownloadTask | None, status: str, written: int, error: str = ""
    ) -> None:
        if task is None or self.repo is None:
            return
        task.status = status
        task.bytes_done = written
        task.error = error
        task.updated_at = utc_now()
        self.repo.upsert(task)


def specs_from_mapping(mapping: Iterable[tuple[str, Path, str]]) -> list[DownloadSpec]:
    return [DownloadSpec(url=url, destination=path, sha1=sha) for url, path, sha in mapping]
