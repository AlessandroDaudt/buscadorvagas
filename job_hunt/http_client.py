"""Central, audited and SSRF-resistant HTTP client for public job sources."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from job_hunt.security.urls import (
    DEFAULT_MAX_RESPONSE_BYTES,
    MAX_REDIRECTS,
    ResponseTooLargeError,
    UnsafeUrlError,
    _default_resolver,
    validate_public_http_url,
)


class NetworkPolicyError(RuntimeError):
    code = "network_policy_error"


class InvalidDomainError(NetworkPolicyError):
    code = "invalid_domain"


class UnsafeRedirectError(NetworkPolicyError):
    code = "unsafe_redirect"


class UnsupportedContentTypeError(NetworkPolicyError):
    code = "unsupported_content_type"


class BlockedByRobotsError(NetworkPolicyError):
    code = "blocked_by_robots"


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status_code: int
    content: bytes
    content_type: str


_domain_locks_guard = threading.Lock()
_domain_locks: dict[str, threading.Lock] = {}
_last_request: dict[str, float] = {}


def _domain_lock(domain: str) -> threading.Lock:
    with _domain_locks_guard:
        return _domain_locks.setdefault(domain, threading.Lock())


class SafeHttpClient:
    def __init__(
        self,
        *,
        connector: str = "unknown",
        timeout_seconds: float = 20,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        rate_limit_seconds: float = 1.0,
        retries: int = 1,
        cache_directory: Path = Path("state/http_cache"),
        audit_path: Path = Path("state/network_audit.jsonl"),
        resolver=_default_resolver,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.connector = connector
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.rate_limit_seconds = max(0, rate_limit_seconds)
        self.retries = max(0, min(3, retries))
        self.cache_directory = cache_directory
        self.audit_path = audit_path
        self.resolver = resolver
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            transport=transport,
            headers={
                "User-Agent": "autopilot-jobhunt/0.5 (local respectful public-job client)"
            },
        )

    @staticmethod
    def _normalize_allowed_hosts(hosts: Iterable[str]) -> set[str]:
        return {host.rstrip(".").casefold() for host in hosts if host}

    def _validate(self, url: str, allowed_hosts: set[str]) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https":
            raise UnsafeUrlError("Only HTTPS public-source URLs are allowed")
        try:
            validate_public_http_url(
                url,
                resolver=self.resolver,
                allowed_hosts=allowed_hosts,
            )
        except UnsafeUrlError as exc:
            raise InvalidDomainError(str(exc)) from exc

    def _audit(
        self,
        *,
        domain: str,
        status: int | None,
        duration: float,
        size: int,
        policy: str,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connector": self.connector,
            "domain": domain,
            "method": "GET",
            "status": status,
            "duration": round(duration, 4),
            "size": size,
            "policy": policy,
        }
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass

    def _cache_path(self, url: str) -> Path:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_directory / f"{key}.json"

    def _read_cache(self, url: str, ttl_seconds: int) -> HttpResponse | None:
        if ttl_seconds <= 0:
            return None
        path = self._cache_path(url)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - float(raw["saved_at"]) > ttl_seconds:
                return None
            return HttpResponse(
                url=str(raw["url"]),
                status_code=int(raw["status_code"]),
                content=bytes.fromhex(str(raw["content_hex"])),
                content_type=str(raw["content_type"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _write_cache(self, original_url: str, response: HttpResponse) -> None:
        try:
            self.cache_directory.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at": time.time(),
                "url": response.url,
                "status_code": response.status_code,
                "content_type": response.content_type,
                "content_hex": response.content.hex(),
            }
            self._cache_path(original_url).write_text(
                json.dumps(payload, separators=(",", ":")), encoding="utf-8"
            )
        except OSError:
            pass

    def _rate_limit(self, domain: str) -> None:
        with _domain_lock(domain):
            wait = self.rate_limit_seconds - (time.monotonic() - _last_request.get(domain, 0))
            if wait > 0:
                time.sleep(wait)
            _last_request[domain] = time.monotonic()

    def get(
        self,
        url: str,
        *,
        allowed_hosts: set[str],
        accepted_content_types: tuple[str, ...],
        cache_ttl_seconds: int = 900,
    ) -> HttpResponse:
        allowed = self._normalize_allowed_hosts(allowed_hosts)
        cached = self._read_cache(url, cache_ttl_seconds)
        if cached is not None:
            self._audit(
                domain=urlsplit(cached.url).hostname or "",
                status=cached.status_code,
                duration=0,
                size=len(cached.content),
                policy="cache_hit",
            )
            return cached

        started = time.monotonic()
        current_url = url
        domain = urlsplit(url).hostname or ""
        status: int | None = None
        size = 0
        try:
            for redirect_number in range(MAX_REDIRECTS + 1):
                self._validate(current_url, allowed)
                domain = urlsplit(current_url).hostname or domain
                self._rate_limit(domain)
                for attempt in range(self.retries + 1):
                    with self.client.stream("GET", current_url) as response:
                        status = response.status_code
                        if status in {429, 500, 502, 503, 504} and attempt < self.retries:
                            time.sleep(0.5 * (2**attempt))
                            continue
                        if response.is_redirect:
                            if redirect_number == MAX_REDIRECTS:
                                raise UnsafeRedirectError("Too many redirects")
                            location = response.headers.get("location")
                            if not location:
                                raise UnsafeRedirectError("Redirect is missing Location")
                            redirected = urljoin(current_url, location)
                            try:
                                self._validate(redirected, allowed)
                            except NetworkPolicyError as exc:
                                raise UnsafeRedirectError(str(exc)) from exc
                            current_url = redirected
                            break
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
                        if not any(
                            content_type == expected or content_type.endswith("+json")
                            for expected in accepted_content_types
                        ):
                            raise UnsupportedContentTypeError(
                                f"Unexpected Content-Type: {content_type or 'missing'}"
                            )
                        length = response.headers.get("content-length")
                        if length and int(length) > self.max_response_bytes:
                            raise ResponseTooLargeError("Response exceeds configured size limit")
                        chunks: list[bytes] = []
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > self.max_response_bytes:
                                raise ResponseTooLargeError("Response exceeds configured size limit")
                            chunks.append(chunk)
                        result = HttpResponse(current_url, status, b"".join(chunks), content_type)
                        self._write_cache(url, result)
                        self._audit(
                            domain=domain,
                            status=status,
                            duration=time.monotonic() - started,
                            size=size,
                            policy="allowed",
                        )
                        return result
                else:
                    continue
                if current_url != url:
                    continue
            raise UnsafeRedirectError("Redirect handling failed")
        except Exception as exc:
            self._audit(
                domain=domain,
                status=status,
                duration=time.monotonic() - started,
                size=size,
                policy=getattr(exc, "code", type(exc).__name__),
            )
            raise

    def get_json(
        self,
        url: str,
        *,
        allowed_hosts: set[str] | None = None,
        cache_ttl_seconds: int = 900,
    ) -> Any:
        response = self.get(
            url,
            allowed_hosts=allowed_hosts or set(),
            accepted_content_types=("application/json", "text/json"),
            cache_ttl_seconds=cache_ttl_seconds,
        )
        try:
            return json.loads(response.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Remote response is not valid UTF-8 JSON") from exc

    def get_text(
        self,
        url: str,
        *,
        allowed_hosts: set[str],
        cache_ttl_seconds: int = 900,
    ) -> str:
        response = self.get(
            url,
            allowed_hosts=allowed_hosts,
            accepted_content_types=("text/html", "text/plain", "application/xhtml+xml"),
            cache_ttl_seconds=cache_ttl_seconds,
        )
        try:
            return response.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Remote response is not UTF-8 text") from exc

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> SafeHttpClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class RobotsPolicy:
    def __init__(self, client: SafeHttpClient) -> None:
        self.client = client
        self._parsers: dict[str, RobotFileParser] = {}

    def allowed(self, url: str, *, allowed_hosts: set[str]) -> bool:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._parsers:
            robots_url = f"{origin}/robots.txt"
            parser = RobotFileParser(robots_url)
            try:
                text = self.client.get_text(
                    robots_url,
                    allowed_hosts=allowed_hosts,
                    cache_ttl_seconds=86_400,
                )
                parser.parse(text.splitlines())
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {401, 403}:
                    return False
                parser.parse([])
            self._parsers[origin] = parser
        return self._parsers[origin].can_fetch("autopilot-jobhunt/0.5", url)

    def require_allowed(self, url: str, *, allowed_hosts: set[str]) -> None:
        if not self.allowed(url, allowed_hosts=allowed_hosts):
            raise BlockedByRobotsError("robots.txt disallows this URL")

