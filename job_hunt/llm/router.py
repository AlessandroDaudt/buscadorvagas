"""Fallback, retries, exponential backoff, and per-run cost enforcement."""

from __future__ import annotations

import time
from collections.abc import Callable

from job_hunt.llm.base import StructuredResponse, StructuredT
from job_hunt.llm.config import LLMSettings, ProviderSettings
from job_hunt.llm.providers import build_provider
from job_hunt.log import get_logger
from job_hunt.metrics import metrics

logger = get_logger()


class LLMBudgetExceeded(RuntimeError):
    pass


class LLMRouter:
    def __init__(
        self,
        settings: LLMSettings,
        *,
        monthly_cost: Callable[[], float] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._monthly_cost = monthly_cost or (lambda: 0.0)
        self._sleep = sleep
        self.run_cost_usd = 0.0

    def _check_budget(self, prospective_cost: float = 0) -> None:
        # Ollama is local and has no per-token bill. Zero means that cost budgets
        # are intentionally disabled, not that every local request is rejected.
        if self.settings.run_cost_limit_usd and (
            self.run_cost_usd + prospective_cost >= self.settings.run_cost_limit_usd
        ):
            raise LLMBudgetExceeded("Per-run LLM cost limit reached")
        if self.settings.monthly_cost_limit_usd and (
            self._monthly_cost() + self.run_cost_usd + prospective_cost
            >= self.settings.monthly_cost_limit_usd
        ):
            raise LLMBudgetExceeded("Monthly LLM cost limit reached")

    def _maximum_request_cost(self, settings: ProviderSettings, input_characters: int) -> float:
        estimated_input_tokens = min(input_characters, self.settings.max_input_characters) / 4
        return (
            estimated_input_tokens * settings.input_cost_per_million / 1_000_000
            + self.settings.max_output_tokens * settings.output_cost_per_million / 1_000_000
        )

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[StructuredT],
        provider_settings: ProviderSettings | None = None,
    ) -> StructuredResponse:
        if not self.settings.enabled:
            raise RuntimeError("LLM analysis is disabled")
        candidates = (
            [provider_settings]
            if provider_settings is not None
            else [self.settings.primary, *self.settings.fallback]
        )
        failures: list[str] = []
        for candidate in candidates:
            for attempt in range(self.settings.max_retries + 1):
                self._check_budget(self._maximum_request_cost(candidate, len(user_prompt)))
                try:
                    provider = build_provider(candidate, timeout=self.settings.timeout_seconds)
                    response = provider.generate(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt[: self.settings.max_input_characters],
                        response_model=response_model,
                        max_output_tokens=self.settings.max_output_tokens,
                    )
                    self.run_cost_usd += response.usage.estimated_cost_usd
                    metrics.increment("llm_input_tokens_total", response.usage.input_tokens)
                    metrics.increment("llm_output_tokens_total", response.usage.output_tokens)
                    metrics.increment("llm_estimated_cost_usd", response.usage.estimated_cost_usd)
                    metrics.observe("llm_request_duration", response.duration_seconds)
                    logger.info(
                        "Structured LLM analysis completed via %s/%s in %.2fs",
                        response.provider,
                        response.model,
                        response.duration_seconds,
                    )
                    return response
                except LLMBudgetExceeded:
                    raise
                except Exception as exc:
                    metrics.increment("llm_errors_total")
                    failures.append(f"{candidate.provider}/{candidate.model}: {type(exc).__name__}")
                    logger.warning(
                        "Structured LLM attempt failed via %s/%s (%s)",
                        candidate.provider,
                        candidate.model,
                        type(exc).__name__,
                    )
                    if attempt < self.settings.max_retries:
                        self._sleep(self.settings.backoff_seconds * (2**attempt))
        raise RuntimeError("All configured LLM providers failed: " + "; ".join(failures))
