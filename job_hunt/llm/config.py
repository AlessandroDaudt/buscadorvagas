"""Validated settings for the local Ollama structured-analysis backend."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import Field, model_validator

from job_hunt.domain.models import StrictModel
from job_hunt.local_config import is_local_service_url

ProviderName = Literal["ollama"]


class ProviderSettings(StrictModel):
    provider: ProviderName = "ollama"
    model: str = Field(default="qwen3:8b", min_length=1, max_length=300)
    base_url: str = Field(default="http://ollama:11434", min_length=8, max_length=500)
    context_size: int = Field(default=8192, ge=2048, le=262_144)
    keep_alive: str = Field(default="5m", min_length=1, max_length=30)
    max_concurrency: int = Field(default=1, ge=1, le=4)
    cpu_only: bool = False
    input_cost_per_million: float = Field(default=0, ge=0, le=0)
    output_cost_per_million: float = Field(default=0, ge=0, le=0)

    @model_validator(mode="after")
    def local_endpoint_only(self) -> ProviderSettings:
        if not is_local_service_url(self.base_url):
            raise ValueError("Ollama base_url must be localhost or an approved Compose service")
        return self


class LLMSettings(StrictModel):
    enabled: bool = True
    primary: ProviderSettings = Field(default_factory=ProviderSettings)
    fallback: list[ProviderSettings] = Field(default_factory=list, max_length=0)
    consensus_reviewer: ProviderSettings | None = None
    timeout_seconds: float = Field(default=180, ge=1, le=900)
    max_retries: int = Field(default=1, ge=0, le=3)
    backoff_seconds: float = Field(default=0.5, ge=0, le=10)
    max_output_tokens: int = Field(default=3000, ge=256, le=32_768)
    max_input_characters: int = Field(default=60_000, ge=1000, le=120_000)
    run_cost_limit_usd: float = Field(default=0, ge=0, le=0)
    monthly_cost_limit_usd: float = Field(default=0, ge=0, le=0)
    consensus_divergence_threshold: float = Field(default=8, ge=0, le=100)

    @classmethod
    def from_application_config(cls, config: dict) -> LLMSettings:
        ollama = dict(config.get("ollama") or {})
        base_url = os.getenv("OLLAMA_BASE_URL") or ollama.get("base_url", "http://ollama:11434")
        model = os.getenv("OLLAMA_CHAT_MODEL") or ollama.get("chat_model", "qwen3:8b")
        nested = config.get("ai")
        enabled = bool(nested.get("enabled", True)) if isinstance(nested, dict) else True
        data = {
            "enabled": enabled,
            "primary": {
                "provider": "ollama",
                "model": model,
                "base_url": base_url,
                "context_size": ollama.get("context_size", 8192),
                "keep_alive": ollama.get("keep_alive", "5m"),
                "max_concurrency": ollama.get("max_concurrency", 1),
                "cpu_only": ollama.get("cpu_only", False),
            },
            "timeout_seconds": ollama.get(
                "timeout_seconds",
                nested.get("timeout_seconds", 180) if isinstance(nested, dict) else 180,
            ),
            "max_retries": ollama.get(
                "max_retries",
                nested.get("max_retries", 1) if isinstance(nested, dict) else 1,
            ),
            "backoff_seconds": nested.get("backoff_seconds", 0.5)
            if isinstance(nested, dict)
            else 0.5,
            "max_output_tokens": nested.get("max_output_tokens", 3000)
            if isinstance(nested, dict)
            else 3000,
            "max_input_characters": nested.get("max_input_characters", 60_000)
            if isinstance(nested, dict)
            else 60_000,
        }
        return cls.model_validate(data)
