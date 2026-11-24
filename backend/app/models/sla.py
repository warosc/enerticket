"""Tablas del módulo SLA (migración ``0002_sla``).

- ``business_calendars``: zona horaria y ventanas de servicio por día de la semana.
- ``calendar_holidays``: feriados de cada calendario (fecha fija o recurrente cada año).
- ``sla_policies``: condiciones de aplicación (prioridad/tipo/departamento/categoría/
  subcategoría/VIP) con precedencia,
  objetivos de primera respuesta y resolución en minutos hábiles, umbral de advertencia y
  estados/categorías que pausan el reloj.
- ``sla_instances``: reloj de una métrica (first_response | resolution) para un ticket.
- ``sla_events``: bitácora de cada instancia (inicio, pausa, reanudación, cumplimiento...).
"""

from __future__ import annotations

import datetime as dt
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TZDateTime, utcnow
from app.models.core import Ticket

SLA_METRICS = ("first_response", "resolution")
SLA_STATUSES = ("running", "paused", "met", "breached", "cancelled")
DEFAULT_TIMEZONE = "America/Guatemala"


class BusinessCalendar(Base):
    __tablename__ = "business_calendars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    timezone: Mapped[str] = mapped_column(String(64), default=DEFAULT_TIMEZONE)
    # Lista de ventanas: [{"weekday": 0..6 (0 = lunes), "start": "HH:MM", "end": "HH:MM" | "24:00"}]
    windows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)

    holidays: Mapped[list["CalendarHoliday"]] = relationship(
        back_populates="calendar", cascade="all, delete-orphan", order_by="CalendarHoliday.date"
    )

    def __repr__(self) -> str:
        return f"<BusinessCalendar {self.id} {self.name}>"


class CalendarHoliday(Base):
    __tablename__ = "calendar_holidays"
    __table_args__ = (
        UniqueConstraint("calendar_id", "date", name="uq_calendar_holidays_calendar_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(
        ForeignKey("business_calendars.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[dt.date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(150))
    # Recurrente: se repite cada año en el mismo día y mes (p.ej. 15 de septiembre).
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)

    calendar: Mapped[BusinessCalendar] = relationship(back_populates="holidays")


class SlaPolicy(Base):
    __tablename__ = "sla_policies"
    __table_args__ = (
        CheckConstraint(
            "warning_threshold_pct >= 1 AND warning_threshold_pct <= 99",
            name="warning_threshold_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    calendar_id: Mapped[int] = mapped_column(ForeignKey("business_calendars.id"), index=True)
    # Condiciones de aplicación: lista vacía = cualquiera. Todas deben cumplirse (AND).
    priority_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    type_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    department_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    # Catálogo de servicio (0006_catalog): categorías/subcategorías del ticket y solo VIP.
    category_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    subcategory_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    vip_only: Mapped[bool] = mapped_column(Boolean, default=False)
    # Menor valor = se evalúa antes. Gana la primera política activa que cumpla las condiciones.
    precedence: Mapped[int] = mapped_column(Integer, default=100)
    # Objetivos en minutos hábiles del calendario (None = sin objetivo para esa métrica).
    first_response_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resolution_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    warning_threshold_pct: Mapped[int] = mapped_column(Integer, default=75)
    # Categorías (todo, in_progress, waiting) y claves de estado que pausan el reloj.
    pause_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    pause_states: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)

    calendar: Mapped[BusinessCalendar] = relationship()

    def __repr__(self) -> str:
        return f"<SlaPolicy {self.id} {self.name}>"


class SlaInstance(Base):
    __tablename__ = "sla_instances"
    __table_args__ = (
        CheckConstraint("metric IN ('first_response', 'resolution')", name="metric_valid"),
        CheckConstraint(
            "status IN ('running', 'paused', 'met', 'breached', 'cancelled')", name="status_valid"
        ),
        Index("ix_sla_instances_ticket_metric", "ticket_id", "metric"),
        Index("ix_sla_instances_status_target", "status", "target_at"),
        Index("ix_sla_instances_status_warning", "status", "warning_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"))
    policy_id: Mapped[int] = mapped_column(ForeignKey("sla_policies.id"), index=True)
    # Calendario y objetivo con los que se calculó (instantánea: editar la política no
    # altera instancias en curso hasta que se recalculan).
    calendar_id: Mapped[int] = mapped_column(ForeignKey("business_calendars.id"))
    metric: Mapped[str] = mapped_column(String(30))
    target_minutes: Mapped[int] = mapped_column(Integer)
    warning_threshold_pct: Mapped[int] = mapped_column(Integer, default=75)
    started_at: Mapped[datetime] = mapped_column(TZDateTime())
    target_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    warning_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    paused_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    paused_business_seconds: Mapped[int] = mapped_column(Integer, default=0)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    breached_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    warned_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)

    ticket: Mapped[Ticket] = relationship()
    policy: Mapped[SlaPolicy] = relationship()
    calendar: Mapped[BusinessCalendar] = relationship()
    events: Mapped[list["SlaEvent"]] = relationship(
        back_populates="instance", cascade="all, delete-orphan", order_by="SlaEvent.id"
    )

    def __repr__(self) -> str:
        return f"<SlaInstance {self.id} ticket={self.ticket_id} {self.metric} {self.status}>"


class SlaEvent(Base):
    __tablename__ = "sla_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("sla_instances.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    instance: Mapped[SlaInstance] = relationship(back_populates="events")
