#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p backups
destination="${1:-backups/autopilot-local-$(date +%Y%m%d-%H%M%S).tar.gz}"
temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT
paths=''
volume_archives=''
for item in .env config.json companies.json config resume state output; do [ ! -e "$item" ] || paths="$paths $item"; done
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  for logical_name in autopilot_state autopilot_output; do
    volume_name="autopilot-jobhunt_${logical_name}"
    if docker volume inspect "$volume_name" >/dev/null 2>&1; then
      docker run --rm --entrypoint python \
        --mount "type=volume,source=$volume_name,target=/source,readonly" \
        --mount "type=bind,source=$temporary,target=/backup" \
        python:3.12-slim -c "import shutil; shutil.make_archive('/backup/$logical_name','gztar','/source')"
      volume_archives="$volume_archives $logical_name.tar.gz"
    fi
  done
fi
[ -n "$paths$volume_archives" ] || { echo 'No local data was found to back up' >&2; exit 2; }
if [ -n "$volume_archives" ]; then
  # shellcheck disable=SC2086
  tar -czf "$destination" $paths -C "$temporary" $volume_archives
else
  # shellcheck disable=SC2086
  tar -czf "$destination" $paths
fi
echo "Backup created: $destination"
