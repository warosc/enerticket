"""Plantillas del generador de reportes (migración ``0007_report_templates``).

- ``definition``: ``ReportDefinition`` serializada (ver ``app.services.reports.builder``).
- ``owner_id`` nulo = plantilla sin dueño (las de fábrica, ``is_system=True``).
- Compartir: ``shared_roles`` (roles de realm) y ``shared_department_ids`` (departamentos del
  usuario: equipos de agente o departamento del AD). Las de sistema las ven todos.
- ``schedule``: envío programado ``{enabled, frequency: monthly|weekly, day, hour, recipients}``;
  ``next_run_at`` lo calcula la API (hora de Guatemala) y lo avanza la tarea ``reports.run_scheduled``;
  ``retry_count`` cuenta los reintentos tras un error (migración ``0008_phase3_fixes``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TZDateTime, utcnow
from app.models.core import User


class ReportTemplate(Base):
    __tablename__ = "report_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    shared_roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    shared_department_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    schedule: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True, index=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    last_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    # Reintentos consecutivos tras un fallo transitorio del envío programado (0 = ninguno).
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)

    owner: Mapped[Optional[User]] = relationship()

    def __repr__(self) -> str:
        return f"<ReportTemplate {self.id} {self.name}>"
