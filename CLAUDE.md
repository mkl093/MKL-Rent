# Claude Code Project Rules

1. Read TECHNICAL_SPECIFICATION.md before implementation.
2. This is a modular monolith built with FastAPI, PostgreSQL, SQLAlchemy 2,
   Alembic, Jinja2, HTMX and Bootstrap 5.
3. Do not introduce React, Flask, Redis, Celery or microservices.
4. Keep business logic out of route handlers and templates.
5. Use Decimal for all money calculations.
6. Add tests for every business rule.
7. Use PostgreSQL constraints and transactions for barcode uniqueness,
   reservations and packing scans.
8. Do not change the approved MVP scope without explicit instruction.
9. Before coding, inspect existing models, services, migrations and tests.
10. After coding, run tests and summarize changed files.

## Структура проекта (модульный монолит)

```
app/
  main.py            # app factory, middleware, подключение роутеров, /healthz
  config.py          # Settings (pydantic-settings) из .env
  database.py        # engine, SessionLocal, Base, get_db, utcnow, TimestampMixin
  dependencies.py    # get_current_user, require_login, verify_csrf, render, redirect
  templating.py      # Jinja2 + CSRF + flash-сообщения
  auth/              # пользователи и вход
  settings/          # настройки компании (singleton)
  dashboard/ inventory/ projects/   # разделы (часть — заглушки до своих этапов)
  utils/             # security (argon2), timezone
  templates/ static/
migrations/          # Alembic (env.py берёт URL и metadata из app)
tests/{unit,integration}/   # unit на SQLite, integration на PostgreSQL
scripts/create_user.py
```

## Конвенции

- Бизнес-логика — в `*/service.py`; роутеры тонкие.
- Деньги/ставки — только `Decimal`/`Numeric`, никогда `float`.
- Время храним в UTC (`utcnow()`), отображаем через `utils/timezone.py`.
- Формы защищены CSRF (`Depends(verify_csrf)`), страницы — `Depends(require_login)`.
- Применённые миграции задним числом не править — добавлять новые.
- Этапы разработки и бизнес-правила — в TECHNICAL_SPECIFICATION.md (§40, §44).

## Команды

- Запуск:        `uvicorn app.main:app --reload`
- Миграции:      `alembic upgrade head` / `alembic revision --autogenerate -m "..."`
- Пользователь:  `python -m scripts.create_user --username admin`
- Тесты:         `pytest` (unit) / `pytest -m integration` (нужен DATABASE_URL_TEST)
- Линт/формат:   `ruff check .` / `ruff format .`
