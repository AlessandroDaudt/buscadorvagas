import httpx
import pytest

from job_hunt.analysis.models import LLMJobReview
from job_hunt.llm.config import LLMSettings, ProviderSettings
from job_hunt.llm.providers import OllamaStructuredProvider
from job_hunt.ollama import (
    OllamaClient,
    OllamaResponseError,
    OllamaSettings,
    OllamaUnavailableError,
)


def test_native_chat_and_embeddings_are_local_and_structured():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": "{\"ok\":true}"}, "prompt_eval_count": 3, "eval_count": 2})
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    settings = OllamaSettings(base_url="http://localhost:11434", max_retries=0)
    with OllamaClient(settings, transport=httpx.MockTransport(handler)) as client:
        result = client.chat([{"role": "user", "content": "private"}], response_format="json")
        embeddings = client.embeddings("text")
    assert result.content == '{"ok":true}'
    assert embeddings == [[0.1, 0.2]]
    assert {request.url.host for request in requests} == {"localhost"}


def test_invalid_ollama_shape_is_rejected():
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"message": {}}))
    with OllamaClient(OllamaSettings(base_url="http://localhost:11434", max_retries=0), transport=transport) as client:
        with pytest.raises(OllamaResponseError):
            client.chat([{"role": "user", "content": "x"}])


def test_model_inventory_and_vram_status_use_native_endpoints():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}, "ignored"]})
        return httpx.Response(
            200,
            json={"models": [{"name": "qwen3:8b", "size_vram": 1234}, "ignored"]},
        )

    transport = httpx.MockTransport(handler)
    with OllamaClient(
        OllamaSettings(base_url="http://localhost:11434"), transport=transport
    ) as client:
        assert client.list_models() == {"qwen3:8b"}
        assert client.running_models() == [{"name": "qwen3:8b", "size_vram": 1234}]


@pytest.mark.parametrize("method", ["list_models", "running_models"])
def test_invalid_runtime_status_is_reported_as_unavailable(method):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"not-json"))
    with OllamaClient(
        OllamaSettings(base_url="http://localhost:11434"), transport=transport
    ) as client:
        with pytest.raises(OllamaUnavailableError):
            getattr(client, method)()


def test_structured_provider_validates_json(monkeypatch):
    provider = OllamaStructuredProvider(
        ProviderSettings(base_url="http://localhost:11434", model="fixture"), timeout=1
    )
    options = {}

    def chat(*_args, **kwargs):
        options.update(kwargs)
        return type(
            "R",
            (),
            {
                "content": '{"explanation":"safe"}',
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "duration_seconds": 0.1,
            },
        )()

    monkeypatch.setattr(provider.client, "chat", chat)
    response = provider.generate(system_prompt="s", user_prompt="u", response_model=LLMJobReview, max_output_tokens=100)
    assert response.provider == "ollama"
    assert response.data.explanation == "safe"
    assert options["response_format"] == "json"


def test_llm_settings_have_no_fallback_provider():
    settings = LLMSettings.from_application_config({"ollama": {"base_url": "http://localhost:11434"}})
    assert settings.primary.provider == "ollama"
    assert settings.fallback == []
