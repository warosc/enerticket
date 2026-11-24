#!/bin/sh
# Punto de entrada del contenedor backend.
#   api            -> migraciones (alembic upgrade head), seeds idempotentes y uvicorn
#   migrate        -> solo migraciones + seeds
#   cualquier otro -> se ejecuta tal cual (p.ej. "celery -A app.tasks.celery_app worker")
set -eu

run_migrations() {
    echo "[entrypoint] alembic upgrade head"
    alembic upgrade head
    echo "[entrypoint] seeds (python -m app.seeds)"
    python -m app.seeds
}

case "${1:-api}" in
    api)
        run_migrations
        echo "[entrypoint] iniciando uvicorn"
        exec uvicorn app.main:app \
            --host 0.0.0.0 \
            --port "${PORT:-8000}" \
            --workers "${UVICORN_WORKERS:-1}" \
            --proxy-headers \
            --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
            --ws-max-size "${WS_MAX_MESSAGE_BYTES:-16384}"
        ;;
    migrate)
        run_migrations
        ;;
    *)
        exec "$@"
        ;;
esac
