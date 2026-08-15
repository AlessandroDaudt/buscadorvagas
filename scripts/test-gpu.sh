#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || true
docker compose exec -T ollama sh -c 'ls -l /dev/nvidia*'
docker compose exec -T ollama ollama ps
