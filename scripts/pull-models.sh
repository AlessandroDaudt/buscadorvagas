#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
docker compose up -d ollama
docker compose exec -T ollama ollama pull "${OLLAMA_CHAT_MODEL:-qwen3:8b}"
docker compose exec -T ollama ollama pull "${OLLAMA_EMBEDDING_MODEL:-qwen3-embedding:0.6b}"
docker compose exec -T ollama ollama list
