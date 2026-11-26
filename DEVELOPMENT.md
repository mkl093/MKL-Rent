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
доступностью, как в смете), плановые даты отгрузки/возврата проекта (авто по статусу, если
пусты, + ручная правка, вывод в packing/PDF; расширяют окно занятости — ТЗ §13.4.1, §15.1;
окно считается в `app/projects/availability.py`: `occupancy_start_expr()`/`occupancy_end_expr()`
через CASE, т.к. в SQLite нет LEAST/GREATEST), трёхцветная индикация состояний склада
(доступно/зарезервировано/в работе) с учётом просрочки отгрузки и раскрытием перечня
занявших проектов (ТЗ §15.1–15.2), календарь склада `/inventory/planboard` — занятость
моделей по дням (ТЗ §15.3), календарь занятости персонала `/calendar` (ТЗ §54), файловое
хранилище мануалов модели и сертификатов испытаний единицы (ТЗ §55), подсказки поиска по
складу при вводе (ТЗ §25.1): сервис `app/inventory/services/search.py`, htmx-фрагмент
`inventory/_search_suggest.html`, клавиатура в `static/js/inventory-search.js`. Любой
htmx-запрос без входа получает `HX-Redirect: /login` (гостевой — `/guest/login`) вместо 303:
это делают общие обработчики `LoginRequired`/`GuestLoginRequired` в `app/main.py`, иначе
htmx прошёл бы редирект сам и вставил страницу входа во фрагмент. Календарь склада на
телефоне: таблица в своей области прокрутки (высоту подгоняет скрипт страницы под остаток
экрана), шапка с датами и колонка моделей закреплены, детали ячейки — в модальном окне
`#pb-modal`, фильтр свёрнут за кнопкой «Фильтр». Списки склада и подбора в
смету/packing считают доступность пакетно (`compute_availability_bulk`,
`occupancy_detail_bulk`, `compute_kit_availability_bulk` в `app/projects/availability.py`)
— несколько GROUP BY-запросов на весь список; в списках фото — миниатюры
(`thumb_url()`, маршрут `/media/thumb/...`, `app/utils/images.py`, ТЗ §11).

Модули добавленные после базовой раскладки: estimates/, packing/, documents/ (PDF),
audit/ (журнал §29), users/ (управление §4), backup/ (резервные копии §36), staff/
(персонал и календарь занятости §54).

## Структура проекта (модульный монолит)

```
app/
  main.py            # app factory, middleware, /static, /media, роутеры, /healthz
  config.py          # Settings (pydantic-settings) из .env; sqlalchemy_url
  database.py        # engine, SessionLocal, Base, get_db, utcnow, as_utc, TimestampMixin, _enum_column
  dependencies.py    # get_current_user, require_login, verify_csrf, render, redirect
  templating.py      # Jinja2 + CSRF + flash-сообщения
  auth/              # пользователи и вход (модель User, rate-limit)
  settings/          # настройки компании (singleton CompanySettings), статистика хранилища
  inventory/         # склад: models, enums, schemas,
                     #   services/{categories,equipment,items,documents (§55)}, router
  projects/          # проекты: models, enums, schemas, availability (движок), service, router
  numbering/         # счётчики номеров PRJ/EST/PL (models, service.next_number)
  dashboard/         # главная: service (виджеты §5), router
  staff/             # персонал и календарь занятости (§54)
  utils/             # security (argon2), timezone, images (Pillow), documents (PDF/ZIP, §55)
  templates/ static/ # static/vendor — вендоренные Bootstrap 5 + htmx
migrations/          # Alembic (env.py берёт URL и metadata из app); versions/000N_*.py
tests/{unit,integration}/   # unit на SQLite, integration на PostgreSQL (маркер integration)
scripts/                     # create_user, backup, restore, make_cert, make_thumbnails, бэкфиллы
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
- Для списков не вызывать `compute_availability`/`occupancy_detail`/`stock_quantity` в
  цикле по моделям — использовать пакетные `*_bulk` (иначе N×5 запросов на страницу).
  Новое правило доступности добавлять в обе версии; `tests/unit/test_availability_bulk.py`
  сверяет пакетный расчёт с поштучным.
- Фото модели в списках — только `thumb_url(m.photo_path)` с `loading="lazy"`; полный
  `/media/<photo_path>` — в карточке и форме. Файлы в `storage/models` и `storage/logo`
  не перезаписывать под тем же именем: они кешируются браузером как immutable.
- Применённые миграции задним числом не править — добавлять новые (по возрастанию номера `00NN_*`).
- Этапы и бизнес-правила — в TECHNICAL_SPECIFICATION.md (§40 правила, §44 этапы).

## Команды

- Запуск:        `uvicorn app.main:app --reload`  (или `python main.py`)
- Миграции:      `alembic upgrade head` / `alembic revision --autogenerate -m "..."`
- Пользователь:  `python -m scripts.create_user --username admin`
- Миниатюры:     `python -m scripts.make_thumbnails` (`--force` — пересоздать все)
- Тесты:         `pytest` (unit) / `pytest -m integration` (нужен DATABASE_URL_TEST)
- Линт/формат:   `ruff check .` / `ruff format .`
