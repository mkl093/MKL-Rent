"""Планировщик ежедневного backup внутри приложения (ТЗ §36).

Включается через BACKUP_AUTO=true. Лёгкий фоновый поток: спит до BACKUP_TIME,
создаёт резервную копию и применяет retention. Ошибки не роняют приложение.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

from app.backup import service
from app.config import get_settings

logger = logging.getLogger("backup.scheduler")


def _seconds_until(hhmm: str) -> float:
    now = datetime.now()
    try:
        hour, minute = (int(x) for x in hhmm.split(":"))
    except (ValueError, AttributeError):
        hour, minute = 3, 0
    nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


def _loop() -> None:
    while True:
        time.sleep(max(60.0, _seconds_until(get_settings().backup_time)))
        try:
            path = service.create_backup()
            removed = service.apply_retention()
            logger.info("Авто-backup создан: %s (удалено старых: %s)", path.name, removed)
        except Exception:  # noqa: BLE001 — авто-backup не должен ронять приложение
            logger.exception("Ошибка авто-backup")


def start() -> None:
    if not get_settings().backup_auto:
        return
    threading.Thread(target=_loop, name="backup-scheduler", daemon=True).start()
    logger.info("Планировщик авто-backup запущен (время %s)", get_settings().backup_time)


def run_foreground() -> None:
    """Запуск планировщика в foreground — для отдельного сервиса compose (ТЗ §35).

    При нескольких воркерах web авто-backup не запускают внутри web (иначе N копий),
    а выносят в один сервис `scheduler`, который выполняет тот же цикл, что и start().
    """
    settings = get_settings()
    if not settings.backup_auto:
        logger.info("BACKUP_AUTO выключен — планировщик не запускается")
        return
    logger.info("Планировщик авто-backup (foreground) запущен, время %s", settings.backup_time)
    _loop()


if __name__ == "__main__":  # pragma: no cover — точка входа сервиса scheduler
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    run_foreground()
