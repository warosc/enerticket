"""Búsqueda de usuarios (agentes y administradores)."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.db import get_db
from app.core.security import Principal, require_roles
from app.models.core import User
from app.schemas.core import UserOut

router = APIRouter(tags=["usuarios"])


@router.get("/users", response_model=list[UserOut])
def list_users(
    q: Optional[str] = Query(default=None, max_length=100),
    role: Optional[Literal["admin", "agent", "approver", "requester"]] = None,
    include_inactive: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    _: Principal = Depends(require_roles("agent")),
    session: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(User).order_by(User.display_name, User.id).limit(limit)
    if not include_inactive:
        stmt = stmt.where(User.active.is_(True))
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                User.username.ilike(pattern),
                User.display_name.ilike(pattern),
                User.email.ilike(pattern),
            )
        )
    if role:
        # roles es una lista JSON; la búsqueda textual es portable (PostgreSQL y SQLite).
        stmt = stmt.where(cast(User.roles, String).like(f'%"{role}"%'))
    return [user_out(u) for u in session.scalars(stmt.options(joinedload(User.department))).all()]


def user_out(user: User) -> dict:
    """``UserOut``: datos de la cuenta, su departamento (AD o manual) y VIP."""
    department = user.department
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "roles": list(user.roles or []),
        "active": user.active,
        "department": {"id": department.id, "name": department.name} if department else None,
        "vip": bool(user.vip),
    }
