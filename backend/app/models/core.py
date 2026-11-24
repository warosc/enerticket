"""Tablas core de la fundación (migración ``0001_core``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TZDateTime, utcnow

TEAM_ROLES = ("member", "lead")
STATE_CATEGORIES = ("todo", "in_progress", "waiting", "done")
COMMENT_VISIBILITIES = ("public", "internal")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keycloak_sub: Mapped[str] = mapped_column(String(255), unique=True)
    username: Mapped[str] = mapped_column(String(150), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Últimos roles de realm conocidos (sincronizados en cada login); permite filtrar /users?role=.
    roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    # Departamento del usuario según el AD (claim ``department`` resuelto por alias) o asignado
    # por un administrador; VIP por pertenencia al grupo ``VIP_GROUP``.
    department_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    vip: Mapped[bool] = mapped_column(Boolean, default=False)
    # Último valor del AD (claim ``department``) normalizado: distingue un cambio real en el AD
    # (que manda) de un ajuste manual del administrador, y permite aplicar de inmediato la
    # asignación de un alias a quienes lo traen (migración ``0008_phase3_fixes``).
    ad_department_value: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)

    memberships: Mapped[list["TeamMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    department: Mapped[Optional["Department"]] = relationship(foreign_keys=[department_id])

    def __repr__(self) -> str:
        return f"<User {self.id} {self.username}>"


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    gerencia_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("gerencias.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # True = departamento de atención (recibe tickets); False = solo departamento solicitante.
    serves_tickets: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    parent: Mapped[Optional["Department"]] = relationship(remote_side=[id])
    gerencia: Mapped[Optional["Gerencia"]] = relationship("Gerencia")  # noqa: F821
    teams: Mapped[list["Team"]] = relationship(back_populates="department")


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("department_id", "name", name="uq_teams_department_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    external_group: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    department: Mapped[Department] = relationship(back_populates="teams")
    members: Mapped[list["TeamMember"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (CheckConstraint("role IN ('member', 'lead')", name="role_valid"),)

    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), default="member")

    team: Mapped[Team] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)

    owner: Mapped[Optional[User]] = relationship()
    team: Mapped[Optional[Team]] = relationship()


class TicketType(Base):
    __tablename__ = "ticket_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Priority(Base):
    __tablename__ = "priorities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    rank: Mapped[int] = mapped_column(Integer)


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    states: Mapped[list["WorkflowState"]] = relationship(
        back_populates="workflow", order_by="WorkflowState.order"
    )


class WorkflowState(Base):
    __tablename__ = "workflow_states"
    __table_args__ = (
        UniqueConstraint("workflow_id", "key", name="uq_workflow_states_workflow_key"),
        CheckConstraint(
            "category IN ('todo', 'in_progress', 'waiting', 'done')", name="category_valid"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflow_definitions.id"), index=True)
    key: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(20))
    order: Mapped[int] = mapped_column(Integer, default=0)
    is_initial: Mapped[bool] = mapped_column(Boolean, default=False)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)

    workflow: Mapped[WorkflowDefinition] = relationship(back_populates="states")


class WorkflowTransition(Base):
    __tablename__ = "workflow_transitions"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id", "from_state_id", "to_state_id", name="uq_workflow_transitions_path"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflow_definitions.id"), index=True)
    from_state_id: Mapped[int] = mapped_column(ForeignKey("workflow_states.id"))
    to_state_id: Mapped[int] = mapped_column(ForeignKey("workflow_states.id"))
    name: Mapped[str] = mapped_column(String(100))
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)

    from_state: Mapped[WorkflowState] = relationship(foreign_keys=[from_state_id])
    to_state: Mapped[WorkflowState] = relationship(foreign_keys=[to_state_id])


TICKET_CHANNELS = ("portal", "email", "phone", "other")


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint("channel IN ('portal', 'email', 'phone', 'other')", name="channel_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_key: Mapped[Optional[str]] = mapped_column(String(30), unique=True, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"), nullable=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("ticket_types.id"))
    priority_id: Mapped[int] = mapped_column(ForeignKey("priorities.id"))
    status_id: Mapped[int] = mapped_column(ForeignKey("workflow_states.id"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    team_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("teams.id"), nullable=True, index=True
    )
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)
    first_response_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    # --- Catálogo de servicio (0006_catalog) ---
    category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ticket_categories.id"), nullable=True, index=True
    )
    subcategory_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ticket_subcategories.id"), nullable=True, index=True
    )
    provider_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("providers.id"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), default="portal")
    vip: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    requester_department_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_ticket_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    business_resolution_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    project: Mapped[Optional[Project]] = relationship()
    type: Mapped[TicketType] = relationship()
    priority: Mapped[Priority] = relationship()
    status: Mapped[WorkflowState] = relationship()
    department: Mapped[Department] = relationship(foreign_keys=[department_id])
    team: Mapped[Optional[Team]] = relationship()
    requester: Mapped[User] = relationship(foreign_keys=[requester_id])
    assignee: Mapped[Optional[User]] = relationship(foreign_keys=[assignee_id])
    category: Mapped[Optional["TicketCategory"]] = relationship("TicketCategory")  # noqa: F821
    subcategory: Mapped[Optional["TicketSubcategory"]] = relationship("TicketSubcategory")  # noqa: F821
    provider: Mapped[Optional["Provider"]] = relationship("Provider")  # noqa: F821
    resolved_by: Mapped[Optional[User]] = relationship(foreign_keys=[resolved_by_id])
    requester_department: Mapped[Optional[Department]] = relationship(
        foreign_keys=[requester_department_id]
    )

    @property
    def key(self) -> str:
        return self.public_key or f"ESD-{self.id}"

    def __repr__(self) -> str:
        return f"<Ticket {self.key}>"


class TicketComment(Base):
    __tablename__ = "ticket_comments"
    __table_args__ = (
        CheckConstraint("visibility IN ('public', 'internal')", name="visibility_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(String(20), default="public")
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)

    author: Mapped[User] = relationship()


class TicketHistory(Base):
    __tablename__ = "ticket_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    before_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    after_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, index=True)

    actor: Mapped[Optional[User]] = relationship()
