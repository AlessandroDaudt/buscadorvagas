# ADR 0005: FastAPI server-rendered web panel

- Status: Accepted
- Date: 2026-07-18

## Context

The project needs a complete single-user local control plane for scans, job review, configuration,
resume versioning, document generation, exports and diagnostics. Adding a separate JavaScript build
system would increase operational and dependency complexity.

## Decision

Use FastAPI with server-rendered Jinja templates and small package-owned static CSS/JavaScript files.
Use the existing SQLAlchemy session layer directly through request-scoped dependencies. Do not
enable OpenAPI documentation endpoints in the deployed app.

The panel intentionally has no authentication and is bound/published only on loopback. A signed,
HTTP-only cookie stores only a CSRF token. State-changing requests require that token and a matching
local Origin/Referer. The app also enforces trusted hosts, request-size limits, strict SameSite
cookies, a Content-Security-Policy without inline scripts/styles, path-safe signed downloads and safe
response headers. Configuration endpoints accept only typed, allowlisted local fields.

## Consequences

- Local installation remains one Python application with no Node build step.
- The panel must not be exposed as a network or public production service.
- Long operations are persisted and run through a bounded local task manager.
- The application exposes no endpoint that submits a job application automatically.
