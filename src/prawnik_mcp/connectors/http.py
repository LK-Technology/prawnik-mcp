"""Polite HTTP client for public legal-data sources.

Rules enforced here (not left to callers):
- only allowlisted hosts over https (default: hosts of implemented sources in the catalog);
  every redirect hop is re-checked;
- hosts resolving to private/loopback/link-local addresses are refused (SSRF guard);
- identifiable User-Agent, timeout, maximum response size;
- at most one request per host at a time, with a per-host minimum delay (catalog rate limits);
- cookies persist per client (needed by form-based sources);
- retry with exponential backoff on network errors, 429 and 5xx, honouring Retry-After;
- 404 is reported as `NotFoundUpstream`, everything else as `SourceUnavailable`.

Anti-bot challenges (CAPTCHA/WAF, e.g. EUR-Lex returning 202 with an empty body)
are never bypassed: they surface as `SourceUnavailable`.
"""

from __future__ import annotations

import email.utils
import ipaddress
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from prawnik_mcp import __version__

USER_AGENT = f"prawnik-mcp/{__version__} (+https://github.com/LK-Technology/prawnik-mcp; open-source legal research tool)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class UpstreamError(Exception):
    """Base class for upstream failures."""

    def __init__(self, url: str, reason: str, http_status: int | None = None):
        super().__init__(f"{reason} ({url}, http={http_status})")
        self.url = url
        self.reason = reason
        self.http_status = http_status


class SourceUnavailable(UpstreamError):
    """Network error, 5xx, blocked/unexpected response. Says nothing about whether the resource exists."""


class NotFoundUpstream(UpstreamError):
    """The upstream answered 404 for this URL."""


class BlockedUrl(SourceUnavailable):
    """URL (or a redirect hop) outside the allowlist, non-https, or resolving to a private address."""


@dataclass
class FetchResult:
    url: str  # final URL after redirects
    status: int
    content: bytes
    content_type: str
    headers: dict[str, str]
    fetched_at: datetime
    redirects: list[str] = field(default_factory=list)


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified
    )


def _parse_retry_after(value: str | None, now: float) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return max(0.0, dt.timestamp() - now)


class PoliteClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        allowlist: frozenset[str] | set[str] | None = None,
        timeout: float = 30.0,
        max_bytes: int = 40 * 1024 * 1024,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        max_retry_after: float = 120.0,
        min_delay: float | None = None,
        host_delays: dict[str, float] | None = None,
        max_redirects: int = 5,
        resolve_dns: bool | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        from prawnik_mcp import sources

        self.allowlist = frozenset(h.lower() for h in (allowlist if allowlist is not None else sources.allowed_hosts()))
        # Per-host delays from the catalog; an explicit min_delay overrides all hosts (tests use 0).
        self.host_delays = {} if min_delay is not None else dict(host_delays or sources.host_delays())
        self.max_bytes = max_bytes
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.max_retry_after = max_retry_after
        self.min_delay = 1.0 if min_delay is None else min_delay
        self.max_redirects = max_redirects
        # With an injected transport (offline tests) there is no real DNS lookup.
        self.resolve_dns = (transport is None) if resolve_dns is None else resolve_dns
        self._sleep = sleep
        self._clock = clock
        self._host_locks: dict[str, threading.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._locks_guard = threading.Lock()
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ guards
    def check_url(self, url: str) -> str:
        """Return the lower-cased host if the URL may be fetched, else raise BlockedUrl."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https":
            raise BlockedUrl(url, "dozwolone są tylko adresy https")
        if parts.username or parts.password:
            raise BlockedUrl(url, "adres z danymi logowania jest niedozwolony")
        if host not in self.allowlist:
            raise BlockedUrl(url, f"host {host!r} spoza listy dozwolonych źródeł")
        if parts.port not in (None, 443):
            raise BlockedUrl(url, "niestandardowy port jest niedozwolony")
        if self.resolve_dns:
            try:
                infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            except OSError as e:
                raise SourceUnavailable(url, f"błąd DNS: {e}") from e
            for info in infos:
                if not _is_public_ip(info[4][0]):
                    raise BlockedUrl(url, f"host {host} wskazuje adres niepubliczny {info[4][0]}")
        return host

    def _host_lock(self, host: str) -> threading.Lock:
        with self._locks_guard:
            return self._host_locks.setdefault(host, threading.Lock())

    # ------------------------------------------------------------------ fetching
    def get(self, url: str, *, accept: str | None = None, headers: dict[str, str] | None = None) -> FetchResult:
        return self.request("GET", url, accept=accept, headers=headers)

    def post(self, url: str, *, data: dict[str, str] | None = None, json: object | None = None,
             accept: str | None = None, headers: dict[str, str] | None = None) -> FetchResult:
        """POST (form or JSON). Redirects after POST are followed with GET (303 semantics)."""
        return self.request("POST", url, accept=accept, headers=headers, data=data, json=json)

    def request(self, method: str, url: str, *, accept: str | None = None, headers: dict[str, str] | None = None,
                data: dict[str, str] | None = None, json: object | None = None) -> FetchResult:
        hdrs = dict(headers or {})
        if accept:
            hdrs["Accept"] = accept
        redirects: list[str] = []
        current = url
        for _ in range(self.max_redirects + 1):
            host = self.check_url(current)
            resp_status, resp_headers, content = self._get_with_retry(current, host, hdrs, method, data, json)
            method, data, json = "GET", None, None  # any redirect continues as GET
            if resp_status in (301, 302, 303, 307, 308):
                loc = resp_headers.get("location")
                if not loc:
                    raise SourceUnavailable(current, "przekierowanie bez nagłówka Location", resp_status)
                nxt = urljoin(current, loc)
                np = urlsplit(nxt)
                if np.scheme == "http" and (np.hostname or "").lower() in self.allowlist and np.port in (None, 80):
                    # Cellar redirects to plain http on the same host; upgrade instead of downgrading.
                    nxt = urlunsplit(("https", np.hostname, np.path, np.query, ""))
                redirects.append(current)
                current = nxt
                continue
            return self._finish(current, resp_status, resp_headers, content, redirects)
        raise SourceUnavailable(url, "zbyt wiele przekierowań")

    def _finish(self, url: str, status: int, headers: dict[str, str], content: bytes, redirects: list[str]) -> FetchResult:
        if status == 404:
            raise NotFoundUpstream(url, "zasób nie istnieje u źródła (404)", 404)
        if status == 202 or (status == 200 and not content):
            # EUR-Lex style bot challenge / async placeholder: never bypass, treat as unavailable.
            raise SourceUnavailable(url, "pusta odpowiedź lub wyzwanie anty-botowe; nie obchodzimy", status)
        if status != 200:
            raise SourceUnavailable(url, f"nieoczekiwany status HTTP {status}", status)
        return FetchResult(
            url=url, status=status, content=content,
            content_type=headers.get("content-type", "application/octet-stream"),
            headers=headers, fetched_at=datetime.now(UTC), redirects=redirects,
        )

    def _get_with_retry(self, url: str, host: str, headers: dict[str, str], method: str = "GET",
                        data: dict[str, str] | None = None, json: object | None = None) -> tuple[int, dict[str, str], bytes]:
        attempt = 0
        while True:
            try:
                status, resp_headers, content = self._one_request(url, host, headers, method, data, json)
            except httpx.TransportError as e:
                if attempt >= self.max_retries:
                    raise SourceUnavailable(url, f"błąd sieci: {type(e).__name__}: {e}") from e
                self._sleep(self.backoff_base * (2 ** attempt))
                attempt += 1
                continue
            if status in RETRY_STATUSES:
                if attempt >= self.max_retries:
                    raise SourceUnavailable(url, f"HTTP {status} po {attempt + 1} próbach", status)
                wait = self.backoff_base * (2 ** attempt)
                ra = _parse_retry_after(resp_headers.get("retry-after"), time.time())
                if ra is not None:
                    if ra > self.max_retry_after:
                        raise SourceUnavailable(url, f"HTTP {status}; Retry-After {ra:.0f}s przekracza limit", status)
                    wait = max(wait, ra)
                self._sleep(wait)
                attempt += 1
                continue
            return status, resp_headers, content

    def _one_request(self, url: str, host: str, headers: dict[str, str], method: str = "GET",
                     data: dict[str, str] | None = None, json: object | None = None) -> tuple[int, dict[str, str], bytes]:
        delay = self.host_delays.get(host, self.min_delay)
        with self._host_lock(host):
            last = self._host_last.get(host)
            if last is not None:
                gap = self._clock() - last
                if gap < delay:
                    self._sleep(delay - gap)
            try:
                with self._client.stream(method, url, headers=headers, data=data, json=json) as resp:
                    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                    declared = resp_headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > self.max_bytes:
                        raise SourceUnavailable(url, f"odpowiedź większa niż limit {self.max_bytes} B", resp.status_code)
                    buf = bytearray()
                    if resp.status_code not in (301, 302, 303, 307, 308):
                        for chunk in resp.iter_bytes():
                            buf += chunk
                            if len(buf) > self.max_bytes:
                                raise SourceUnavailable(url, f"odpowiedź większa niż limit {self.max_bytes} B", resp.status_code)
                    return resp.status_code, resp_headers, bytes(buf)
            finally:
                self._host_last[host] = self._clock()
