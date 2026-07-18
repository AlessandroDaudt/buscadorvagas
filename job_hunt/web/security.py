"""Authentication, CSRF, rate limiting, and browser security controls."""

from __future__ import annotations

import os
import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import Field
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from job_hunt.domain.models import StrictModel


class PanelSecuritySettings(StrictModel):
    username: str = Field(default="admin", min_length=1, max_length=100)
    password_hash: str = Field(min_length=20, max_length=1000)
    session_secret: str = Field(min_length=32, max_length=1000)
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1", "testserver"])
    secure_cookie: bool = True
    session_max_age_seconds: int = Field(default=28_800, ge=300, le=604_800)
    max_request_bytes: int = Field(default=1_048_576, ge=1024, le=20_971_520)

    @classmethod
    def from_environment(cls) -> PanelSecuritySettings:
        password_hash = os.getenv("PANEL_PASSWORD_HASH")
        session_secret = os.getenv("PANEL_SESSION_SECRET")
        if not password_hash or not session_secret:
            raise RuntimeError(
                "PANEL_PASSWORD_HASH and PANEL_SESSION_SECRET are required to start the panel"
            )
        hosts = [
            host.strip()
            for host in os.getenv("PANEL_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
            if host.strip()
        ]
        return cls(
            username=os.getenv("PANEL_USERNAME", "admin"),
            password_hash=password_hash,
            session_secret=session_secret,
            allowed_hosts=hosts,
            secure_cookie=os.getenv("PANEL_SECURE_COOKIE", "true").lower() == "true",
        )


class PasswordVerifier:
    def __init__(self, password_hash: str) -> None:
        self.password_hash = password_hash
        self.hasher = PasswordHasher()
        self._dummy_hash = self.hasher.hash("not-the-password")

    def verify(self, username_ok: bool, password: str) -> bool:
        candidate_hash = self.password_hash if username_ok else self._dummy_hash
        try:
            valid = self.hasher.verify(candidate_hash, password)
        except (VerificationError, InvalidHashError):
            return False
        return bool(valid and username_ok)


class LoginRateLimiter:
    def __init__(self, *, attempts: int = 5, window_seconds: int = 300) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def check(self, identity: str) -> bool:
        now = time.monotonic()
        failures = self._failures[identity]
        while failures and failures[0] <= now - self.window_seconds:
            failures.popleft()
        return len(failures) < self.attempts

    def failure(self, identity: str) -> None:
        self._failures[identity].append(time.monotonic())

    def success(self, identity: str) -> None:
        self._failures.pop(identity, None)


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request: Request, supplied: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not isinstance(expected, str) or not supplied or not secrets.compare_digest(expected, supplied):
        raise ValueError("invalid CSRF token")


def authenticated(request: Request) -> bool:
    return request.session.get("user") == request.app.state.security.username


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
                    await JSONResponse({"detail": "request too large"}, status_code=413)(scope, receive, send)
                    return
            except ValueError:
                await JSONResponse({"detail": "invalid content length"}, status_code=400)(scope, receive, send)
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
            await JSONResponse({"detail": "request too large"}, status_code=413)(scope, receive, send)


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
