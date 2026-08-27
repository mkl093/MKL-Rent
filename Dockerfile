FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Системные зависимости: curl (healthcheck), библиотеки WeasyPrint (ТЗ §26),
# postgresql-client для pg_dump/pg_restore в backup (ТЗ §36).
#
# Версия клиента ЗАКРЕПЛЕНА на 16 — должна совпадать с мажорной версией сервера
# `db` в docker-compose.yml (postgres:16-alpine). Иначе pg_restore новее сервера
# шлёт неизвестные серверу GUC (напр. transaction_timeout, появился в PG17) и
# падает при восстановлении: `pg_restore: error: unrecognized configuration
# parameter "transaction_timeout"`. Пакет нужной версии берём из официального
# репозитория PostgreSQL (apt.postgresql.org) — в базовом образе Debian по
# умолчанию может быть другая мажорная версия. При апгрейде `db` на новый
# мажор — обновите номер версии здесь тоже.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        gnupg \
        ca-certificates \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        shared-mime-info \
        fonts-dejavu-core \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo "$VERSION_CODENAME")-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY . .

# Непривилегированный пользователь (ТЗ §41.2). UID/GID можно подогнать под хост,
# чтобы примонтированный каталог backups был доступен на запись (см. DEPLOY.md).
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd -g "${APP_GID}" app \
    && useradd -m -u "${APP_UID}" -g app app \
    # Нормализуем перевод строк entrypoint (на случай CRLF из Windows) и делаем исполняемым.
    && sed -i 's/\r$//' /app/docker/entrypoint.sh \
    && chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /data/storage /backups \
    && chown -R app:app /app /data /backups

ENV STORAGE_PATH=/data/storage \
    BACKUP_PATH=/backups

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# Миграции применяются в entrypoint перед стартом; за прокси — proxy-headers.
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
