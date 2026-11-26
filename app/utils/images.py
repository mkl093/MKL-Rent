"""Загрузка и оптимизация фото моделей (ТЗ §11).

Поддерживаются JPG, PNG, WebP, до 10 МБ. После загрузки изображение
оптимизируется с сохранением пропорций.

Для списков (склад, подбор в смету/packing) рядом с фото хранится квадратная
миниатюра `<имя>.thumb.jpg`: в ячейке 36–56px незачем качать снимок до 1600px.
Миниатюра создаётся при загрузке фото, для старых фото — командой
`python -m scripts.make_thumbnails` или при первом запросе (make_thumbnail).
"""

from __future__ import annotations

import io
import os
import uuid
from pathlib import Path, PurePosixPath

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 МБ (ТЗ §11)
MAX_DIMENSION = 1600  # макс. сторона после оптимизации, px
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
SUBDIR = "models"
LOGO_SUBDIR = "logo"
LOGO_MAX_DIMENSION = 600  # логотипу столько с запасом хватает для PDF и веба
# Сторона квадратной миниатюры: ячейка до 56px × плотность экрана до 3x с запасом.
THUMB_SIZE = 192
THUMB_SUFFIX = ".thumb.jpg"


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
    # JPEG не поддерживает альфа-канал. Прямой convert("RGB") просто отбрасывает
    # альфу, а прозрачные пиксели PNG обычно хранят (0,0,0) под ней — без
    # композитинга на подложку они превращаются в чёрную заливку. Поэтому
    # прозрачность сначала "сплющиваем" на белый фон, и только потом в RGB.
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")

    filename = f"{uuid.uuid4().hex}.jpg"
    target = _models_dir() / filename
    image.save(target, format="JPEG", quality=85, optimize=True)
    rel = f"{SUBDIR}/{filename}"
    try:
        _write_thumbnail(image, _abs(thumb_rel_path(rel)))
    except OSError:
        pass  # не критично: миниатюра создастся при первом запросе списка
    return rel


# --- Миниатюры для списков ------------------------------------------------


def _abs(rel_path: str) -> Path:
    return Path(get_settings().storage_path) / rel_path


def is_model_photo_path(rel_path: str | None) -> bool:
    """Путь похож на фото модели (models/<имя>.jpg), а не на его миниатюру."""
    if not rel_path or "\\" in rel_path:
        return False
    p = PurePosixPath(rel_path)
    return (
        len(p.parts) == 2
        and p.parts[0] == SUBDIR
        and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        and not p.name.endswith(THUMB_SUFFIX)
    )


def thumb_rel_path(rel_path: str) -> str:
    """Относительный путь миниатюры для фото модели: models/<stem>.thumb.jpg."""
    p = PurePosixPath(rel_path)
    return str(p.with_name(p.stem + THUMB_SUFFIX))


def _write_thumbnail(image: Image.Image, target: Path) -> None:
    """Квадратная миниатюра с обрезкой по центру — как object-fit: cover в списке.

    Пишется во временный файл и атомарно подменяется: два параллельных запроса
    не увидят недописанный файл.
    """
    thumb = ImageOps.fit(image.convert("RGB"), (THUMB_SIZE, THUMB_SIZE))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        thumb.save(tmp, format="JPEG", quality=82, optimize=True)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def make_thumbnail(rel_path: str, *, force: bool = False) -> Path | None:
    """Создать миниатюру фото модели (если её нет или force). Вернуть путь или None.

    None — фото не найдено или не читается; вызывающий отдаёт исходник/ничего.
    """
    if not is_model_photo_path(rel_path):
        return None
    source = _abs(rel_path)
    target = _abs(thumb_rel_path(rel_path))
    if target.is_file() and not force:
        return target
    if not source.is_file():
        return None
    try:
        with Image.open(source) as image:
            # Для JPEG декодируем сразу в уменьшенном масштабе — быстрее и меньше памяти.
            image.draft("RGB", (THUMB_SIZE * 2, THUMB_SIZE * 2))
            image.load()
            _write_thumbnail(image, target)
    except (UnidentifiedImageError, OSError):
        return None
    return target


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
    targets = [_abs(rel_path)]
    if is_model_photo_path(rel_path):
        targets.append(_abs(thumb_rel_path(rel_path)))
    for target in targets:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
