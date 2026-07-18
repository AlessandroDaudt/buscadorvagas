"""Validated provider, retry, token, and cost configuration."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import Field, HttpUrl, model_validator

from job_hunt.domain.models import StrictModel

ProviderName = Literal["openai", "openrouter", "anthropic", "gemini", "local"]

_DEFAULT_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "local": "LOCAL_LLM_API_KEY",
}


class ProviderSettings(StrictModel):
    provider: ProviderName
    model: str = Field(min_length=1, max_length=300)
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,99}$")
    base_url: HttpUrl | None = None
    input_cost_per_million: float = Field(default=0, ge=0, le=10_000)
    output_cost_per_million: float = Field(default=0, ge=0, le=10_000)

    @model_validator(mode="after")
    def local_requires_base_url(self) -> ProviderSettings:
        if self.provider == "local" and self.base_url is None:
            raise ValueError("local provider requires base_url")
        return self

    @property
    def environment_key(self) -> str:
        return self.api_key_env or _DEFAULT_KEY_ENV[self.provider]

    def api_key(self) -> str | None:
        return os.getenv(self.environment_key)


class LLMSettings(StrictModel):
    enabled: bool = False
    primary: ProviderSettings = Field(
        default_factory=lambda: ProviderSettings(provider="openai", model="gpt-5.6-sol")
    )
    fallback: list[ProviderSettings] = Field(default_factory=list, max_length=5)
    consensus_reviewer: ProviderSettings | None = None
    timeout_seconds: float = Field(default=60, ge=1, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)
    backoff_seconds: float = Field(default=1, ge=0, le=60)
    max_output_tokens: int = Field(default=3000, ge=256, le=128_000)
    max_input_characters: int = Field(default=80_000, ge=1000, le=500_000)
    run_cost_limit_usd: float = Field(default=2, ge=0, le=100_000)
    monthly_cost_limit_usd: float = Field(default=25, ge=0, le=1_000_000)
    consensus_divergence_threshold: float = Field(default=8, ge=0, le=100)

    @classmethod
    def from_application_config(cls, config: dict) -> LLMSettings:
        nested = config.get("ai")
        if isinstance(nested, dict):
            data = dict(nested)
        else:
            provider = str(config.get("llm_provider", "openrouter"))
            model_keys = {
                "openai": "openai_model",
                "openrouter": "openrouter_model",
                "anthropic": "anthropic_model",
                "gemini": "gemini_model",
                "local": "local_llm_model",
            }
            data = {
                "enabled": True,
                "primary": {
                    "provider": provider,
                    "model": config.get(model_keys.get(provider, "openrouter_model"), "unknown"),
                },
            }
        model_override = os.getenv("OPENAI_MODEL")
        primary = data.get("primary")
        if model_override and isinstance(primary, dict) and primary.get("provider") == "openai":
            data["primary"] = {**primary, "model": model_override}
        return cls.model_validate(data)
