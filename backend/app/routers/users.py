"""Búsqueda de usuarios (agentes y administradores)."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

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
    return [
        {
            "id": u.id,
            "username": u.username,
            "display_name": u.display_name,
            "email": u.email,
            "roles": list(u.roles or []),
            "active": u.active,
        }
        for u in session.scalars(stmt).all()
    ]
