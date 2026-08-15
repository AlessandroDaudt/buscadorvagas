"""Small native Ollama HTTP client with bounded local retries and concurrency."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field

from job_hunt.local_config import LocalConfigurationError, is_local_service_url, parse_bool
from job_hunt.log import get_logger
from job_hunt.metrics import metrics

logger = get_logger("autopilot.ollama")


class OllamaUnavailableError(RuntimeError):
    pass


class OllamaResponseError(RuntimeError):
    pass


class OllamaSettings(BaseModel):
    base_url: str = "http://ollama:11434"
    chat_model: str = Field(default="qwen3:8b", min_length=1, max_length=200)
    embedding_model: str = Field(default="qwen3-embedding:0.6b", min_length=1, max_length=200)
    context_size: int = Field(default=8192, ge=2048, le=262_144)
    timeout_seconds: float = Field(default=180, ge=1, le=900)
    keep_alive: str = Field(default="5m", min_length=1, max_length=30)
    max_concurrency: int = Field(default=1, ge=1, le=4)
    max_retries: int = Field(default=1, ge=0, le=3)
    cpu_only: bool = False

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> OllamaSettings:
        raw = dict(config.get("ollama") or {})
        import os

        overrides = {
            "base_url": os.getenv("OLLAMA_BASE_URL"),
            "chat_model": os.getenv("OLLAMA_CHAT_MODEL"),
            "embedding_model": os.getenv("OLLAMA_EMBEDDING_MODEL"),
        }
        raw.update({key: value for key, value in overrides.items() if value})
        if os.getenv("OLLAMA_CPU_ONLY") is not None:
            raw["cpu_only"] = parse_bool(os.getenv("OLLAMA_CPU_ONLY"))
        settings = cls.model_validate(raw)
        if not is_local_service_url(settings.base_url, config=config):
            raise LocalConfigurationError(
                "Ollama must use localhost or an approved Compose service"
            )
        settings.base_url = settings.base_url.rstrip("/")
        return settings


@dataclass(frozen=True)
class OllamaResult:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_seconds: float = 0


_semaphore_lock = threading.Lock()
_semaphores: dict[tuple[str, int], threading.BoundedSemaphore] = {}


def _semaphore(base_url: str, maximum: int) -> threading.BoundedSemaphore:
    key = (base_url, maximum)
    with _semaphore_lock:
        return _semaphores.setdefault(key, threading.BoundedSemaphore(maximum))


def strip_markdown_fences(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


class OllamaClient:
    def __init__(
        self,
        settings: OllamaSettings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._client = httpx.Client(
            base_url=settings.base_url,
            timeout=httpx.Timeout(settings.timeout_seconds),
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "autopilot-jobhunt/local"},
        )

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        transient = (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)
        with _semaphore(self.settings.base_url, self.settings.max_concurrency):
            for attempt in range(self.settings.max_retries + 1):
                try:
                    response = self._client.post(path, json=payload)
                    if response.status_code >= 500 and attempt < self.settings.max_retries:
                        time.sleep(0.5 * (2**attempt))
                        continue
                    response.raise_for_status()
                    data = response.json()
                    if not isinstance(data, dict):
                        raise OllamaResponseError("Ollama returned a non-object response")
                    return data
                except transient as exc:
                    if attempt < self.settings.max_retries:
                        time.sleep(0.5 * (2**attempt))
                        continue
                    raise OllamaUnavailableError(
                        f"Ollama is unavailable at {self.settings.base_url}. "
                        "Start it with 'docker compose up -d ollama'."
                    ) from exc
                except (httpx.HTTPStatusError, json.JSONDecodeError) as exc:
                    raise OllamaResponseError(
                        f"Ollama returned an invalid response ({type(exc).__name__})"
                    ) from exc
        raise OllamaUnavailableError("Ollama request failed")

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | str | None = None,
        model: str | None = None,
    ) -> OllamaResult:
        started = time.monotonic()
        payload: dict[str, Any] = {
            "model": model or self.settings.chat_model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": self.settings.keep_alive,
            "options": {
                "temperature": temperature,
                "num_ctx": self.settings.context_size,
                "num_predict": max_tokens,
                "num_gpu": 0 if self.settings.cpu_only else -1,
            },
        }
        if response_format is not None:
            payload["format"] = response_format
        data = self._post("/api/chat", payload)
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise OllamaResponseError("Ollama response is missing message.content")
        duration = time.monotonic() - started
        prompt_tokens = int(data.get("prompt_eval_count") or 0)
        completion_tokens = int(data.get("eval_count") or 0)
        metrics.increment("ollama_prompt_tokens_total", prompt_tokens)
        metrics.increment("ollama_completion_tokens_total", completion_tokens)
        metrics.observe("ollama_inference_duration", duration)
        logger.info(
            "Local Ollama inference completed via %s in %.2fs",
            model or self.settings.chat_model,
            duration,
        )
        return OllamaResult(content, prompt_tokens, completion_tokens, duration)

    def embeddings(self, texts: list[str] | str) -> list[list[float]]:
        payload = {
            "model": self.settings.embedding_model,
            "input": texts,
            "keep_alive": self.settings.keep_alive,
            "options": {"num_gpu": 0 if self.settings.cpu_only else -1},
        }
        data = self._post("/api/embed", payload)
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or not all(
            isinstance(item, list) for item in embeddings
        ):
            raise OllamaResponseError("Ollama response is missing embeddings")
        return [[float(value) for value in vector] for vector in embeddings]

    def list_models(self) -> set[str]:
        try:
            response = self._client.get("/api/tags")
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise OllamaUnavailableError(
                f"Ollama is unavailable at {self.settings.base_url}"
            ) from exc
        models = data.get("models", []) if isinstance(data, dict) else []
        return {
            str(item.get("name")) for item in models if isinstance(item, dict) and item.get("name")
        }

    def running_models(self) -> list[dict[str, Any]]:
        """Return Ollama's in-memory models, including reported VRAM use."""
        try:
            response = self._client.get("/api/ps")
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise OllamaUnavailableError(
                f"Ollama runtime status is unavailable at {self.settings.base_url}"
            ) from exc
        models = data.get("models", []) if isinstance(data, dict) else []
        return [dict(item) for item in models if isinstance(item, dict)]

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OllamaClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
