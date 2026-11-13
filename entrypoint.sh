#!/usr/bin/env sh
set -eu

# One-off commands, e.g. `docker compose run --rm bot zammad-setup`
if [ "$#" -gt 0 ]; then
  exec nephthys "$@"
fi

echo "[entrypoint] Initialising"

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  if [ -z "${DATABASE_URL:-}" ]; then
    echo "[entrypoint] DATABASE_URL is not set" >&2
    exit 1
  fi
  echo "[entrypoint] Running migrations"
  piccolo migrations forwards nephthys
else
  echo "[entrypoint] Skipping migrations (RUN_MIGRATIONS=${RUN_MIGRATIONS})"
fi

echo "[entrypoint] Starting Cloudsail Support"
exec nephthys
