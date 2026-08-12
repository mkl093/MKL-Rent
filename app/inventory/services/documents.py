"""Сервис файлов мануалов моделей и сертификатов испытаний единиц оборудования."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.inventory.models import (
    EquipmentCertificate,
    EquipmentItem,
    EquipmentManual,
    EquipmentModel,
)
from app.utils.documents import delete_document


def get_manual(db: Session, model_id: int, manual_id: int) -> EquipmentManual | None:
    stmt = select(EquipmentManual).where(
        EquipmentManual.id == manual_id, EquipmentManual.model_id == model_id
    )
    return db.execute(stmt).scalar_one_or_none()


def create_manual(
    db: Session,
    model: EquipmentModel,
    *,
    title: str | None,
    file_path: str,
    original_filename: str,
    file_size: int,
    uploaded_by_id: int | None,
) -> EquipmentManual:
    manual = EquipmentManual(
        model_id=model.id,
        title=title,
        file_path=file_path,
        original_filename=original_filename,
        file_size=file_size,
        uploaded_at=utcnow(),
        uploaded_by_id=uploaded_by_id,
    )
    db.add(manual)
    db.commit()
    db.refresh(manual)
    return manual


def rename_manual(db: Session, manual: EquipmentManual, title: str | None) -> None:
    manual.title = title
    db.commit()


def delete_manual(db: Session, manual: EquipmentManual) -> None:
    delete_document(manual.file_path)
    db.delete(manual)
    db.commit()


def get_certificate(db: Session, item_id: int, certificate_id: int) -> EquipmentCertificate | None:
    stmt = select(EquipmentCertificate).where(
        EquipmentCertificate.id == certificate_id, EquipmentCertificate.item_id == item_id
    )
    return db.execute(stmt).scalar_one_or_none()


def create_certificate(
    db: Session,
    item: EquipmentItem,
    *,
    title: str | None,
    issued_at: date | None,
    expires_at: date | None,
    file_path: str,
    original_filename: str,
    file_size: int,
    uploaded_by_id: int | None,
) -> EquipmentCertificate:
    cert = EquipmentCertificate(
        item_id=item.id,
        title=title,
        issued_at=issued_at,
        expires_at=expires_at,
        file_path=file_path,
        original_filename=original_filename,
        file_size=file_size,
        uploaded_at=utcnow(),
        uploaded_by_id=uploaded_by_id,
    )
    db.add(cert)
    db.commit()
    db.refresh(cert)
    return cert


def rename_certificate(
    db: Session,
    cert: EquipmentCertificate,
    *,
    title: str | None,
    issued_at: date | None,
    expires_at: date | None,
) -> None:
    cert.title = title
    cert.issued_at = issued_at
    cert.expires_at = expires_at
    db.commit()


def delete_certificate(db: Session, cert: EquipmentCertificate) -> None:
    delete_document(cert.file_path)
    db.delete(cert)
    db.commit()
