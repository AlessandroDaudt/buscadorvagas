"""Native OpenAI and portable structured-output provider adapters."""

from __future__ import annotations

import time
from typing import Any, cast

from openai import OpenAI

from job_hunt.llm.base import StructuredResponse, StructuredT, TokenUsage
from job_hunt.llm.config import ProviderSettings


def _usage_cost(settings: ProviderSettings, input_tokens: int, output_tokens: int) -> float:
    return round(
        input_tokens * settings.input_cost_per_million / 1_000_000
        + output_tokens * settings.output_cost_per_million / 1_000_000,
        6,
    )


class OpenAIResponsesProvider:
    name = "openai"

    def __init__(self, settings: ProviderSettings, *, timeout: float) -> None:
        api_key = settings.api_key()
        if not api_key:
            raise RuntimeError(f"Missing API key in {settings.environment_key}")
        self.settings = settings
        self.model = settings.model
        self.client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[StructuredT],
        max_output_tokens: int,
    ) -> StructuredResponse:
        started = time.monotonic()
        response = self.client.responses.parse(
            model=self.model,
            instructions=system_prompt,
            input=user_prompt,
            text_format=response_model,
            max_output_tokens=max_output_tokens,
            store=False,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise ValueError("OpenAI response did not contain parsed structured output")
        usage = response.usage
        input_tokens = int(usage.input_tokens) if usage else 0
        output_tokens = int(usage.output_tokens) if usage else 0
        return StructuredResponse(
            data=parsed,
            provider=self.name,
            model=self.model,
            usage=TokenUsage(
                input_tokens,
                output_tokens,
                _usage_cost(self.settings, input_tokens, output_tokens),
            ),
            duration_seconds=time.monotonic() - started,
        )


class OpenAICompatibleProvider:
    def __init__(self, settings: ProviderSettings, *, timeout: float) -> None:
        api_key = settings.api_key()
        if not api_key and settings.provider != "local":
            raise RuntimeError(f"Missing API key in {settings.environment_key}")
        self.settings = settings
        self.name = settings.provider
        self.model = settings.model
        base_urls = {
            "openrouter": "https://openrouter.ai/api/v1",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
        }
        base_url = str(settings.base_url) if settings.base_url else base_urls.get(settings.provider)
        self.client = OpenAI(
            api_key=api_key or "local-not-secret",
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
        )

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[StructuredT],
        max_output_tokens: int,
    ) -> StructuredResponse:
        started = time.monotonic()
        schema = response_model.model_json_schema()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=cast(
                "Any",
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            ),
            response_format=cast(
                "Any",
                {
                    "type": "json_schema",
                    "json_schema": {"name": response_model.__name__, "strict": True, "schema": schema},
                },
            ),
            temperature=0,
            max_tokens=max_output_tokens,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Provider returned an empty structured response")
        parsed = response_model.model_validate_json(content)
        raw_usage = response.usage
        input_tokens = int(raw_usage.prompt_tokens) if raw_usage else 0
        output_tokens = int(raw_usage.completion_tokens) if raw_usage else 0
        return StructuredResponse(
            data=parsed,
            provider=self.name,
            model=self.model,
            usage=TokenUsage(
                input_tokens,
                output_tokens,
                _usage_cost(self.settings, input_tokens, output_tokens),
            ),
            duration_seconds=time.monotonic() - started,
        )


class AnthropicStructuredProvider:
    name = "anthropic"

    def __init__(self, settings: ProviderSettings, *, timeout: float) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("Install the optional dependency: pip install '.[claude]'") from exc
        api_key = settings.api_key()
        if not api_key:
            raise RuntimeError(f"Missing API key in {settings.environment_key}")
        self.settings = settings
        self.model = settings.model
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=0)

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[StructuredT],
        max_output_tokens: int,
    ) -> StructuredResponse:
        started = time.monotonic()
        response = self.client.messages.create(
            model=self.model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            max_tokens=max_output_tokens,
            temperature=0,
            tools=[
                {
                    "name": "emit_structured_analysis",
                    "description": "Return the validated job analysis.",
                    "input_schema": response_model.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": "emit_structured_analysis"},
        )
        block = next((item for item in response.content if getattr(item, "type", "") == "tool_use"), None)
        if block is None:
            raise ValueError("Anthropic response did not call the structured output tool")
        payload = getattr(block, "input", None)
        if not isinstance(payload, dict):
            raise ValueError("Anthropic structured output was not an object")
        parsed = response_model.model_validate(cast("Any", payload))
        input_tokens = int(response.usage.input_tokens)
        output_tokens = int(response.usage.output_tokens)
        return StructuredResponse(
            data=parsed,
            provider=self.name,
            model=self.model,
            usage=TokenUsage(
                input_tokens,
                output_tokens,
                _usage_cost(self.settings, input_tokens, output_tokens),
            ),
            duration_seconds=time.monotonic() - started,
        )


def build_provider(settings: ProviderSettings, *, timeout: float):
    if settings.provider == "openai":
        return OpenAIResponsesProvider(settings, timeout=timeout)
    if settings.provider == "anthropic":
        return AnthropicStructuredProvider(settings, timeout=timeout)
    return OpenAICompatibleProvider(settings, timeout=timeout)
