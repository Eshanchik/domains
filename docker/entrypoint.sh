#!/usr/bin/env sh
# Container entrypoint. Selects the process to run based on the first argument so a
# single image backs the api / worker / scheduler / migrate services.
set -eu

role="${1:-api}"

case "$role" in
  api)
    exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
    ;;
  worker)
    exec dramatiq app.workers.checks --processes "${WORKER_PROCESSES:-1}" --threads "${WORKER_THREADS:-4}"
    ;;
  scheduler)
    exec python -m app.scheduler.main
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  create-admin)
    exec python -m scripts.create_admin
    ;;
  *)
    # Fall through to an arbitrary command (e.g. a shell for debugging).
    exec "$@"
    ;;
esac
