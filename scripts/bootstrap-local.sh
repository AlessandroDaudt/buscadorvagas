#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
command -v docker >/dev/null
docker compose version
docker info >/dev/null
[ -f .env ] || cp .env.example .env
[ -f config.json ] || cp config.example.json config.json
mkdir -p state output resume
docker compose config --quiet
docker compose build
docker compose up -d ollama
"$PWD/scripts/pull-models.sh"
docker compose exec -T ollama ollama run "${OLLAMA_CHAT_MODEL:-qwen3:8b}" 'Reply only with LOCAL_OK'
docker compose up -d autopilot scheduler
"$PWD/scripts/test-gpu.sh"
"$PWD/scripts/doctor.sh"
