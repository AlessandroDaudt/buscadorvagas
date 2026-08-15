import json

import httpx
import pytest

from job_hunt.http_client import SafeHttpClient, UnsafeRedirectError, UnsupportedContentTypeError
from job_hunt.security.urls import ResponseTooLargeError


def client(tmp_path, handler, **kwargs):
    return SafeHttpClient(
        connector="fixture", resolver=lambda _host: ["93.184.216.34"],
        transport=httpx.MockTransport(handler), rate_limit_seconds=0,
        cache_directory=tmp_path / "cache", audit_path=tmp_path / "audit.jsonl", **kwargs,
    )


def test_audited_json_request_and_cache(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})
    with client(tmp_path, handler) as http:
        assert http.get_json("https://jobs.example/jobs", allowed_hosts={"jobs.example"}) == {"ok": True}
        assert http.get_json("https://jobs.example/jobs", allowed_hosts={"jobs.example"}) == {"ok": True}
    assert len(calls) == 1
    audits = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert {item["policy"] for item in audits} == {"allowed", "cache_hit"}
    assert set(audits[0]) == {"timestamp", "connector", "domain", "method", "status", "duration", "size", "policy"}


def test_unsafe_redirect_content_type_and_size_are_rejected(tmp_path):
    def redirect(_request):
        return httpx.Response(302, headers={"location": "https://evil.example/secret"})
    with client(tmp_path, redirect) as http:
        with pytest.raises(UnsafeRedirectError):
            http.get_json("https://jobs.example/jobs", allowed_hosts={"jobs.example"})
    def wrong(_request):
        return httpx.Response(200, content=b"html", headers={"content-type": "text/html"})
    with client(tmp_path, wrong) as http:
        with pytest.raises(UnsupportedContentTypeError):
            http.get_json("https://jobs.example/jobs", allowed_hosts={"jobs.example"}, cache_ttl_seconds=0)
    def large(_request):
        return httpx.Response(200, content=b"12345", headers={"content-type": "application/json"})
    with client(tmp_path, large, max_response_bytes=4) as http:
        with pytest.raises(ResponseTooLargeError):
            http.get_json("https://jobs.example/jobs", allowed_hosts={"jobs.example"}, cache_ttl_seconds=0)


def test_http_and_private_hosts_are_rejected(tmp_path):
    with client(tmp_path, lambda _: httpx.Response(200, json={})) as http:
        with pytest.raises(Exception):
            http.get_json("http://jobs.example/jobs", allowed_hosts={"jobs.example"})
    private = SafeHttpClient(
        connector="fixture", resolver=lambda _host: ["127.0.0.1"],
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
        rate_limit_seconds=0, cache_directory=tmp_path / "private",
        audit_path=tmp_path / "private-audit.jsonl",
    )
    with private:
        with pytest.raises(Exception):
            private.get_json("https://jobs.example/jobs", allowed_hosts={"jobs.example"})
