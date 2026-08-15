"""Local-only installation diagnostics without exposing personal data."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from job_hunt.http_client import SafeHttpClient
from job_hunt.local_config import LocalConfigurationError, local_only_enabled, validate_local_config
from job_hunt.ollama import (
    OllamaClient,
    OllamaResponseError,
    OllamaSettings,
    OllamaUnavailableError,
)


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


def _command(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _json_checks() -> list[Check]:
    checks: list[Check] = []
    for path in (
        Path("config.json"),
        Path("companies.json"),
        Path("config/candidate_profile.json"),
        Path("config/search_preferences.json"),
        Path("resume/master_resume.json"),
        Path("state/seen_jobs.json"),
        Path("state/last_scan.json"),
        Path("state/job_history.json"),
    ):
        if not path.exists():
            required = path.parts[0] in {"config", "resume"} or path.name in {
                "config.json",
                "companies.json",
            }
            checks.append(Check("FAIL" if required else "SKIP", f"JSON {path}", "missing"))
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
            checks.append(Check("PASS", f"JSON {path}", "valid UTF-8 JSON"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            checks.append(Check("FAIL", f"JSON {path}", "invalid or unreadable"))
    return checks


def collect_checks(config: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    try:
        validate_local_config(config)
        checks.append(Check("PASS", "configuration", "central local-only policy accepted"))
    except LocalConfigurationError as exc:
        checks.append(Check("FAIL", "configuration", str(exc).splitlines()[0]))
    checks.append(
        Check(
            "PASS" if local_only_enabled(config) else "FAIL",
            "LOCAL_ONLY",
            "enabled" if local_only_enabled(config) else "disabled",
        )
    )

    for directory in (Path("state"), Path("output")):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".doctor-write-test"
            probe.write_text("local", encoding="utf-8")
            probe.unlink()
            checks.append(Check("PASS", f"write {directory}", "writable"))
        except OSError:
            checks.append(Check("FAIL", f"write {directory}", "not writable"))
    checks.extend(_json_checks())

    required = ("pydantic", "httpx", "sqlalchemy", "dotenv")
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    checks.append(
        Check(
            "FAIL" if missing else "PASS",
            "Python dependencies",
            ", ".join(missing) if missing else "available",
        )
    )
    checks.append(
        Check(
            "PASS" if importlib.util.find_spec("playwright") else "SKIP",
            "Playwright",
            "installed"
            if importlib.util.find_spec("playwright")
            else "not installed; static/API connectors remain available",
        )
    )

    docker = _command(["docker", "version", "--format", "{{.Server.Version}}"])
    checks.append(
        Check(
            "PASS" if docker and docker.returncode == 0 else "WARN",
            "Docker",
            docker.stdout.strip()
            if docker and docker.returncode == 0
            else "daemon unavailable or not on PATH",
        )
    )
    gpu = _command(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    checks.append(
        Check(
            "PASS" if gpu and gpu.returncode == 0 else "WARN",
            "GPU",
            gpu.stdout.strip()
            if gpu and gpu.returncode == 0
            else "nvidia-smi unavailable in this environment",
        )
    )

    try:
        settings = OllamaSettings.from_config(config)
        with OllamaClient(settings) as ollama_client:
            models = ollama_client.list_models()
            checks.append(Check("PASS", "Ollama", f"reachable at {settings.base_url}"))
            for label, model in (
                ("chat model", settings.chat_model),
                ("embedding model", settings.embedding_model),
            ):
                present = model in models or f"{model}:latest" in models
                checks.append(
                    Check(
                        "PASS" if present else "FAIL",
                        label,
                        f"{model} {'installed' if present else 'missing'}",
                    )
                )
            if settings.chat_model in models or f"{settings.chat_model}:latest" in models:
                result = ollama_client.chat(
                    [{"role": "user", "content": "Reply only with: LOCAL_OK"}],
                    max_tokens=16,
                    temperature=0,
                )
                checks.append(
                    Check(
                        "PASS" if result.content.strip() else "FAIL",
                        "local inference",
                        f"completed in {result.duration_seconds:.2f}s",
                    )
                )
                if settings.cpu_only:
                    checks.append(
                        Check("SKIP", "Ollama GPU", "CPU-only mode was explicitly enabled")
                    )
                else:
                    vram_bytes = sum(
                        int(model.get("size_vram") or 0) for model in ollama_client.running_models()
                    )
                    checks.append(
                        Check(
                            "PASS" if vram_bytes > 0 else "FAIL",
                            "Ollama GPU",
                            f"{vram_bytes / (1024**3):.2f} GiB VRAM active"
                            if vram_bytes > 0
                            else "inference completed but Ollama reported no VRAM use",
                        )
                    )
    except (OllamaUnavailableError, OllamaResponseError, ValueError) as exc:
        checks.append(Check("FAIL", "Ollama", f"unavailable ({type(exc).__name__})"))

    with tempfile.TemporaryDirectory(prefix="autopilot-doctor-") as temporary:

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"fixture": True}, headers={"content-type": "application/json"}
            )

        try:
            with SafeHttpClient(
                connector="doctor_fixture",
                resolver=lambda _host: ["93.184.216.34"],
                transport=httpx.MockTransport(handler),
                rate_limit_seconds=0,
                cache_directory=Path(temporary) / "cache",
                audit_path=Path(temporary) / "audit.jsonl",
            ) as http_client:
                passed = http_client.get_json(
                    "https://fixture.invalid/jobs", allowed_hosts={"fixture.invalid"}
                ) == {"fixture": True}
            checks.append(
                Check(
                    "PASS" if passed else "FAIL",
                    "local fixture",
                    "HTTP policy stack exercised without internet",
                )
            )
        except Exception as exc:
            checks.append(Check("FAIL", "local fixture", type(exc).__name__))
    checks.append(
        Check(
            "SKIP",
            "live public source",
            "disabled by default; normal diagnostics never require internet",
        )
    )
    return checks


def run_doctor(config: dict[str, Any]) -> int:
    checks = collect_checks(config)
    for check in checks:
        print(f"{check.status:4}  {check.name}: {check.detail}")
    failures = sum(check.status == "FAIL" for check in checks)
    warnings = sum(check.status == "WARN" for check in checks)
    print(
        f"\nSummary: {failures} FAIL, {warnings} WARN, {len(checks) - failures - warnings} PASS/SKIP"
    )
    return 1 if failures else 0
