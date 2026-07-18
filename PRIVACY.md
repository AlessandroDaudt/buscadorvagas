# Privacy — what leaves your machine

`autopilot-jobhunt` reads your resume and fetches job pages. This page states exactly
where that content goes, so you can pick a setup that matches your comfort level.

## The short version

- **This tool never applies to a job.** It drafts a resume + cover letter locally for
  your review (see [SECURITY.md](SECURITY.md)). You send applications yourself.
- With the legacy scanner, discovery requests go through **TinyFish**. Official Greenhouse and
  Lever connectors call their public APIs directly. Local fixture tests send nothing.
- If LLM analysis is enabled, the configured candidate profile plus job description is sent to
  the selected provider. Deterministic scoring works without an LLM.
- **The default provider (`openrouter`) is a cloud provider.** Out of the box, your
  resume and the JD transit OpenRouter. Switch to `claude_cli` before your first run if
  you want to keep content on your existing local Claude session.

## What leaves your machine, by LLM provider

Set the provider with `llm_provider` in `config.json` (or `LLM_PROVIDER` in `.env`).

| Provider (`llm_provider`) | Resume + JD content goes to | Key needed | Notes |
|---|---|---|---|
| `claude_cli` | **Your existing local Claude Code / Claude session** | none (uses `claude` CLI login) | No separate cloud upload beyond your existing Anthropic relationship. Best for privacy. |
| `openrouter` **(default)** | **OpenRouter (cloud, third party)** | API key | Routes to whichever model you pick; content transits OpenRouter + the chosen model host. |
| `anthropic` | **Anthropic (cloud)** | API key | Direct Anthropic API. |
| `openai` | **OpenAI (cloud)** | API key | Direct API with schema-validated output. |
| `gemini` | **Google Gemini (cloud)** | API key | Google-hosted configured model. |
| `local` | **Configured OpenAI-compatible endpoint** | optional | Local only when the endpoint and all dependencies are local. |

Consensus mode can send the same bounded input to two configured providers. It is disabled by
default because it increases both disclosure and cost.

## Other outbound data (opt-in only)

- **Telegram** (configured via `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID`) — sends the top
  matches, score explanation, salary estimate, restrictions and URL to Telegram's servers.
  Notification only; it never applies to anything.

## What is stored locally

- **`state/autopilot.db`** (or PostgreSQL) — profile records, jobs, snapshots, analyses,
  LLM usage, documents, notifications and application history. Gitignored locally.
- **`state/last_scan.json` / `state/metrics.json`** — compatibility output and aggregate
  operational metrics. Gitignored.
- **`output/<company>-<date>/`** — drafted resumes and cover letters. **These contain
  your personal content.** Gitignored.
- **`resume/` and `config/candidate_profile.json`** — factual profile and resume data. This
  personalized baseline intentionally versions non-contact professional facts; contact fields
  are null. Use a private fork or encryption before adding sensitive contact data.
- **`config.json` / `.env`** — provider choice and secrets. Both are gitignored; protect backups.
- **`scan.log`** — run logs. Gitignored.

## Telemetry

None. This tool sends no analytics, usage pings, or crash reports anywhere.
