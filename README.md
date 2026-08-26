# Autopilot Job Hunt — local first

A local agent for job discovery, analysis, and application preparation. It reads public pages configured in `companies.json`, calculates an explainable deterministic score, uses local Ollama for a limited review, and generates reports/documents on your computer. No application is submitted automatically.

## Guarantees

- `LOCAL_ONLY=true` is the default and blocks AI providers, scraping, notifications, and external webhooks.
- There is no cloud fallback and no external API key is required.
- Resumes, profiles, prompts, embeddings, analyses, databases, logs, and documents remain local.
- Job descriptions are untrusted content; they cannot control prompts, URLs, files, or scores.
- Discovery uses only allowlisted public career/ATS URLs; it does not use search engines.
- `draft` only creates files for human review. It does not fill out forms, click Apply, or send data.

## Flow

```text
companies.json
    → public registry/connector
    → HTTPS + SSRF/robots/rate-limit/audit checks
    → normalized and deduplicated job
    → deterministic score
    → structured Ollama adjustment (maximum ±10 per component)
    → local JSON/SQLite
    → local CSV + JSON + HTML + documents
```

Implemented and tested connectors: Greenhouse, Lever, Ashby, SmartRecruiters, Workable, JSON-LD `JobPosting`, and generic static HTML. Playwright is not a required dependency.

## Quick start — Windows 11, WSL2, and RTX 3060

Prerequisites: Docker Desktop using WSL2, a current NVIDIA driver, and PowerShell.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap-local.ps1
```

The bootstrap preserves existing files, builds the image, starts Ollama, downloads `qwen3:8b` and `qwen3-embedding:0.6b`, tests inference/GPU support, and runs diagnostics.

Daily operation:

```powershell
.\scripts\start-local.ps1
docker compose run --rm autopilot autopilot scan
docker compose run --rm autopilot autopilot draft '#1'
docker compose run --rm autopilot autopilot export --min 60 --days 7
docker compose run --rm autopilot autopilot doctor
.\scripts\stop-local.ps1
```

Daily web interface: open `http://127.0.0.1:8000` (no login and no network exposure). Searches, jobs, companies, resume, documents, exports, schedule, and diagnostics are available from the menu. See [docs/WEB_INTERFACE.md](docs/WEB_INTERFACE.md) for workflows and security limits.

MCP over stdio:

```powershell
docker compose run --rm -i autopilot autopilot mcp
```

The complete dashboard is available exclusively at `http://127.0.0.1:8000`, without a login screen. Access is local, with CSRF protection, origin validation, and temporary download links; see [SETUP.md](SETUP.md).

## Configuration

`config.json` defines models, profile, and behavior. `.env` contains only local overrides and an optional secret for signing the CSRF session and temporary downloads. The default is:

```json
{
  "local_only": true,
  "llm_provider": "ollama",
  "ollama": {
    "base_url": "http://ollama:11434",
    "chat_model": "qwen3:8b",
    "embedding_model": "qwen3-embedding:0.6b",
    "context_size": 8192,
    "max_concurrency": 1,
    "cpu_only": false
  }
}
```

For Python running on the host, use `OLLAMA_BASE_URL=http://localhost:11434`. CPU mode requires `OLLAMA_CPU_ONLY=true`; it is never selected silently.

Existing `companies.json` entries remain valid. Optional fields:

```json
{
  "name": "Company",
  "careers_url": "https://company.example/careers",
  "connector": "auto",
  "enabled": true,
  "allowed_domains": ["company.example", "boards.greenhouse.io"],
  "location": "Remote",
  "region": "Global"
}
```

## Data and offline mode

- state/SQLite/cache/audit: `state/` or the `autopilot_state` volume;
- reports and documents: `output/` or the `autopilot_output` volume;
- models: the `ollama_data` volume;
- configuration/profile: panel-writable bind mounts with atomic writes; the original structured resume is read-only and imported versions stay in local SQLite.

Without internet, exports, filters, analysis, stored-job drafts, MCP, the dashboard, and Ollama still work. Only discovery of new jobs is unavailable.

Backup and restore:

```powershell
.\scripts\backup-local.ps1
.\scripts\restore-local.ps1 -Archive .\backups\arquivo.zip -Force
```

The backup includes host personal files and, when Docker is available, the `autopilot_state` and `autopilot_output` volumes. `ollama_data` weights are not duplicated; they can be downloaded again with `pull-models.ps1`. Restore requires `-Force`, creates a prior backup, and validates internal file paths before restoring volumes.

To delete Docker data, stop the services and, only after a backup, explicitly run `docker compose down --volumes`. Bind-mounted files are not deleted by that command.

## Development and validation

```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m pytest --cov=job_hunt --cov-report=term-missing
.venv\Scripts\python.exe -m build
docker compose config
docker compose build
```

Tests marked `local_model`, `gpu`, and `live_source` are optional and separate from the offline suite.

Documentation: [architecture](docs/LOCAL_ARCHITECTURE.md), [connectors](docs/CONNECTORS.md), [network policy](docs/NETWORK_POLICY.md), [Windows GPU](docs/GPU_SETUP_WINDOWS.md), [troubleshooting](docs/TROUBLESHOOTING_LOCAL.md), and the [web dashboard](docs/WEB_INTERFACE.md).
