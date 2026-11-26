"""Создать миниатюры для уже загруженных фото моделей.

Новые фото получают миниатюру сразу при загрузке, а отсутствующую миниатюру
приложение создаёт при первом запросе списка. Этот скрипт — разовый проход после
деплоя, чтобы первое открытие склада не создавало сотни миниатюр одновременно.
Повторный запуск безопасен: существующие миниатюры пропускаются (кроме --force).

Пример:
    docker compose exec web python -m scripts.make_thumbnails
    docker compose exec web python -m scripts.make_thumbnails --force
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from app.database import SessionLocal
from app.inventory.models import EquipmentModel
from app.utils.images import make_thumbnail


def main() -> int:
    parser = argparse.ArgumentParser(description="Миниатюры фото моделей для списков")
    parser.add_argument(
        "--force", action="store_true", help="Пересоздать и уже существующие миниатюры"
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        paths = list(
            db.scalars(
                select(EquipmentModel.photo_path).where(EquipmentModel.photo_path.is_not(None))
            )
        )

    done = failed = 0
    for rel in dict.fromkeys(paths):
        if make_thumbnail(rel, force=args.force) is None:
            failed += 1
            print(f"не удалось: {rel}")
        else:
            done += 1
    print(f"Готово: {done}, не удалось: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
