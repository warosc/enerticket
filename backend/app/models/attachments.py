"""Adjuntos de tickets (migración ``0004_attachments``).

El contenido vive en el almacenamiento de objetos (MinIO/S3); aquí solo se guardan los
metadatos. ``object_key`` es aleatorio (nunca deriva del nombre del archivo del usuario)
y ``filename`` es el nombre original ya saneado, usado solo para la descarga.
El borrado es lógico (``deleted_at``): el objeto se conserva para auditoría.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TZDateTime, utcnow
from app.models.core import Ticket, User


class TicketAttachment(Base):
    __tablename__ = "ticket_attachments"
    __table_args__ = (
        CheckConstraint("size >= 0", name="size_non_negative"),
        Index("ix_ticket_attachments_ticket_id_deleted_at", "ticket_id", "deleted_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"))
    object_key: Mapped[str] = mapped_column(String(255), unique=True)
    filename: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str] = mapped_column(String(150))
    checksum: Mapped[str] = mapped_column(String(64))
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)

    ticket: Mapped[Ticket] = relationship()
    uploader: Mapped[User] = relationship()

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def __repr__(self) -> str:
        return f"<TicketAttachment {self.id} ticket={self.ticket_id} {self.filename!r}>"
