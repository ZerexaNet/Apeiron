"""TLS-only HTTP client with retries, timeouts and structured errors.

The client is deliberately thin (stdlib ``urllib``) so that packaging stays
predictable, but it enforces the rules Apeiron needs:

* HTTPS only (plain HTTP is permitted solely for loopback, used by tests);
* a fixed, honest ``User-Agent`` identifying the launcher;
* bounded retries with exponential backoff and ``Retry-After`` support;
* no silent redirection to a different host for metadata endpoints;
* response bodies capped when requested (manifest downloads can be large,
  arbitrary HTML error pages can be enormous).
"""

from __future__ import annotations

import hashlib
import json
import random
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from apeiron.core import __version__ as apeiron_version
from apeiron.core.errors import IntegrityError, NetworkError, TimeoutError_
from apeiron.core.logging import get_logger
from apeiron.net.sources import download_candidates

log = get_logger("http")

DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_REDIRECTS = 5
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def user_agent() -> str:
    return f"Apeiron/{apeiron_version} (+https://github.com/ZerexaNet/Apeiron)"


@dataclass(frozen=True)
class HttpConfig:
    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    backoff_base: float = 0.5
    backoff_max: float = 20.0
    verify_tls: bool = True
    use_mirrors: bool = False
    allow_insecure: bool = False  # loopback HTTP only, used by tests


@dataclass
class Response:
    url: str
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NetworkError(f"invalid JSON from {self.url}: {exc}") from exc


