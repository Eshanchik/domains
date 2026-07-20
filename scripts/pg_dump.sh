#!/usr/bin/env sh
# Optional manual database dump (SPEC NFR-6). Primary backups are DO snapshots.
# Usage: bash scripts/pg_dump.sh   (run from the repo root, stack up)
set -eu

ts="$(date +%Y%m%d-%H%M)"
out="dump-${ts}.sql.gz"

# Dump via the compose postgres service.
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-domainguard}" \
  "${POSTGRES_DB:-domainguard}" | gzip > "$out"

echo "wrote $out"
