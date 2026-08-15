"""Browser security controls for the unauthenticated, loopback-only panel."""

from __future__ import annotations

import os
import secrets
from urllib.parse import urlsplit

from pydantic import Field
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from job_hunt.domain.models import StrictModel


class PanelSecuritySettings(StrictModel):
    # username/password_hash remain accepted for backwards-compatible configuration,
    # but are deliberately unused: this panel has no authentication layer.
    username: str = "local"
    password_hash: str | None = None
    session_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(48), min_length=32)
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost",
            "http://localhost:8000",
            "http://127.0.0.1",
            "http://127.0.0.1:8000",
            "http://testserver",
        ]
    )
    secure_cookie: bool = False
    session_max_age_seconds: int = Field(default=28_800, ge=300, le=604_800)
    max_request_bytes: int = Field(default=16_777_216, ge=1024, le=20_971_520)

    @classmethod
    def from_environment(cls) -> PanelSecuritySettings:
        session_secret = os.getenv("PANEL_SESSION_SECRET") or secrets.token_urlsafe(48)
        hosts = [
            host.strip()
            for host in os.getenv("PANEL_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
            if host.strip()
        ]
        return cls(
            session_secret=session_secret,
            allowed_hosts=hosts,
            secure_cookie=os.getenv("PANEL_SECURE_COOKIE", "false").lower() == "true",
        )


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request: Request, supplied: str | None) -> None:
    expected = request.session.get("csrf_token")
    if (
        not isinstance(expected, str)
        or not supplied
        or not secrets.compare_digest(expected, supplied)
    ):
        raise ValueError("invalid CSRF token")


def validate_local_origin(request: Request) -> None:
    """Reject cross-site browser mutations even when a CSRF token is leaked."""
    raw = request.headers.get("origin") or request.headers.get("referer")
    if not raw:
        return
    parsed = urlsplit(raw)
    security: PanelSecuritySettings = request.app.state.security
    host = request.headers.get("host", "").casefold()
    hostname = (parsed.hostname or "").casefold()
    allowed_hostnames = {item.casefold() for item in security.allowed_hosts}
    same_host = parsed.netloc.casefold() == host and hostname in allowed_hostnames
    if parsed.scheme not in {"http", "https"} or not same_host:
        raise ValueError("invalid request origin")


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > self.max_bytes:
                    await JSONResponse({"detail": "request too large"}, status_code=413)(
                        scope, receive, send
                    )
                    return
            except ValueError:
                await JSONResponse({"detail": "invalid content length"}, status_code=400)(
                    scope, receive, send
                )
                return
        consumed = 0

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    raise RequestTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestTooLarge:
            await JSONResponse({"detail": "request too large"}, status_code=413)(
                scope, receive, send
            )


class RequestTooLarge(Exception):
    pass


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def add_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; style-src 'self'; "
                    "script-src 'self'; img-src 'self' data:; "
                    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                )
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, add_headers)