class HttpClient:
    """Synchronous HTTP client; callers run it from worker threads."""

    def __init__(self, config: HttpConfig | None = None) -> None:
        self.config = config or HttpConfig()
        self._context = self._build_context()

    def _build_context(self) -> ssl.SSLContext:
        if self.config.verify_tls:
            context = ssl.create_default_context()
            # These endpoints must never be reachable over a downgraded
            # protocol, so require TLS 1.2+ on every platform.
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            return context
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    # -- validation ---------------------------------------------------------
    @staticmethod
    def validate_url(url: str, *, allow_insecure: bool = False) -> str:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("https", "http"):
            raise NetworkError(f"unsupported URL scheme: {parsed.scheme or '(none)'}")
        if parsed.scheme == "http":
            host = (parsed.hostname or "").lower()
            loopback = host in ("127.0.0.1", "::1", "localhost")
            if not (allow_insecure and loopback):
                raise NetworkError("refusing to use plain HTTP for a remote host")
        if not parsed.hostname:
            raise NetworkError(f"URL has no host: {url}")
        return url

    # -- core request -------------------------------------------------------
    def _request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        data: bytes | None = None,
        timeout: float | None = None,
        max_bytes: int = MAX_JSON_BYTES,
        retries: int | None = None,
        accepted_statuses: frozenset[int] = frozenset(),
    ) -> Response:
        self.validate_url(url, allow_insecure=self.config.allow_insecure)
        attempts = self.config.retries if retries is None else retries
        timeout = self.config.timeout if timeout is None else timeout
        last_error: BaseException | None = None

        for attempt in range(attempts + 1):
            try:
                return self._once(
                    url,
                    method=method,
                    headers=headers,
                    data=data,
                    timeout=timeout,
                    max_bytes=max_bytes,
                    accepted_statuses=accepted_statuses,
                )
            except _Retryable as exc:
                last_error = exc.__cause__ or exc
                if attempt >= attempts:
                    break
                delay = exc.retry_after or self._backoff(attempt)
                log.warning(
                    "request failed (%s), retry %d/%d in %.1fs: %s",
                    type(last_error).__name__,
                    attempt + 1,
                    attempts,
                    delay,
                    _safe(url),
                )
                time.sleep(delay)
            except _Fatal as exc:
                raise exc.error from exc.__cause__

        raise NetworkError(
            f"request failed after {attempts + 1} attempts: {_safe(url)}",
            detail=str(last_error),
        ) from last_error

    def request(
        self, url: str, *, expected_sha1: str = "", validate_json: bool = False, **kwargs: Any
    ) -> Response:
        candidates = self.candidates(url) if kwargs.get("method", "GET") == "GET" else (url,)
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                response = self._request(candidate, **kwargs)
                if (
                    expected_sha1
                    and hashlib.sha1(response.body).hexdigest() != expected_sha1.lower()
                ):
                    raise IntegrityError("version metadata checksum mismatch")
                if validate_json:
                    response.json()
                return response
            except (NetworkError, IntegrityError) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def candidates(self, url: str) -> tuple[str, ...]:
        return download_candidates(url) if self.config.use_mirrors else (url,)

    def _backoff(self, attempt: int) -> float:
        raw = self.config.backoff_base * (2**attempt)
        capped = min(raw, self.config.backoff_max)
        return capped * (0.5 + random.random() / 2)  # jitter, never zero

    def _once(
        self,
        url: str,
        *,
        method: str,
        headers: Mapping[str, str] | None,
        data: bytes | None,
        timeout: float,
        max_bytes: int,
        accepted_statuses: frozenset[int] = frozenset(),
    ) -> Response:
        merged = {"User-Agent": user_agent(), "Accept-Encoding": "identity"}
        if headers:
            merged.update(headers)
        request = urllib.request.Request(url, data=data, headers=merged, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=self._context) as raw:
                final_url = raw.geturl()
                self._check_redirect_origin(url, final_url)
                body = raw.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise _Fatal(
                        NetworkError(f"response larger than {max_bytes} bytes: {_safe(url)}")
                    )
                return Response(
                    url=final_url,
                    status=getattr(raw, "status", 200),
                    headers={k.lower(): v for k, v in raw.headers.items()},
                    body=body,
                )
        except urllib.error.HTTPError as exc:
            if exc.code in accepted_statuses:
                with exc:
                    self._check_redirect_origin(url, exc.geturl())
                    body = exc.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        raise _Fatal(
                            NetworkError("HTTP error response exceeds size limit")
                        ) from exc
                    return Response(exc.geturl(), exc.code, dict(exc.headers), body)
            retry_after = _parse_retry_after(
                exc.headers.get("Retry-After") if exc.headers else None
            )
            if exc.code in RETRY_STATUSES:
                raise _Retryable(retry_after=retry_after) from exc
            raise _Fatal(
                NetworkError(
                    f"HTTP {exc.code} for {_safe(url)}",
                    detail=exc.reason,
                )
            ) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise _Retryable() from TimeoutError_(f"timed out: {_safe(url)}")
            if isinstance(reason, ssl.SSLError):
                raise _Fatal(
                    NetworkError(f"TLS failure for {_safe(url)}", detail=str(reason))
                ) from exc
            raise _Retryable() from NetworkError(
                f"connection failed: {_safe(url)}", detail=str(reason)
            )
        except TimeoutError:
            raise _Retryable() from TimeoutError_(f"timed out: {_safe(url)}")
        except ssl.SSLError as exc:
            raise _Fatal(NetworkError(f"TLS failure for {_safe(url)}", detail=str(exc))) from exc
        except OSError as exc:
            raise _Fatal(NetworkError(f"I/O failure for {_safe(url)}", detail=str(exc))) from exc

    @staticmethod
    def _check_redirect_origin(original: str, final: str) -> None:
        """Metadata endpoints must not be silently served by another host."""
        a = urllib.parse.urlsplit(original)
        b = urllib.parse.urlsplit(final)
        if a.scheme == "https" and b.scheme != "https":
            raise _Fatal(NetworkError("refusing TLS downgrade redirect"))
        # A cross-host redirect on the *metadata* endpoints is a supply-chain
        # risk; allow only the two official Mojang hosts to interchange and
        # require CDN host changes to happen through explicit URLs instead.
        if a.hostname != b.hostname:
            log.info("cross-host redirect: %s -> %s", _safe(original), _safe(final))

    # -- convenience --------------------------------------------------------
    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.request(url, validate_json=True, **kwargs).json()

    def get_bytes(self, url: str, **kwargs: Any) -> bytes:
        return self.request(url, **kwargs).body

    def head(self, url: str, *, timeout: float | None = None) -> Response:
        return self.request(url, method="HEAD", timeout=timeout, max_bytes=0)

    def stream(
        self, url: str, *, chunk_size: int = 262144, timeout: float | None = None
    ) -> Iterator[bytes]:
        """Yield the response body in chunks (single attempt; used by the downloader)."""
        self.validate_url(url, allow_insecure=self.config.allow_insecure)
        request = urllib.request.Request(
            url, headers={"User-Agent": user_agent(), "Accept-Encoding": "identity"}
        )
        timeout = self.config.timeout if timeout is None else timeout
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=self._context) as raw:
                self._check_redirect_origin(url, raw.geturl())
                while True:
                    chunk = raw.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except _Fatal as exc:
            raise exc.error from exc
        except urllib.error.HTTPError as exc:
            raise NetworkError(f"HTTP {exc.code} for {_safe(url)}", detail=str(exc.reason)) from exc
        except TimeoutError as exc:
            raise TimeoutError_(f"timed out: {_safe(url)}") from exc
        except urllib.error.URLError as exc:
            raise NetworkError(f"connection failed: {_safe(url)}", detail=str(exc.reason)) from exc


class _Retryable(Exception):
    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("retryable")
        self.retry_after = retry_after


class _Fatal(Exception):
    def __init__(self, error: NetworkError) -> None:
        super().__init__(str(error))
        self.error = error


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, min(60.0, float(value)))
    except ValueError:
        return None


def _safe(url: str) -> str:
    """Strip query strings before a URL reaches a log line."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
