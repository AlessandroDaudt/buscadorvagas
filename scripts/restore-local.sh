#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
[ "${2:-}" = '--force' ] || { echo 'Usage: restore-local.sh ARCHIVE --force' >&2; exit 2; }
[ -f "$1" ] || { echo 'Archive not found' >&2; exit 2; }
archive="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
"$PWD/scripts/backup-local.sh"
temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT
tar -xzf "$archive" -C "$temporary"
for item in .env config.json companies.json config resume state output; do
  [ -e "$temporary/$item" ] || continue
  if [ -d "$temporary/$item" ]; then
    mkdir -p "$PWD/$item"
    cp -R "$temporary/$item/." "$PWD/$item/"
  else
    cp "$temporary/$item" "$PWD/$item"
  fi
done
volumes_stopped=false
for volume_archive in "$temporary"/autopilot_*.tar.gz; do
  [ -f "$volume_archive" ] || continue
  logical_name="$(basename "$volume_archive" .tar.gz)"
  case "$logical_name" in autopilot_state|autopilot_output) ;; *) continue ;; esac
  if [ "$volumes_stopped" = false ]; then
    docker compose stop autopilot scheduler
    volumes_stopped=true
  fi
  volume_name="autopilot-jobhunt_${logical_name}"
  docker volume create "$volume_name" >/dev/null
  python_code="import pathlib,shutil,tarfile; r=pathlib.Path('/target').resolve(); t=tarfile.open('/backup/$(basename "$volume_archive")','r:gz'); m=t.getmembers(); assert all((r/x.name).resolve()==r or r in (r/x.name).resolve().parents for x in m), 'unsafe archive path'; [(shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()) for p in r.iterdir()]; t.extractall(r,members=m,filter='data'); t.close()"
  docker run --rm --entrypoint python \
    --mount "type=volume,source=$volume_name,target=/target" \
    --mount "type=bind,source=$temporary,target=/backup,readonly" \
    python:3.12-slim -c "$python_code"
done
echo 'Restore complete; a pre-restore backup was created.'
