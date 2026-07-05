"""CLI восстановления из резервной копии (ТЗ §36).

ВНИМАНИЕ: перезаписывает текущую базу и файлы. Приложение должно быть остановлено.

    python -m scripts.restore backups/backup_20260706_030000.tar.gz
    python -m scripts.restore <архив> --yes     # без подтверждения
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.backup import service


def main() -> int:
    parser = argparse.ArgumentParser(description="Восстановление из резервной копии")
    parser.add_argument("archive", help="Путь к архиву backup_*.tar.gz")
    parser.add_argument("--yes", action="store_true", help="Не спрашивать подтверждение")
    args = parser.parse_args()

    archive = Path(args.archive)
    if not archive.exists():
        print(f"Архив не найден: {archive}", file=sys.stderr)
        return 1

    if not args.yes:
        print("ВНИМАНИЕ: текущая база и файлы будут перезаписаны данными из копии.")
        if input("Продолжить? [y/N]: ").strip().lower() not in ("y", "yes", "д", "да"):
            print("Отменено.")
            return 1

    try:
        manifest = service.restore_backup(archive)
    except service.BackupError as exc:
        print(f"Ошибка восстановления: {exc}", file=sys.stderr)
        return 1

    print(f"Восстановлено из {archive.name} (движок: {manifest.get('engine')}).")
    print("Запустите приложение и при необходимости выполните: alembic upgrade head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
