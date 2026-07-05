"""CLI резервного копирования (ТЗ §36).

Создаёт резервную копию (дамп БД + файлы STORAGE_PATH) в BACKUP_PATH и применяет
retention. Подходит для ручного запуска и планировщика ОС (cron / Task Scheduler).

    python -m scripts.backup
"""

from __future__ import annotations

import sys

from app.backup import service


def main() -> int:
    try:
        path = service.create_backup()
    except service.BackupError as exc:
        print(f"Ошибка backup: {exc}", file=sys.stderr)
        return 1
    removed = service.apply_retention()
    print(f"Создана резервная копия: {path}")
    if removed:
        print(f"Удалено устаревших копий: {removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
