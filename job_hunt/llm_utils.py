"""Compatibility helpers backed exclusively by the local Ollama service."""

from __future__ import annotations

from typing import Any

from job_hunt.local_config import validate_local_config
from job_hunt.ollama import OllamaClient, OllamaSettings


def chat_with_llm(
    config: dict,
    messages: list[dict],
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    """Call the configured local Ollama chat model; never fall back to a cloud provider."""
    validate_local_config(config)
    settings = OllamaSettings.from_config(config)
    normalized: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = str(message.get("content", ""))
        if role not in {"system", "user", "assistant"}:
            raise ValueError(f"Unsupported chat role: {role}")
        normalized.append({"role": role, "content": content})
    with OllamaClient(settings) as client:
        return client.chat(
            normalized,
            temperature=temperature,
            max_tokens=max_tokens,
        ).content


def embed_texts(config: dict[str, Any], texts: list[str] | str) -> list[list[float]]:
    """Generate local embeddings. Embeddings are optional for the main scan pipeline."""
    validate_local_config(config)
    with OllamaClient(OllamaSettings.from_config(config)) as client:
        return client.embeddings(texts)
