"""Catálogo de servicio (migración ``0006_catalog``).

- ``gerencias``: agrupan departamentos; ``external_group`` (grupo AD) da visibilidad de solo
  lectura sobre los tickets SOLICITADOS por departamentos de la gerencia.
- ``department_aliases``: valores del atributo AD ``department`` normalizados y su
  departamento (``department_id`` nulo = pendiente de asignar por el administrador).
- ``ticket_categories`` / ``ticket_subcategories``: clasificación por departamento de atención.
- ``providers``: proveedores externos (``department_ids`` vacío = aplica a todos).
- ``gerencia_viewers``: asignación manual de usuarios que ven los tickets de una gerencia.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TZDateTime, utcnow
from app.models.core import Department


class Gerencia(Base):
    __tablename__ = "gerencias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    external_group: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    def __repr__(self) -> str:
        return f"<Gerencia {self.id} {self.name}>"


class DepartmentAlias(Base):
    __tablename__ = "department_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    normalized_value: Mapped[str] = mapped_column(String(200), unique=True)
    raw_value: Mapped[str] = mapped_column(String(200))
    department_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)

    department: Mapped[Optional[Department]] = relationship()


class TicketCategory(Base):
    __tablename__ = "ticket_categories"
    __table_args__ = (
        UniqueConstraint("department_id", "name", name="uq_ticket_categories_department_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    # Claves de tipo de ticket admitidas; lista vacía = todos los tipos.
    type_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    department: Mapped[Department] = relationship()
    subcategories: Mapped[list["TicketSubcategory"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="TicketSubcategory.sort_order",
    )

    def admits_type(self, type_key: str | None) -> bool:
        return not self.type_keys or type_key in self.type_keys


class TicketSubcategory(Base):
    __tablename__ = "ticket_subcategories"
    __table_args__ = (
        UniqueConstraint("category_id", "name", name="uq_ticket_subcategories_category_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("ticket_categories.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(150))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    category: Mapped[TicketCategory] = relationship(back_populates="subcategories")


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    # Departamentos de atención donde aplica; lista vacía = todos.
    department_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    def applies_to(self, department_id: int | None) -> bool:
        return not self.department_ids or department_id in self.department_ids


class GerenciaViewer(Base):
    __tablename__ = "gerencia_viewers"

    gerencia_id: Mapped[int] = mapped_column(
        ForeignKey("gerencias.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
