# Разработка проекта

Руководство для контрибьюторов: архитектура, конвенции и команды. Полное
техническое задание — в [`TECHNICAL_SPECIFICATION.md`](TECHNICAL_SPECIFICATION.md),
инструкция по деплою — в [`DEPLOY.md`](DEPLOY.md).

## Правила проекта

1. Перед реализацией прочитайте TECHNICAL_SPECIFICATION.md.
2. Это модульный монолит на FastAPI, PostgreSQL, SQLAlchemy 2, Alembic, Jinja2,
   HTMX и Bootstrap 5.
3. Не вводить React, Flask, Redis, Celery или микросервисы.
4. Не держать бизнес-логику в роут-хендлерах и шаблонах.
5. Все денежные расчёты — только `Decimal`.
6. Добавлять тесты на каждое бизнес-правило.
7. Уникальность штрих-кодов, брони и packing-сканы — через ограничения и транзакции PostgreSQL.
8. Не менять утверждённый scope MVP без явного указания.
9. Перед кодом — изучить существующие модели, сервисы, миграции и тесты.
10. После кода — прогнать тесты и перечислить изменённые файлы.

## Готовность этапов (ТЗ §44)

Готово: Этапы 1–8 (основа, склад, проекты/доступность, смета, packing, штрих-коды/mobile,
PDF, backup/hardening). Плюс доработки: единый поединичный учёт со статусами (в т.ч.
«Есть дефект»), статус проекта «Отгружено», дерево склада, фильтр доступности по датам,
локальный HTTPS для камеры, журнал действий, управление пользователями, индивидуальная
скидка по строкам сметы, добавление складского оборудования в packing-лист (подбор с
доступностью, как в смете), фактические даты отгрузки/возврата проекта (авто по статусу +
ручная правка, вывод в packing/PDF; ТЗ §13.4.1), трёхцветная индикация состояний склада
(доступно/зарезервировано/в работе) с учётом просрочки отгрузки и раскрытием перечня
занявших проектов (ТЗ §15.1–15.2), календарь склада `/inventory/planboard` — занятость
моделей по дням (ТЗ §15.3).

Модули добавленные после базовой раскладки: estimates/, packing/, documents/ (PDF),
audit/ (журнал §29), users/ (управление §4), backup/ (резервные копии §36).

## Структура проекта (модульный монолит)

```
app/
  main.py            # app factory, middleware, /static, /media, роутеры, /healthz
  config.py          # Settings (pydantic-settings) из .env; sqlalchemy_url
  database.py        # engine, SessionLocal, Base, get_db, utcnow, as_utc, TimestampMixin, _enum_column
  dependencies.py    # get_current_user, require_login, verify_csrf, render, redirect
  templating.py      # Jinja2 + CSRF + flash-сообщения
  auth/              # пользователи и вход (модель User, rate-limit)
  settings/          # настройки компании (singleton CompanySettings)
  inventory/         # склад: models, enums, schemas, services/{categories,equipment,items}, router
  projects/          # проекты: models, enums, schemas, availability (движок), service, router
  numbering/         # счётчики номеров PRJ/EST/PL (models, service.next_number)
  dashboard/         # главная: service (виджеты §5), router
  utils/             # security (argon2), timezone, images (Pillow)
  templates/ static/ # static/vendor — вендоренные Bootstrap 5 + htmx
migrations/          # Alembic (env.py берёт URL и metadata из app); versions/000N_*.py
tests/{unit,integration}/   # unit на SQLite, integration на PostgreSQL (маркер integration)
scripts/create_user.py
```

При добавлении модуля с таблицами — импортировать его `models` в `app/main.py`,
`migrations/env.py` и `tests/conftest.py` (регистрация в `Base.metadata`).

## Конвенции

- Бизнес-логика — в `*/service.py` (или `services/`); роутеры тонкие, шаблоны без расчётов.
- Деньги/ставки/вес — только `Decimal`/`Numeric`, никогда `float`.
- Время храним в UTC (`utcnow()`), отображаем через `utils/timezone.py`; naive-время трактуем как UTC (`as_utc`).
- Перечисления — `enum.StrEnum`; в БД хранится `.value` через `_enum_column` (см. `inventory/models.py`).
- Формы защищены CSRF (`Depends(verify_csrf)`), страницы — `Depends(require_login)`; рендер через `render(...)`.
- Номера документов — только `numbering.service.next_number` (ручное изменение запрещено, ТЗ §14).
- Доступность/дефицит — единый движок `projects/availability.py`; формулы не дублировать.
- Применённые миграции задним числом не править — добавлять новые (по возрастанию номера `00NN_*`).
- Этапы и бизнес-правила — в TECHNICAL_SPECIFICATION.md (§40 правила, §44 этапы).

## Команды

- Запуск:        `uvicorn app.main:app --reload`  (или `python main.py`)
- Миграции:      `alembic upgrade head` / `alembic revision --autogenerate -m "..."`
- Пользователь:  `python -m scripts.create_user --username admin`
- Тесты:         `pytest` (unit) / `pytest -m integration` (нужен DATABASE_URL_TEST)
- Линт/формат:   `ruff check .` / `ruff format .`
