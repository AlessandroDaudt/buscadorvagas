import pytest

from job_hunt.local_config import (
    LocalConfigurationError,
    is_local_service_url,
    validate_local_config,
)


def local_config():
    return {
        "local_only": True,
        "llm_provider": "ollama",
        "ollama": {"base_url": "http://ollama:11434"},
    }


def test_local_only_is_default_and_accepts_ollama():
    assert validate_local_config(local_config(), environ={})


@pytest.mark.parametrize("key", ["OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "TINYFISH_API_KEY", "TELEGRAM_TOKEN"])
def test_local_only_rejects_external_credentials(key):
    with pytest.raises(LocalConfigurationError, match=key):
        validate_local_config(local_config(), environ={key: "real-secret"})


def test_placeholders_do_not_break_local_migration():
    validate_local_config(local_config(), environ={"OPENROUTER_API_KEY": "your_openrouter_api_key_here"})


def test_external_and_arbitrary_compatible_endpoints_are_rejected():
    config = local_config()
    config["ollama"] = {"base_url": "https://models.example.com"}
    with pytest.raises(LocalConfigurationError, match="not an approved local endpoint"):
        validate_local_config(config, environ={})
    assert not is_local_service_url("https://models.example.com")


def test_cloud_provider_is_rejected_even_without_key():
    config = local_config()
    config["llm_provider"] = "openrouter"
    with pytest.raises(LocalConfigurationError, match="external LLM provider"):
        validate_local_config(config, environ={})
