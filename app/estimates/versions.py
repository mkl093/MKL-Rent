"""Стенды проекта и версии смет: создание, активация, заморозка, копирование.

Стенд (ProjectSection) — площадка/стенд проекта с номером сметы EST-YYYY-NNN.
У стенда одна или несколько версий сметы (Estimate), ровно одна активна:
только активные версии всех стендов дают бронь проекта (estimates.service.
sync_reservations) и план packing-листа. Версии не удаляются; заморозка
делает версию только для просмотра (estimates.service.ensure_editable).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.models import User
from app.database import utcnow
from app.estimates import service
from app.estimates.models import Estimate, ProjectSection
from app.estimates.totals import EstimateTotals
from app.inventory.models import EquipmentModel, Kit
from app.numbering.models import DocType
from app.numbering.service import next_number
from app.projects.models import Project

DEFAULT_SECTION_NAME = "Основной стенд"


def _current_year() -> int:
    from app.utils.timezone import to_local

    return to_local(utcnow()).year


def _version_number(section: ProjectSection, version_no: int) -> str:
    """Номер версии: v1 — номер стенда (EST-2026-015), далее «EST-2026-015-v2»."""
    return section.number if version_no == 1 else f"{section.number}-v{version_no}"


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise service.EstimateError("Укажите название стенда")
    return name[:255]


def _clean_label(label: str | None) -> str | None:
    label = (label or "").strip()
    return label[:255] or None


# --- Стенды ----------------------------------------------------------------


def list_sections(db: Session, project: Project) -> list[ProjectSection]:
    stmt = (
        select(ProjectSection)
        .options(selectinload(ProjectSection.versions).selectinload(Estimate.lines))
        .where(ProjectSection.project_id == project.id)
        .order_by(ProjectSection.sort_order, ProjectSection.id)
    )
    return list(db.execute(stmt).scalars().all())


def get_section(db: Session, project: Project, section_id: int) -> ProjectSection | None:
    stmt = (
        select(ProjectSection)
        .options(selectinload(ProjectSection.versions).selectinload(Estimate.lines))
        .where(ProjectSection.id == section_id, ProjectSection.project_id == project.id)
    )
    return db.execute(stmt).scalar_one_or_none()


def create_section(
    db: Session, project: Project, name: str, *, user: User | None = None
) -> ProjectSection:
    """Новый стенд с номером EST-YYYY-NNN и пустой активной версией v1."""
    name = _clean_name(name)
    sort_order = max((s.sort_order for s in list_sections(db, project)), default=-1) + 1
    section = ProjectSection(
        project_id=project.id,
        name=name,
        number=next_number(db, DocType.ESTIMATE, _current_year()),
        sort_order=sort_order,
    )
    db.add(section)
    db.flush()
    db.add(
        Estimate(
            project_id=project.id,
            section_id=section.id,
            version_no=1,
            number=_version_number(section, 1),
            is_active=True,
            activated_at=utcnow(),
            created_by_id=user.id if user else None,
            discount_percent=Decimal("0"),
            rental_coefficient=project.rental_coefficient,
            hide_prices=False,
        )
    )
    db.commit()
    db.refresh(section)
    return section


def ensure_default_section(db: Session, project: Project) -> ProjectSection:
    """Первый стенд проекта; пустой проект получает «Основной стенд»."""
    sections = list_sections(db, project)
    if sections:
        return sections[0]
    return create_section(db, project, DEFAULT_SECTION_NAME)


def rename_section(db: Session, section: ProjectSection, name: str) -> None:
    section.name = _clean_name(name)
    db.commit()


def move_section(db: Session, project: Project, section: ProjectSection, direction: int) -> None:
    sections = list_sections(db, project)
    # Нормализуем порядок (у стендов из миграции он может совпадать).
    for i, s in enumerate(sections):
        s.sort_order = i
    idx = next(i for i, s in enumerate(sections) if s.id == section.id)
    swap = idx + direction
    if 0 <= swap < len(sections):
        other = sections[swap]
        sections[idx].sort_order, other.sort_order = other.sort_order, sections[idx].sort_order
    db.commit()


def delete_section(db: Session, project: Project, section: ProjectSection) -> None:
    """Удалить можно только стенд, все версии которого пусты (версии не удаляются)."""
    if any(v.lines for v in section.versions):
        raise service.EstimateError(
            "Стенд со строками в смете удалить нельзя — версии смет не удаляются"
        )
    db.delete(section)
    db.commit()
    service.sync_reservations(db, project)


# --- Версии ----------------------------------------------------------------


def get_version(db: Session, section: ProjectSection, version_no: int) -> Estimate | None:
    stmt = (
        select(Estimate)
        .options(selectinload(Estimate.lines))
        .where(Estimate.section_id == section.id, Estimate.version_no == version_no)
    )
    return db.execute(stmt).scalar_one_or_none()


def create_version(
    db: Session,
    project: Project,
    section: ProjectSection,
    *,
    source: Estimate | None = None,
    label: str | None = None,
    activate: bool = False,
    user: User | None = None,
) -> Estimate:
    """Новая версия стенда: копия ``source`` (строки, скидка, коэффициент, «скрыть
    стоимость») или пустая. Новая версия не заморожена; активной становится только
    при ``activate=True``.
    """
    version_no = max((v.version_no for v in section.versions), default=0) + 1
    version = Estimate(
        project_id=project.id,
        section_id=section.id,
        version_no=version_no,
        number=_version_number(section, version_no),
        label=_clean_label(label),
        is_active=False,
        created_by_id=user.id if user else None,
        discount_percent=source.discount_percent if source else Decimal("0"),
        rental_coefficient=(source.rental_coefficient if source else project.rental_coefficient),
        hide_prices=source.hide_prices if source else False,
    )
    if source is not None:
        for line in sorted(source.lines, key=lambda ln: (ln.sort_order, ln.id)):
            version.lines.append(service.copy_line(line))
    db.add(version)
    db.commit()
    db.refresh(section)
    if activate:
        activate_version(db, project, version)
    db.refresh(version)
    return version


def activate_version(db: Session, project: Project, version: Estimate) -> None:
    """Сделать версию активной (замороженную — тоже можно) и пересчитать бронь."""
    if version.is_active:
        return
    current = db.execute(
        select(Estimate).where(
            Estimate.section_id == version.section_id, Estimate.is_active.is_(True)
        )
    ).scalar_one_or_none()
    if current is not None:
        current.is_active = False
        # Сначала снять флаг, иначе сработает частичный уникальный индекс.
        db.flush()
    version.is_active = True
    version.activated_at = utcnow()
    db.commit()
    service.sync_reservations(db, project)


def lock_version(db: Session, version: Estimate, user: User | None) -> None:
    if version.is_locked:
        return
    version.is_locked = True
    version.locked_at = utcnow()
    version.locked_by_id = user.id if user else None
    db.commit()


def unlock_version(db: Session, version: Estimate) -> None:
    version.is_locked = False
    version.locked_at = None
    version.locked_by_id = None
    db.commit()


def set_version_label(db: Session, version: Estimate, label: str | None) -> None:
    service.ensure_editable(version)
    version.label = _clean_label(label)
    db.commit()


# --- Предпросмотр активации -----------------------------------------------


@dataclass
class ActivationPreview:
    """Что изменится при активации версии: бронь и расхождения packing-листа."""

    added: list[tuple[str, int]] = field(default_factory=list)  # (модель, кол-во)
    removed: list[tuple[str, int]] = field(default_factory=list)
    changed: list[tuple[str, int, int]] = field(default_factory=list)  # (модель, было, станет)
    kits_added: list[str] = field(default_factory=list)
    kits_removed: list[str] = field(default_factory=list)
    packing_exists: bool = False
    packing_discrepancies: int = 0

    @property
    def reservation_unchanged(self) -> bool:
        return not (
            self.added or self.removed or self.changed or self.kits_added or self.kits_removed
        )


def preview_activation(db: Session, project: Project, version: Estimate) -> ActivationPreview:
    current = service.active_versions(db, project)
    candidate = [v for v in current if v.section_id != version.section_id] + [version]
    cur_models, cur_kits = service.wanted_reservations(db, project, current)
    new_models, new_kits = service.wanted_reservations(db, project, candidate)

    def model_name(model_id: int) -> str:
        model = db.get(EquipmentModel, model_id)
        return model.name if model else f"#{model_id}"

    def kit_name(kit_id: int) -> str:
        kit = db.get(Kit, kit_id)
        return kit.name if kit else f"#{kit_id}"

    preview = ActivationPreview()
    for model_id, qty in new_models.items():
        before = cur_models.get(model_id)
        if before is None:
            preview.added.append((model_name(model_id), qty))
        elif before != qty:
            preview.changed.append((model_name(model_id), before, qty))
    for model_id, qty in cur_models.items():
        if model_id not in new_models:
            preview.removed.append((model_name(model_id), qty))
    preview.kits_added = [kit_name(k) for k in new_kits - cur_kits]
    preview.kits_removed = [kit_name(k) for k in cur_kits - new_kits]

    from app.packing import service as packing_service

    packing = packing_service.get_packing(db, project)
    if packing is not None:
        preview.packing_exists = True
        preview.packing_discrepancies = len(
            packing_service.discrepancies(db, project, packing, versions=candidate)
        )
    return preview


# --- Сводка и копирование --------------------------------------------------


@dataclass
class SectionSummary:
    section: ProjectSection
    active: Estimate | None
    totals: EstimateTotals | None
    versions_count: int


def section_summaries(db: Session, project: Project) -> list[SectionSummary]:
    result = []
    for section in list_sections(db, project):
        active = section.active_version
        result.append(
            SectionSummary(
                section=section,
                active=active,
                totals=service.totals(db, active, project) if active else None,
                versions_count=len(section.versions),
            )
        )
    return result


def copy_sections(db: Session, source: Project, target: Project) -> None:
    """Копия проекта (ТЗ §13.8): стенды и только их АКТИВНЫЕ версии (в копии — v1).

    Строки комплектов аксессуаров не копируются: комплект аксессуаров проектный
    (app.accessory_kits), в копии проекта его нет.
    """
    for src_section in list_sections(db, source):
        src = src_section.active_version
        section = create_section(db, target, src_section.name)
        dst = section.active_version
        assert dst is not None
        if src is not None:
            dst.discount_percent = src.discount_percent
            dst.rental_coefficient = src.rental_coefficient
            dst.hide_prices = src.hide_prices
            for line in sorted(src.lines, key=lambda ln: (ln.sort_order, ln.id)):
                if line.is_accessory_kit:
                    continue
                dst.lines.append(service.copy_line(line))
        db.commit()
    service.sync_reservations(db, target)
