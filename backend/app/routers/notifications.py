"""Bandeja de notificaciones in-app del usuario autenticado."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.db import get_db, utcnow
from app.core.security import Principal, get_principal
from app.models.automation import Notification
from app.services.notifications import serialize_notification

router = APIRouter(prefix="/notifications", tags=["notificaciones"])


class NotificationTicket(BaseModel):
    id: int
    key: str


class NotificationOut(BaseModel):
    id: int
    kind: str
    title: str
    body: str
    ticket: Optional[NotificationTicket] = None
    data: dict[str, Any]
    read: bool
    read_at: Optional[datetime] = None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    total: int
    unread: int
    page: int
    page_size: int


def _inbox(principal: Principal):
    return (Notification.user_id == principal.user_id, Notification.channel == "in_app")


def _unread_count(session: Session, principal: Principal) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(*_inbox(principal), Notification.is_read.is_(False))
        )
        or 0
    )


@router.get("", response_model=NotificationPage)
def list_notifications(
    unread: bool = Query(default=False, description="Solo no leídas"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    conditions = list(_inbox(principal))
    if unread:
        conditions.append(Notification.is_read.is_(False))
    total = session.scalar(select(func.count()).select_from(Notification).where(*conditions)) or 0
    rows = session.scalars(
        select(Notification)
        .where(*conditions)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [serialize_notification(n) for n in rows],
        "total": total,
        "unread": _unread_count(session, principal),
        "page": page,
        "page_size": page_size,
    }


@router.get("/unread-count")
def unread_count(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> dict:
    return {"count": _unread_count(session, principal)}


@router.post("/read-all")
def mark_all_read(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> dict:
    result = session.execute(
        update(Notification)
        .where(*_inbox(principal), Notification.is_read.is_(False))
        .values(is_read=True, read_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return {"updated": result.rowcount or 0, "unread": 0}


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: int,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    notification = session.get(Notification, notification_id)
    if (
        notification is None
        or notification.user_id != principal.user_id
        or notification.channel != "in_app"
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Notificación no encontrada")
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = utcnow()
        session.commit()
    return serialize_notification(notification)
