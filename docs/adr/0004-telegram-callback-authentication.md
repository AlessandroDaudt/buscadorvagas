# ADR 0004: Signed Telegram callbacks

- Status: Accepted
- Date: 2026-07-18

## Context

Telegram inline buttons can mutate saved jobs, application status, document generation, and muted
preferences. Callback data arrives from an external HTTP request and cannot be trusted solely because
it references the configured bot.

## Decision

Encode only an allowlisted compact action and the persisted job UUID. Authenticate the payload with
an HMAC-SHA256 signature derived from `TELEGRAM_CALLBACK_SECRET`, truncated to remain within
Telegram's 64-byte callback limit. Validate the configured chat ID and, when configured, an allowlist
of Telegram user IDs before accepting the action.

There is intentionally no automatic-application action. `Candidatarei` and `Candidatado` only change
the locally tracked application state after a deliberate user click.

## Consequences

- Forged and cross-chat callbacks are rejected.
- Rotating the callback secret invalidates outstanding buttons.
- Opening the official job URL remains a regular URL button and performs no local state mutation.
