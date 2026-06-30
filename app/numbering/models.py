"""Счётчики номеров документов (ТЗ §14).

Раздельные счётчики на тип документа и год, сбрасываются каждый календарный год.
"""

from __future__ import annotations

import enum

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DocType(enum.StrEnum):
    """Тип документа с авто-нумерацией."""

    PROJECT = "project"
    ESTIMATE = "estimate"
    PACKING = "packing"

    @property
    def prefix(self) -> str:
        return {"project": "PRJ", "estimate": "EST", "packing": "PL"}[self.value]


class SequenceCounter(Base):
    """Текущее значение счётчика для (тип документа, год)."""

    __tablename__ = "sequence_counters"
    __table_args__ = (UniqueConstraint("doc_type", "year", name="uq_sequence_doc_year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(20), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    last_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
