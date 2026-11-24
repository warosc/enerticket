"""Aprobaciones de transiciones (módulo flows, migración ``0003_flows``).

- ``approval_policies``: configuración (qué transición, para qué tipos/departamentos y con qué
  etapas de aprobadores). Si una transición tiene ``requires_approval`` y no hay políticas para
  ella se usa la política por defecto (aprobadores del departamento del ticket).
- ``approval_requests``: solicitud para ejecutar una transición concreta de un ticket.
- ``approval_steps``: etapas secuenciales (orden) con aprobador usuario / rol / equipo / líder
  de equipo y regla ``all`` | ``any``.
- ``approval_decisions``: una fila por aprobador elegible de cada etapa (``pending`` hasta que
  decide); se crean al abrir la solicitud para que la visibilidad y la bandeja sean consultas SQL.
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
from app.models.core import Ticket, User, WorkflowTransition

REQUEST_STATUSES = ("pending", "approved", "rejected", "cancelled")
STEP_STATUSES = ("waiting", "pending", "approved", "rejected", "cancelled")
DECISION_VALUES = ("pending", "approved", "rejected", "skipped")
APPROVER_TYPES = ("user", "role", "team", "team_lead")
STEP_RULES = ("all", "any")


class ApprovalPolicy(Base):
    __tablename__ = "approval_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    transition_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_transitions.id", ondelete="CASCADE"), index=True
    )
    # Filtros: lista vacía = aplica a todos.
    ticket_type_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    department_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    # Etapas: [{"name", "approver_type", "user_id"?, "role"?, "team_id"?, "scope"?, "rule"}]
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)

    transition: Mapped[WorkflowTransition] = relationship()


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')", name="status_valid"
        ),
        Index("ix_approval_requests_ticket_status", "ticket_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"))
    transition_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_transitions.id", ondelete="CASCADE")
    )
    policy_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("approval_policies.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    requested_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    # Ejecución automática de la transición tras la aprobación.
    executed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    execution_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    ticket: Mapped[Ticket] = relationship()
    transition: Mapped[WorkflowTransition] = relationship()
    requested_by: Mapped[Optional[User]] = relationship()
    steps: Mapped[list["ApprovalStep"]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="ApprovalStep.order",
    )


class ApprovalStep(Base):
    __tablename__ = "approval_steps"
    __table_args__ = (
        CheckConstraint(
            "status IN ('waiting', 'pending', 'approved', 'rejected', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint("rule IN ('all', 'any')", name="rule_valid"),
        CheckConstraint(
            "approver_type IN ('user', 'role', 'team', 'team_lead')", name="approver_type_valid"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="CASCADE"), index=True
    )
    order: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(150))
    approver_type: Mapped[str] = mapped_column(String(20))
    approver_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approver_role: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    approver_team_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    # Departamento usado para acotar aprobadores por rol (scope="department").
    approver_department_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    rule: Mapped[str] = mapped_column(String(10), default="any")
    status: Mapped[str] = mapped_column(String(20), default="waiting")
    decided_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)

    request: Mapped[ApprovalRequest] = relationship(back_populates="steps")
    approver_user: Mapped[Optional[User]] = relationship()
    decisions: Mapped[list["ApprovalDecision"]] = relationship(
        back_populates="step",
        cascade="all, delete-orphan",
        order_by="ApprovalDecision.id",
    )


class ApprovalDecision(Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected', 'skipped')", name="decision_valid"
        ),
        Index("ix_approval_decisions_approver_decision", "approver_id", "decision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    step_id: Mapped[int] = mapped_column(
        ForeignKey("approval_steps.id", ondelete="CASCADE"), index=True
    )
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    decision: Mapped[str] = mapped_column(String(20), default="pending")
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # True si la decisión la tomó un administrador sin ser aprobador designado.
    is_override: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    decided_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)

    step: Mapped[ApprovalStep] = relationship(back_populates="decisions")
    approver: Mapped[User] = relationship()
