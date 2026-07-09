#!/bin/sh
# Entrypoint контейнера web/scheduler (ТЗ §35).
#   1. Ждём готовности PostgreSQL (идемпотентно; полезно и для внешней БД).
#   2. При RUN_MIGRATIONS=true применяем миграции (alembic upgrade head).
#   3. exec на переданную команду (uvicorn / планировщик backup).
set -eu

DB_HOST="${DATABASE_HOST:-db}"
DB_PORT="${DATABASE_PORT:-5432}"
DB_USER="${DATABASE_USER:-rental}"

# Для SQLite-override ждать/мигрировать через pg_isready не нужно.
case "${DATABASE_URL:-}" in
  sqlite*) SKIP_DB_WAIT=1 ;;
  *) SKIP_DB_WAIT=0 ;;
esac

if [ "$SKIP_DB_WAIT" = "0" ]; then
  echo "[entrypoint] Ожидание PostgreSQL ${DB_HOST}:${DB_PORT}..."
  tries=0
  until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -ge 60 ]; then
      echo "[entrypoint] БД не готова за 120с — выход" >&2
      exit 1
    fi
    sleep 2
  done
  echo "[entrypoint] PostgreSQL готов."
fi

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "[entrypoint] Применение миграций: alembic upgrade head"
  alembic upgrade head
fi

exec "$@"
