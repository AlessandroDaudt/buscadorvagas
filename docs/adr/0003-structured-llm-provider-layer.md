# ADR 0003: Structured and budgeted LLM provider layer

- Status: Accepted
- Date: 2026-07-18

## Context

The legacy scanner asks a model for an unvalidated JSON array and lets the model assign the entire
score. The personalized system requires explainability, provider fallback, optional consensus,
cost limits, caching, and resistance to instructions embedded in job descriptions.

## Decision

Use a provider-neutral router with validated Pydantic outputs. OpenAI uses the Responses API parse
helper; OpenRouter, Gemini, and local servers use their documented OpenAI-compatible JSON Schema
surface; Anthropic uses a forced structured tool call. The deterministic engine owns component
weights and the final score. A model may adjust each component by at most ten points.

Prompts are versioned package resources. Job descriptions are explicitly delimited as untrusted
data. Every persisted analysis records its provider, model, prompt version, cache key, token usage,
and estimated cost. LLM analysis can be disabled without disabling deterministic scoring.

## Consequences

- Invalid model output cannot enter the domain model.
- Provider failures fall back without losing the deterministic result.
- Consensus is optional and may increase cost.
- Pricing must be configured and maintained; zero means cost is unknown, not free.
- Native provider behavior remains isolated behind one protocol.
