from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from job_hunt.llm.config import ProviderSettings
from job_hunt.llm.providers import (
    AnthropicStructuredProvider,
    OpenAICompatibleProvider,
    OpenAIResponsesProvider,
    build_provider,
)


class Result(BaseModel):
    answer: str


def test_openai_responses_provider_uses_parsed_schema_output():
    captured = {}

    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_parsed=Result(answer="ok"),
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )

    provider = object.__new__(OpenAIResponsesProvider)
    provider.settings = ProviderSettings(
        provider="openai",
        model="fixture",
        input_cost_per_million=1,
        output_cost_per_million=2,
    )
    provider.model = "fixture"
    provider.client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    response = provider.generate(
        system_prompt="system",
        user_prompt="user",
        response_model=Result,
        max_output_tokens=500,
    )
    assert response.data == Result(answer="ok")
    assert captured["text_format"] is Result
    assert captured["store"] is False
    assert response.usage.estimated_cost_usd > 0


def test_openai_responses_provider_rejects_missing_parsed_output():
    provider = object.__new__(OpenAIResponsesProvider)
    provider.settings = ProviderSettings(provider="openai", model="fixture")
    provider.model = "fixture"
    provider.client = SimpleNamespace(
        responses=SimpleNamespace(
            parse=lambda **_kwargs: SimpleNamespace(output_parsed=None, usage=None)
        )
    )
    with pytest.raises(ValueError, match="parsed"):
        provider.generate(
            system_prompt="s", user_prompt="u", response_model=Result, max_output_tokens=500
        )


def test_openai_compatible_provider_sends_strict_json_schema():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"ok"}'))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
        )

    provider = object.__new__(OpenAICompatibleProvider)
    provider.settings = ProviderSettings(provider="openrouter", model="fixture")
    provider.name = "openrouter"
    provider.model = "fixture"
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    response = provider.generate(
        system_prompt="s", user_prompt="u", response_model=Result, max_output_tokens=500
    )
    assert response.data == Result(answer="ok")
    assert captured["response_format"]["json_schema"]["strict"] is True


def test_compatible_provider_rejects_empty_or_invalid_output():
    provider = object.__new__(OpenAICompatibleProvider)
    provider.settings = ProviderSettings(provider="gemini", model="fixture")
    provider.name = "gemini"
    provider.model = "fixture"
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **_kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=""))], usage=None
                )
            )
        )
    )
    with pytest.raises(ValueError, match="empty"):
        provider.generate(
            system_prompt="s", user_prompt="u", response_model=Result, max_output_tokens=500
        )


def test_anthropic_provider_validates_forced_tool_payload():
    provider = object.__new__(AnthropicStructuredProvider)
    provider.settings = ProviderSettings(provider="anthropic", model="fixture")
    provider.model = "fixture"
    provider.client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(
                content=[SimpleNamespace(type="tool_use", input={"answer": "ok"})],
                usage=SimpleNamespace(input_tokens=9, output_tokens=3),
            )
        )
    )
    response = provider.generate(
        system_prompt="s", user_prompt="u", response_model=Result, max_output_tokens=500
    )
    assert response.data == Result(answer="ok")


def test_provider_factory_routes_supported_types(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    assert isinstance(
        build_provider(ProviderSettings(provider="openai", model="fixture"), timeout=1),
        OpenAIResponsesProvider,
    )
    assert isinstance(
        build_provider(ProviderSettings(provider="openrouter", model="fixture"), timeout=1),
        OpenAICompatibleProvider,
    )
