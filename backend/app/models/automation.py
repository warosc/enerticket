"""Automatizaciones y notificaciones (módulo flows, migración ``0003_flows``).

- ``automation_rules``: reglas evento → condiciones → acciones (JSON).
- ``automation_executions``: bitácora de cada regla que coincidió (resultado o error).
- ``notifications``: notificaciones por usuario. Canal ``in_app`` (bandeja/campana) y
  ``email`` (una fila por correo con su estado de envío y reintentos).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TZDateTime, utcnow
from app.models.core import Ticket, User

EXECUTION_STATUSES = ("success", "partial", "error")
NOTIFICATION_CHANNELS = ("in_app", "email")
DELIVERY_STATUSES = ("delivered", "pending", "sent", "failed", "skipped")


class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Tipo de evento que dispara la regla (p.ej. "ticket.created").
    trigger: Mapped[str] = mapped_column(String(80), index=True)
    # {"match": "all"|"any", "rules": [{"field", "op", "value"}]}
    conditions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # [{"type": "set_priority", "priority_key": "high"}, ...]
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    order: Mapped[int] = mapped_column(Integer, default=100)
    stop_processing: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)


class AutomationExecution(Base):
    __tablename__ = "automation_executions"
    __table_args__ = (
        CheckConstraint("status IN ('success', 'partial', 'error')", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("automation_rules.id", ondelete="CASCADE"), index=True
    )
    ticket_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(80))
    history_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20))
    # [{"type", "ok", "detail"}]
    result: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)

    rule: Mapped[AutomationRule] = relationship()
    ticket: Mapped[Optional[Ticket]] = relationship()


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("channel IN ('in_app', 'email')", name="channel_valid"),
        CheckConstraint(
            "delivery_status IN ('delivered', 'pending', 'sent', 'failed', 'skipped')",
            name="delivery_status_valid",
        ),
        Index("ix_notifications_inbox", "user_id", "channel", "is_read"),
        Index("ix_notifications_delivery", "channel", "delivery_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(250))
    body: Mapped[str] = mapped_column(Text, default="")
    ticket_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="in_app")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(20), default="delivered")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)

    user: Mapped[User] = relationship()
    ticket: Mapped[Optional[Ticket]] = relationship()
