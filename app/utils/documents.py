"""Загрузка файлов мануалов и сертификатов испытаний.

Поддерживаются PDF и ZIP, до 50 МБ. Формат проверяется не только по
расширению, но и по сигнатуре первых байт файла.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.config import get_settings

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 МБ
MANUALS_SUBDIR = "manuals"
CERTIFICATES_SUBDIR = "certificates"

_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".zip": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
}


class DocumentError(Exception):
    """Ошибка загрузки файла мануала/сертификата."""


def _dir(subdir: str) -> Path:
    path = Path(get_settings().storage_path) / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_document(raw: bytes, original_filename: str, subdir: str) -> str:
    """Проверить и сохранить файл. Вернуть путь относительно STORAGE_PATH.

    На диске файл получает случайное имя (во избежание коллизий и обхода
    пути); оригинальное имя для отображения/скачивания хранится в БД.
    """
    if not raw:
        raise DocumentError("Пустой файл")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise DocumentError("Файл больше 50 МБ")

    ext = Path(original_filename).suffix.lower()
    signatures = _SIGNATURES.get(ext)
    if signatures is None:
        raise DocumentError("Допустимы только файлы PDF и ZIP")
    if not raw.startswith(signatures):
        raise DocumentError("Содержимое файла не соответствует расширению")

    filename = f"{uuid.uuid4().hex}{ext}"
    target = _dir(subdir) / filename
    target.write_bytes(raw)
    return f"{subdir}/{filename}"


def format_filesize(size_bytes: int) -> str:
    """Человекочитаемый размер: КБ до 1 МБ, иначе МБ (иначе мелкие файлы «0.0 МБ»)."""
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} КБ"
    return f"{size_bytes / 1024 / 1024:.1f} МБ"


def delete_document(rel_path: str | None) -> None:
    """Удалить файл по относительному пути (молча игнорирует отсутствие)."""
    if not rel_path:
        return
    target = Path(get_settings().storage_path) / rel_path
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass
