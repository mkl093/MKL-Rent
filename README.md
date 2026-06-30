# rental-inventory

Система учёта оборудования для AV-рентала (MVP). Модульный монолит на FastAPI +
PostgreSQL + SQLAlchemy 2 + Jinja2 + HTMX + Bootstrap 5. Полное ТЗ — в
[`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md).

Текущее состояние: **Этап 1 «Основа»** — каркас приложения, конфигурация, БД и
миграции, авторизация, базовый layout, минимальные настройки компании, тесты.

## Требования

- Python 3.12+
- PostgreSQL (локально запущенный или доступный по сети)

## Локальный запуск (venv)

```bash
# 1. Зависимости (в активированном .venv)
pip install -e ".[dev]"

# 2. Конфигурация
cp .env.example .env
# отредактируйте .env: APP_SECRET_KEY и доступ к PostgreSQL (DATABASE_HOST=localhost)
# сгенерировать секрет: python -c "import secrets; print(secrets.token_urlsafe(48))"

# 3. Миграции (создаст таблицы на чистой БД)
alembic upgrade head

# 4. Первый пользователь
python -m scripts.create_user --username admin

# 5. Запуск
uvicorn app.main:app --reload
```

Откройте http://localhost:8000/ → форма входа. После входа доступны разделы
Главная / Проекты / Склад / Настройки. Healthcheck: `GET /healthz`.

> Быстрый смоук без PostgreSQL: задайте в `.env` `DATABASE_URL=sqlite:///./dev.db`
> и вместо `alembic upgrade head` создайте таблицы через приложение/тесты.
> Боевой режим и миграции рассчитаны на PostgreSQL.

## Тесты

```bash
pytest                 # unit-тесты (SQLite in-memory)
pytest -m integration  # integration-тесты (нужен DATABASE_URL_TEST с PostgreSQL)
```

## Качество кода

```bash
ruff check .
ruff format .
```

## Docker (создано, на Этапе 1 локально не проверялось)

```bash
docker compose up --build   # web + postgres + nginx + заготовка backup
```

## Структура

См. [`CLAUDE.md`](CLAUDE.md) — правила проекта и раскладка модулей.
