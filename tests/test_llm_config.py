import pytest
from pydantic import ValidationError

from job_hunt.llm.config import LLMSettings, ProviderSettings


def test_provider_reads_only_named_environment_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    settings = ProviderSettings(provider="openai", model="gpt-test")
    assert settings.environment_key == "OPENAI_API_KEY"
    assert settings.api_key() == "secret"


def test_local_provider_requires_explicit_base_url():
    with pytest.raises(ValidationError, match="base_url"):
        ProviderSettings(provider="local", model="local")


def test_nested_config_and_openai_model_override(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "override-model")
    settings = LLMSettings.from_application_config(
        {"ai": {"enabled": True, "primary": {"provider": "openai", "model": "old"}}}
    )
    assert settings.primary.model == "override-model"


def test_legacy_provider_config_is_supported():
    settings = LLMSettings.from_application_config(
        {"llm_provider": "openrouter", "openrouter_model": "provider/model"}
    )
    assert settings.enabled
    assert settings.primary.provider == "openrouter"
