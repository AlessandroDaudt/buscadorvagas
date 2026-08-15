#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
docker compose up -d
docker compose ps
