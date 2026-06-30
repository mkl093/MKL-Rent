"""Модель сгенерированного документа (ТЗ §26.6, §39).

На сервере хранится только последняя версия каждого сочетания
(проект, тип документа, язык). Повторная генерация заменяет файл.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GeneratedDocument(Base):
    __tablename__ = "generated_documents"
    __table_args__ = (
        UniqueConstraint("project_id", "doc_type", "language", name="uq_document_combo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    # Хеш исходных данных на момент генерации — для пометки «устарел» (ТЗ §26.6).
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
