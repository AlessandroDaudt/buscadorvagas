"""Structured local Ollama provider."""

from __future__ import annotations

from job_hunt.llm.base import StructuredResponse, StructuredT, TokenUsage
from job_hunt.llm.config import ProviderSettings
from job_hunt.ollama import OllamaClient, OllamaSettings, strip_markdown_fences


class OllamaStructuredProvider:
    name = "ollama"

    def __init__(self, settings: ProviderSettings, *, timeout: float) -> None:
        self.settings = settings
        self.model = settings.model
        self.client = OllamaClient(
            OllamaSettings(
                base_url=settings.base_url,
                chat_model=settings.model,
                context_size=settings.context_size,
                timeout_seconds=timeout,
                keep_alive=settings.keep_alive,
                max_concurrency=settings.max_concurrency,
                cpu_only=settings.cpu_only,
            )
        )

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[StructuredT],
        max_output_tokens: int,
    ) -> StructuredResponse:
        result = self.client.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=max_output_tokens,
            # Large Pydantic schemas can exceed Ollama/llama.cpp grammar repetition
            # limits. JSON mode still constrains the transport, while Pydantic below
            # remains the authoritative structural validator.
            response_format="json",
            model=self.model,
        )
        data = response_model.model_validate_json(strip_markdown_fences(result.content))
        return StructuredResponse(
            data=data,
            provider=self.name,
            model=self.model,
            usage=TokenUsage(
                input_tokens=result.prompt_tokens,
                output_tokens=result.completion_tokens,
                estimated_cost_usd=0,
            ),
            duration_seconds=result.duration_seconds,
        )


def build_provider(settings: ProviderSettings, *, timeout: float) -> OllamaStructuredProvider:
    if settings.provider != "ollama":
        raise ValueError("Only the local Ollama provider is supported")
    return OllamaStructuredProvider(settings, timeout=timeout)
