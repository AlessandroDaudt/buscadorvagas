import httpx
import pytest

from job_hunt.security.urls import (
    ResponseTooLargeError,
    SafeHttpClient,
    UnsafeUrlError,
    validate_public_http_url,
)


def public_ip(_host):
    return ["93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/admin",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
    ],
)
def test_private_or_unsafe_urls_are_rejected(url):
    with pytest.raises(UnsafeUrlError):
        validate_public_http_url(url, resolver=public_ip)


def test_allowlist_is_enforced():
    with pytest.raises(UnsafeUrlError, match="allowlisted"):
        validate_public_http_url(
            "https://example.com/jobs",
            resolver=public_ip,
            allowed_hosts={"api.example.com"},
        )


def test_safe_client_blocks_redirect_to_private_ip():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    client = SafeHttpClient(
        resolver=public_ip,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(UnsafeUrlError):
            client.get_bytes("https://example.com/jobs")
    finally:
        client.close()


def test_safe_client_limits_streamed_response():
    client = SafeHttpClient(
        max_response_bytes=4,
        resolver=public_ip,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"12345")),
    )
    try:
        with pytest.raises(ResponseTooLargeError):
            client.get_bytes("https://example.com/jobs")
    finally:
        client.close()


def test_safe_client_parses_json():
    client = SafeHttpClient(
        resolver=public_ip,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True})),
    )
    try:
        assert client.get_json("https://example.com/jobs") == {"ok": True}
    finally:
        client.close()
