"""Загрузка и оптимизация фото моделей (ТЗ §11).

Поддерживаются JPG, PNG, WebP, до 10 МБ. После загрузки изображение
оптимизируется с сохранением пропорций.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.config import get_settings

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 МБ (ТЗ §11)
MAX_DIMENSION = 1600  # макс. сторона после оптимизации, px
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
SUBDIR = "models"
LOGO_SUBDIR = "logo"
LOGO_MAX_DIMENSION = 600  # логотипу столько с запасом хватает для PDF и веба


class ImageError(Exception):
    """Ошибка обработки изображения."""


def _models_dir() -> Path:
    path = Path(get_settings().storage_path) / SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_model_photo(raw: bytes) -> str:
    """Сохранить и оптимизировать фото модели. Вернуть относительный путь.

    Возвращаемый путь — относительно STORAGE_PATH, пригоден для отдачи через /media.
    """
    if not raw:
        raise ImageError("Пустой файл")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ImageError("Файл больше 10 МБ")

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageError("Неподдерживаемый или повреждённый файл") from exc

    if image.format not in ALLOWED_FORMATS:
        raise ImageError("Допустимы только JPG, PNG, WebP")

    # Оптимизация с сохранением пропорций.
    image.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
    if image.mode in ("RGBA", "P", "LA"):
        image = image.convert("RGB")

    filename = f"{uuid.uuid4().hex}.jpg"
    target = _models_dir() / filename
    image.save(target, format="JPEG", quality=85, optimize=True)
    return f"{SUBDIR}/{filename}"


def save_logo(raw: bytes) -> str:
    """Сохранить логотип компании (для сметы/packing и веба). Вернуть относительный путь.

    В отличие от фото моделей, логотип сохраняем в PNG — чтобы не терять прозрачность.
    """
    if not raw:
        raise ImageError("Пустой файл")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ImageError("Файл больше 10 МБ")

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageError("Неподдерживаемый или повреждённый файл") from exc

    if image.format not in ALLOWED_FORMATS:
        raise ImageError("Допустимы только JPG, PNG, WebP")

    image.thumbnail((LOGO_MAX_DIMENSION, LOGO_MAX_DIMENSION))
    if image.mode not in ("RGBA", "RGB", "LA", "L"):
        image = image.convert("RGBA")

    directory = Path(get_settings().storage_path) / LOGO_SUBDIR
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.png"
    image.save(directory / filename, format="PNG", optimize=True)
    return f"{LOGO_SUBDIR}/{filename}"


def delete_photo(rel_path: str | None) -> None:
    """Удалить файл фото по относительному пути (молча игнорирует отсутствие)."""
    if not rel_path:
        return
    target = Path(get_settings().storage_path) / rel_path
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass
