"""Central local-only policy and configuration validation."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit


class LocalConfigurationError(ValueError):
    """Raised when a configuration could send private data to an external service."""


FORBIDDEN_PROVIDERS = {
    "tinyfish",
    "openrouter",
    "openai",
    "anthropic",
    "gemini",
    "claude_cli",
    "claude-cli",
}

FORBIDDEN_ENVIRONMENT_KEYS = {
    "TINYFISH_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "TELEGRAM_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_CALLBACK_SECRET",
    "TEXTMEBOT_API_KEY",
    "WHATSAPP_API_KEY",
    "WEBHOOK_URL",
    "NOTIFICATION_WEBHOOK_URL",
}

_PLACEHOLDER_MARKERS = (
    "your_",
    "replace_",
    "example",
    "changeme",
    "placeholder",
)


def parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise LocalConfigurationError(f"Invalid boolean value: {value!r}")


def is_placeholder(value: Any) -> bool:
    text = str(value or "").strip().strip('"').strip("'").casefold()
    return not text or any(marker in text for marker in _PLACEHOLDER_MARKERS)


def local_only_enabled(
    config: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    env = environ if environ is not None else os.environ
    if "LOCAL_ONLY" in env:
        return parse_bool(env.get("LOCAL_ONLY"), default=True)
    return parse_bool((config or {}).get("local_only"), default=True)


def _local_service_names(config: Mapping[str, Any], environ: Mapping[str, str]) -> set[str]:
    names = {"ollama", "autopilot", "scheduler"}
    configured = config.get("local_service_names", [])
    if isinstance(configured, list):
        names.update(str(item).strip().casefold() for item in configured if str(item).strip())
    names.update(
        item.strip().casefold()
        for item in environ.get("LOCAL_SERVICE_NAMES", "").split(",")
        if item.strip()
    )
    return names


def is_local_service_url(
    value: str,
    *,
    config: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".localhost"):
        return True
    env = environ if environ is not None else os.environ
    return hostname in _local_service_names(config or {}, env)


def _configured_providers(config: Mapping[str, Any]) -> set[str]:
    providers: set[str] = set()
    legacy = config.get("llm_provider")
    if legacy:
        providers.add(str(legacy).casefold())
    ai = config.get("ai")
    if isinstance(ai, Mapping):
        candidates: list[Any] = [ai.get("primary"), ai.get("consensus_reviewer")]
        fallback = ai.get("fallback")
        if isinstance(fallback, list):
            candidates.extend(fallback)
        for candidate in candidates:
            if isinstance(candidate, Mapping) and candidate.get("provider"):
                providers.add(str(candidate["provider"]).casefold())
    return providers


def validate_local_config(
    config: dict[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate all local-only restrictions in one place and return *config* unchanged."""
    env = environ if environ is not None else os.environ
    if not local_only_enabled(config, env):
        return config

    violations: list[str] = []
    for key in sorted(FORBIDDEN_ENVIRONMENT_KEYS):
        if key in env and not is_placeholder(env.get(key)):
            violations.append(f"{key} is configured")

    secret_keys = {
        "tinyfish_api_key",
        "openrouter_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "gemini_api_key",
    }
    for key in sorted(secret_keys):
        if key in config and not is_placeholder(config.get(key)):
            violations.append(f"{key} is configured in config.json")

    providers = _configured_providers(config)
    forbidden = sorted(providers & FORBIDDEN_PROVIDERS)
    if forbidden:
        violations.append("external LLM provider(s): " + ", ".join(forbidden))
    unsupported = sorted(providers - FORBIDDEN_PROVIDERS - {"ollama"})
    if unsupported:
        violations.append("unsupported LLM provider(s): " + ", ".join(unsupported))

    telegram = config.get("telegram")
    if isinstance(telegram, Mapping) and (
        parse_bool(telegram.get("enabled"), default=False)
        or any(not is_placeholder(telegram.get(key)) for key in ("token", "chat_id"))
    ):
        violations.append("external Telegram notifications are enabled")

    notifications = config.get("notifications")
    if isinstance(notifications, Mapping):
        for key in ("webhook_url", "telegram", "whatsapp", "textmebot"):
            if key in notifications and notifications.get(key):
                violations.append(f"external notification setting: {key}")

    ollama = config.get("ollama")
    base_url = "http://ollama:11434"
    if isinstance(ollama, Mapping):
        base_url = str(ollama.get("base_url") or base_url)
    base_url = str(env.get("OLLAMA_BASE_URL") or base_url)
    if not is_local_service_url(base_url, config=config, environ=env):
        violations.append(f"OLLAMA_BASE_URL is not an approved local endpoint: {base_url}")

    if violations:
        details = "\n- ".join(violations)
        raise LocalConfigurationError(
            "LOCAL_ONLY is enabled, but prohibited configuration was found:\n"
            f"- {details}\n"
            "External AI, scraping, notification, embedding and webhook services are not allowed."
        )
    return config

