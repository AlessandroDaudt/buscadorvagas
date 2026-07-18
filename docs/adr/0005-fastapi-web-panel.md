# ADR 0005: FastAPI server-rendered web panel

- Status: Accepted
- Date: 2026-07-18

## Context

The project needs a small local dashboard, authenticated administrative operations, job filtering,
document generation, and an audited application pipeline. Adding a separate JavaScript build system
would increase operational and dependency complexity for a single-user installation.

## Decision

Use FastAPI with server-rendered Jinja templates and small package-owned static CSS/JavaScript files.
Use the existing SQLAlchemy session layer directly through request-scoped dependencies. Do not
enable OpenAPI documentation endpoints in the deployed app.

Authentication uses an Argon2 password hash from the environment and an authenticated, signed,
HTTP-only session cookie. State-changing requests require a session-bound CSRF token. The app also
enforces trusted hosts, request-size limits, login rate limiting, strict SameSite cookies, a
Content-Security-Policy without inline scripts, and safe response headers. Secret settings expose
only a configured/not-configured boolean.

## Consequences

- Local installation remains one Python application with no Node build step.
- Production should terminate TLS before the app and keep secure cookies enabled.
- In-memory login throttling is process-local; a shared rate limiter is recommended if production
  runs multiple web replicas.
- The application exposes no endpoint that submits a job application automatically.
