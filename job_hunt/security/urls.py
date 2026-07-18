"""SSRF-resistant outbound HTTP utilities.

DNS validation is repeated for every redirect. Production deployments should also use
egress firewall rules because application checks alone cannot fully prevent DNS rebinding.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable, Iterable
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

import httpx

DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_REDIRECTS = 3


class UnsafeUrlError(ValueError):
    pass


class ResponseTooLargeError(ValueError):
    pass


Resolver = Callable[[str], Iterable[str]]


def _default_resolver(hostname: str) -> list[str]:
    return sorted(
        {
            cast(str, item[4][0])
            for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        }
    )


def _is_forbidden_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def validate_public_http_url(
    url: str,
    *,
    resolver: Resolver | None = None,
    allowed_hosts: set[str] | None = None,
) -> str:
    if len(url) > 2_000:
        raise UnsafeUrlError("URL exceeds 2000 characters")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrlError("Only HTTP and HTTPS URLs are allowed")
    if not parsed.hostname:
        raise UnsafeUrlError("URL must include a hostname")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("Credentials in URLs are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrlError("Invalid URL port") from exc
    if port not in {None, 80, 443}:
        raise UnsafeUrlError("Only ports 80 and 443 are allowed")

    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".localhost"):
        raise UnsafeUrlError("Localhost URLs are not allowed")
    if allowed_hosts is not None and hostname not in {host.casefold() for host in allowed_hosts}:
        raise UnsafeUrlError(f"Host is not allowlisted: {hostname}")

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and _is_forbidden_ip(literal_ip):
        raise UnsafeUrlError("Private or non-routable IP addresses are not allowed")

    if resolver is not None:
        try:
            addresses = [ipaddress.ip_address(value) for value in resolver(hostname)]
        except (OSError, ValueError) as exc:
            raise UnsafeUrlError(f"Hostname could not be safely resolved: {hostname}") from exc
        if not addresses:
            raise UnsafeUrlError(f"Hostname did not resolve: {hostname}")
        if any(_is_forbidden_ip(address) for address in addresses):
            raise UnsafeUrlError("Hostname resolves to a private or non-routable address")
    return url


class SafeHttpClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        resolver: Resolver = _default_resolver,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not 1 <= max_response_bytes <= 50 * 1024 * 1024:
            raise ValueError("max_response_bytes must be between 1 byte and 50 MiB")
        self.max_response_bytes = max_response_bytes
        self.resolver = resolver
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "autopilot-jobhunt/0.5 (+respectful job feed client)"},
        )

    def get_bytes(self, url: str, *, allowed_hosts: set[str] | None = None) -> bytes:
        current_url = url
        for redirect_number in range(MAX_REDIRECTS + 1):
            validate_public_http_url(
                current_url,
                resolver=self.resolver,
                allowed_hosts=allowed_hosts,
            )
            with self.client.stream("GET", current_url) as response:
                if response.is_redirect:
                    if redirect_number == MAX_REDIRECTS:
                        raise UnsafeUrlError("Too many redirects")
                    location = response.headers.get("location")
                    if not location:
                        raise UnsafeUrlError("Redirect response is missing Location")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > self.max_response_bytes:
                    raise ResponseTooLargeError("Response exceeds configured size limit")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self.max_response_bytes:
                        raise ResponseTooLargeError("Response exceeds configured size limit")
                    chunks.append(chunk)
                return b"".join(chunks)
        raise UnsafeUrlError("Redirect handling failed")

    def get_json(self, url: str, *, allowed_hosts: set[str] | None = None) -> Any:
        content = self.get_bytes(url, allowed_hosts=allowed_hosts)
        try:
            return json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Remote response is not valid UTF-8 JSON") from exc

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> SafeHttpClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()
