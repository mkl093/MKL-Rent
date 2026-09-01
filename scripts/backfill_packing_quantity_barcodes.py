"""Разовый бэкфилл штрих-кодов количественных строк packing-листа.

Ту же логику (app.packing.service.backfill_quantity_barcodes) теперь дополнительно
вызывают документы (PDF packing-листа, carnet) при каждой пересборке — так что для
новых экспортов отдельный запуск не нужен. Этот скрипт — для разового прохода по
всей базе сразу, не дожидаясь, пока кто-то откроет/пересоберёт документ по каждому
проекту вручную.

Заготовки создаются как confirmed_by_scan=False — реальное сканирование на
погрузке их, как обычно, заменит.

Архивные проекты (Завершён/Отменён) не трогаются: задним числом привязывать
конкретный физический экземпляр к уже закрытому проекту может быть неверно —
эта единица могла успеть уйти в другой проект.

Пример:
    python -m scripts.backfill_packing_quantity_barcodes --dry-run
    python -m scripts.backfill_packing_quantity_barcodes
"""

from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.packing.models import PackingLine, PackingList
from app.packing.service import backfill_quantity_barcodes
from app.projects.enums import ProjectStatus
from app.projects.models import Project


def main() -> int:
    parser = argparse.ArgumentParser(description="Бэкфилл штрих-кодов количественных строк")
    parser.add_argument(
        "--dry-run", action="store_true", help="Показать, что будет сделано, без сохранения"
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        packings = (
            db.execute(
                select(PackingList)
                .join(Project, Project.id == PackingList.project_id)
                .where(
                    Project.status.not_in([ProjectStatus.COMPLETED, ProjectStatus.CANCELLED])
                )
                .options(selectinload(PackingList.lines).selectinload(PackingLine.serial_items))
                .order_by(PackingList.number)
            )
            .scalars()
            .all()
        )

        touched_lists = 0
        attached_total = 0
        for packing in packings:
            attached = backfill_quantity_barcodes(db, packing, commit=False)
            if attached:
                touched_lists += 1
                attached_total += attached
                print(f"  {packing.number}: привязано {attached}")

        print(f"\nPacking-листов затронуто: {touched_lists}, экземпляров привязано: {attached_total}")
        if args.dry_run:
            db.rollback()
            print("Dry-run — изменения не сохранены.")
        else:
            db.commit()
            print("Изменения сохранены.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
