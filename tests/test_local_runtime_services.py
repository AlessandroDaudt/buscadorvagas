from pathlib import Path
from types import SimpleNamespace

from job_hunt import doctor, llm_utils, notifier
from job_hunt.llm.base import StructuredResponse, TokenUsage
from job_hunt.llm.config import LLMSettings, ProviderSettings
from job_hunt.llm.router import LLMRouter


def local_config():
    return {
        "local_only": True,
        "llm_provider": "ollama",
        "ollama": {
            "base_url": "http://localhost:11434",
            "chat_model": "qwen3:8b",
            "embedding_model": "qwen3-embedding:0.6b",
        },
    }


class FakeOllama:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def chat(self, *_args, **_kwargs):
        return SimpleNamespace(
            content="LOCAL_OK", duration_seconds=0.01, prompt_tokens=1, completion_tokens=1
        )

    def embeddings(self, _texts):
        return [[0.1]]

    def list_models(self):
        return {"qwen3:8b", "qwen3-embedding:0.6b"}

    def running_models(self):
        return [{"name": "qwen3:8b", "size_vram": 6 * 1024**3}]


def test_llm_compatibility_helpers_use_ollama(monkeypatch):
    monkeypatch.setattr(llm_utils, "OllamaClient", FakeOllama)
    assert llm_utils.chat_with_llm(local_config(), [{"role": "user", "content": "x"}]) == "LOCAL_OK"
    assert llm_utils.embed_texts(local_config(), "x") == [[0.1]]


def test_router_success_and_retry(monkeypatch):
    from pydantic import BaseModel

    class ResponseModel(BaseModel):
        value: str

    attempts = []

    class Provider:
        def generate(self, **_kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("transient")
            return StructuredResponse(
                ResponseModel(value="ok"), "ollama", "fixture", TokenUsage(), 0.1
            )

    monkeypatch.setattr("job_hunt.llm.router.build_provider", lambda *_args, **_kwargs: Provider())
    settings = LLMSettings(
        primary=ProviderSettings(model="fixture", base_url="http://localhost:11434"),
        max_retries=1,
        backoff_seconds=0,
    )
    response = LLMRouter(settings, sleep=lambda _seconds: None).generate(
        system_prompt="s", user_prompt="u", response_model=ResponseModel
    )
    assert response.data.value == "ok"
    assert len(attempts) == 2


def test_doctor_covers_local_checks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for directory in ("config", "resume", "state", "output"):
        Path(directory).mkdir()
    for path in (
        "config.json",
        "companies.json",
        "config/candidate_profile.json",
        "config/search_preferences.json",
        "resume/master_resume.json",
    ):
        Path(path).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(doctor, "OllamaClient", FakeOllama)
    monkeypatch.setattr(
        doctor, "_command", lambda args, timeout=10: SimpleNamespace(returncode=0, stdout="fixture")
    )
    checks = doctor.collect_checks(local_config())
    assert any(item.name == "local inference" and item.status == "PASS" for item in checks)
    assert any(item.name == "Ollama GPU" and item.status == "PASS" for item in checks)
    assert any(item.name == "local fixture" and item.status == "PASS" for item in checks)
    assert doctor.run_doctor(local_config()) == 0


def test_local_notifier_is_best_effort(monkeypatch):
    monkeypatch.setattr(notifier.os, "name", "posix")
    assert notifier.send_windows_notification("title", "message") is False
    monkeypatch.setattr(notifier.os, "name", "nt")
    monkeypatch.setattr(
        notifier.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0)
    )
    assert notifier.send_windows_notification("title", "message") is True
