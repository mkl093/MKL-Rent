"""Резервное копирование и восстановление (ТЗ §36).

Backup = дамп базы данных + файлы STORAGE_PATH (фото, логотип, PDF) в один
архив .tar.gz в BACKUP_PATH (директория хост-системы вне рабочих volume).
Поддержаны PostgreSQL (pg_dump/pg_restore) и SQLite (консистентная копия файла).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import get_settings
from app.database import utcnow

ARCHIVE_PREFIX = "backup_"
ARCHIVE_SUFFIX = ".tar.gz"


class BackupError(RuntimeError):
    """Ошибка резервного копирования/восстановления."""


@dataclass
class BackupInfo:
    name: str
    path: Path
    size_bytes: int
    created_at: datetime


# --- Дамп/восстановление БД ---------------------------------------------


def _pg_env() -> dict[str, str]:
    s = get_settings()
    return {**os.environ, "PGPASSWORD": s.database_password}


def _dump_database(tmp: Path) -> tuple[str, str]:
    """Сдампить БД во временную папку. Вернуть (имя файла в архиве, движок)."""
    s = get_settings()
    url = make_url(s.sqlalchemy_url)
    if url.drivername.startswith("sqlite"):
        src = Path(url.database or "")
        dst = tmp / "database.sqlite"
        if src.exists():
            # Консистентная копия через backup API SQLite.
            src_con = sqlite3.connect(str(src))
            dst_con = sqlite3.connect(str(dst))
            with dst_con:
                src_con.backup(dst_con)
            src_con.close()
            dst_con.close()
        else:
            dst.write_bytes(b"")
        return "database.sqlite", "sqlite"

    out = tmp / "database.dump"
    cmd = [
        "pg_dump",
        "-h",
        s.database_host,
        "-p",
        str(s.database_port),
        "-U",
        s.database_user,
        "-d",
        s.database_name,
        "-Fc",
        "-f",
        str(out),
    ]
    try:
        subprocess.run(cmd, env=_pg_env(), check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise BackupError("pg_dump не найден — установите клиент PostgreSQL") from exc
    except subprocess.CalledProcessError as exc:
        raise BackupError(
            f"pg_dump завершился с ошибкой: {exc.stderr.decode(errors='ignore')}"
        ) from exc
    return "database.dump", "postgres"


def _restore_database(db_file: Path, engine: str) -> None:
    s = get_settings()
    if engine == "sqlite":
        url = make_url(s.sqlalchemy_url)
        dst = Path(url.database or "")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(db_file, dst)
        return

    cmd = [
        "pg_restore",
        "-h",
        s.database_host,
        "-p",
        str(s.database_port),
        "-U",
        s.database_user,
        "-d",
        s.database_name,
        "--clean",
        "--if-exists",
        str(db_file),
    ]
    try:
        subprocess.run(cmd, env=_pg_env(), check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise BackupError("pg_restore не найден — установите клиент PostgreSQL") from exc
    except subprocess.CalledProcessError as exc:
        raise BackupError(
            f"pg_restore завершился с ошибкой: {exc.stderr.decode(errors='ignore')}"
        ) from exc


# --- Создание / список / retention --------------------------------------


def create_backup() -> Path:
    """Создать резервную копию (дамп БД + файлы) и вернуть путь к архиву (ТЗ §36)."""
    s = get_settings()
    backup_dir = Path(s.backup_path)
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{ARCHIVE_PREFIX}{ts}{ARCHIVE_SUFFIX}"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_member, engine = _dump_database(tmp)
        manifest = {
            "app": "rental-inventory",
            "created_at": utcnow().isoformat(),
            "engine": engine,
            "db_member": db_member,
        }
        with tarfile.open(target, "w:gz") as tar:
            tar.add(tmp / db_member, arcname=db_member)
            storage = Path(s.storage_path)
            if storage.exists() and any(storage.iterdir()):
                tar.add(storage, arcname="storage")
            raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            info = tarfile.TarInfo("manifest.json")
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    return target


def list_backups() -> list[BackupInfo]:
    s = get_settings()
    backup_dir = Path(s.backup_path)
    if not backup_dir.exists():
        return []
    result: list[BackupInfo] = []
    for path in backup_dir.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}"):
        stat = path.stat()
        result.append(
            BackupInfo(
                name=path.name,
                path=path,
                size_bytes=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_mtime),
            )
        )
    return sorted(result, key=lambda b: b.name, reverse=True)


def apply_retention() -> int:
    """Удалить архивы старше BACKUP_RETENTION_DAYS. Вернуть число удалённых."""
    s = get_settings()
    if s.backup_retention_days <= 0:
        return 0
    cutoff = datetime.now().timestamp() - s.backup_retention_days * 86400
    removed = 0
    for info in list_backups():
        if info.path.stat().st_mtime < cutoff:
            info.path.unlink(missing_ok=True)
            removed += 1
    return removed


def get_backup(name: str) -> Path | None:
    """Безопасно получить путь к архиву по имени (без обхода каталога)."""
    if "/" in name or "\\" in name or not name.startswith(ARCHIVE_PREFIX):
        return None
    path = Path(get_settings().backup_path) / name
    return path if path.exists() else None


def restore_backup(archive: Path) -> dict:
    """Восстановить БД и файлы из архива (ТЗ §36). Приложение должно быть остановлено."""
    if not archive.exists():
        raise BackupError(f"Архив не найден: {archive}")
    s = get_settings()
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.extractfile("manifest.json")
        if member is None:
            raise BackupError("В архиве нет manifest.json")
        manifest = json.loads(member.read().decode("utf-8"))
        engine = manifest["engine"]
        db_member = manifest["db_member"]
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            tar.extractall(tmp, filter="data")
            _restore_database(tmp / db_member, engine)
            storage_src = tmp / "storage"
            if storage_src.exists():
                dst = Path(s.storage_path)
                shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(storage_src, dst)
    return manifest
