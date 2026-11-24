"""Tickets: listado con filtros y permisos por área, creación, edición, transiciones,
comentarios, historial y traslado a orden de trabajo."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.permissions import visible_tickets_filter
from app.core.security import Principal, get_principal, require_roles
from app.models.core import Priority, Ticket, TicketType, WorkflowState
from app.schemas.core import (
    CommentIn,
    CommentOut,
    HistoryOut,
    StatsOut,
    TicketCreate,
    TicketOut,
    TicketPage,
    TicketPatch,
    TransitionIn,
    WorkOrderIn,
)
from app.services import tickets as svc

router = APIRouter(tags=["tickets"])

SortKey = Literal["created_at", "-created_at", "updated_at", "-updated_at", "priority", "-priority"]


def _csv(value: Optional[str]) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@router.get("/tickets", response_model=TicketPage)
def list_tickets(
    status_filter: Optional[str] = Query(
        default=None, alias="status", description="Claves de estado separadas por coma"
    ),
    category: Optional[str] = Query(
        default=None, description="Categorías de estado separadas por coma (todo, in_progress...)"
    ),
    priority: Optional[str] = Query(default=None, description="Claves de prioridad separadas por coma"),
    department_id: Optional[int] = None,
    team_id: Optional[int] = None,
    assignee_id: Optional[int] = None,
    requester_id: Optional[int] = None,
    category_id: Optional[int] = None,
    subcategory_id: Optional[int] = None,
    type_key: Optional[str] = Query(default=None, description="Claves de tipo separadas por coma"),
    unassigned: bool = False,
    mine: bool = Query(default=False, description="Solo tickets creados por mí o asignados a mí"),
    q: Optional[str] = Query(default=None, max_length=200),
    sort: SortKey = "-created_at",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    conditions = [visible_tickets_filter(principal)]
    status_keys = _csv(status_filter)
    if status_keys:
        conditions.append(
            Ticket.status_id.in_(select(WorkflowState.id).where(WorkflowState.key.in_(status_keys)))
        )
    categories = _csv(category)
    if categories:
        conditions.append(
            Ticket.status_id.in_(
                select(WorkflowState.id).where(WorkflowState.category.in_(categories))
            )
        )
    priority_keys = _csv(priority)
    if priority_keys:
        conditions.append(
            Ticket.priority_id.in_(select(Priority.id).where(Priority.key.in_(priority_keys)))
        )
    if department_id is not None:
        conditions.append(Ticket.department_id == department_id)
    if team_id is not None:
        conditions.append(Ticket.team_id == team_id)
    if assignee_id is not None:
        conditions.append(Ticket.assignee_id == assignee_id)
    if requester_id is not None:
        conditions.append(Ticket.requester_id == requester_id)
    if category_id is not None:
        conditions.append(Ticket.category_id == category_id)
    if subcategory_id is not None:
        conditions.append(Ticket.subcategory_id == subcategory_id)
    type_keys = _csv(type_key)
    if type_keys:
        conditions.append(Ticket.type_id.in_(select(TicketType.id).where(TicketType.key.in_(type_keys))))
    if unassigned:
        conditions.append(Ticket.assignee_id.is_(None))
    if mine:
        conditions.append(
            or_(Ticket.requester_id == principal.user_id, Ticket.assignee_id == principal.user_id)
        )
    if q and q.strip():
        term = q.strip()
        pattern = f"%{term}%"
        search = [
            Ticket.title.ilike(pattern),
            Ticket.description.ilike(pattern),
            Ticket.public_key.ilike(pattern),
        ]
        digits = term.upper().removeprefix("ESD-")
        if digits.isdigit():
            search.append(Ticket.id == int(digits))
        conditions.append(or_(*search))

    total = session.scalar(select(func.count()).select_from(Ticket).where(*conditions)) or 0

    stmt = select(Ticket).options(*svc.TICKET_LOAD_OPTIONS).where(*conditions)
    descending = sort.startswith("-")
    field = sort.lstrip("-")
    if field == "priority":
        stmt = stmt.join(Priority, Priority.id == Ticket.priority_id)
        order_col = Priority.rank
    elif field == "updated_at":
        order_col = Ticket.updated_at
    else:
        order_col = Ticket.created_at
    stmt = stmt.order_by(order_col.desc() if descending else order_col.asc(), Ticket.id.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    tickets = session.scalars(stmt).unique().all()
    return {
        "items": svc.serialize_tickets(session, tickets),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/tickets", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
def create_ticket(
    payload: TicketCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    ticket = svc.create_ticket(
        session,
        principal,
        title=payload.title,
        description=payload.description,
        type_key=payload.type_key,
        priority_key=payload.priority_key,
        department_id=payload.department_id,
        project_id=payload.project_id,
        category_id=payload.category_id,
        subcategory_id=payload.subcategory_id,
        provider_id=payload.provider_id,
        channel=payload.channel,
    )
    session.commit()
    return svc.ticket_out(session, ticket.id)


@router.get("/tickets/{ticket_id}", response_model=TicketOut)
def get_ticket(
    ticket_id: int,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    ticket = svc.get_visible_ticket(session, principal, ticket_id)
    return svc.ticket_out(session, ticket.id)


@router.patch("/tickets/{ticket_id}", response_model=TicketOut)
def patch_ticket(
    ticket_id: int,
    payload: TicketPatch,
    principal: Principal = Depends(require_roles("agent")),
    session: Session = Depends(get_db),
) -> dict:
    ticket = svc.get_visible_ticket(session, principal, ticket_id, for_update=True)
    svc.update_ticket(session, principal, ticket, payload.model_dump(exclude_unset=True))
    session.commit()
    return svc.ticket_out(session, ticket.id)


@router.post("/tickets/{ticket_id}/transition", response_model=TicketOut)
def transition_ticket(
    ticket_id: int,
    payload: TransitionIn,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    ticket = svc.get_visible_ticket(session, principal, ticket_id, for_update=True)
    svc.transition_ticket(
        session,
        principal,
        ticket,
        payload.to_state_key,
        payload.comment,
        resolution_note=payload.resolution_note,
    )
    session.commit()
    return svc.ticket_out(session, ticket.id)


@router.post(
    "/tickets/{ticket_id}/work-order", response_model=TicketOut, status_code=status.HTTP_201_CREATED
)
def create_work_order(
    ticket_id: int,
    payload: WorkOrderIn,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    """Traslada el ticket a una orden de trabajo (ticket hijo ``work_order``)."""
    parent = svc.get_visible_ticket(session, principal, ticket_id, for_update=True)
    child = svc.create_work_order(
        session,
        principal,
        parent,
        title=payload.title,
        description=payload.description,
        category_id=payload.category_id,
        subcategory_id=payload.subcategory_id,
        resolve_parent=payload.resolve_parent,
        resolution_note=payload.resolution_note,
    )
    session.commit()
    return svc.ticket_out(session, child.id)


@router.get("/tickets/{ticket_id}/comments", response_model=list[CommentOut])
def list_comments(
    ticket_id: int,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[dict]:
    ticket = svc.get_visible_ticket(session, principal, ticket_id)
    return svc.list_comments(session, principal, ticket)


@router.post(
    "/tickets/{ticket_id}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED
)
def add_comment(
    ticket_id: int,
    payload: CommentIn,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    ticket = svc.get_visible_ticket(session, principal, ticket_id, for_update=True)
    comment = svc.add_comment(session, principal, ticket, payload.body, payload.visibility)
    session.commit()
    session.refresh(comment)
    return svc.serialize_comment(comment)


@router.get("/tickets/{ticket_id}/history", response_model=list[HistoryOut])
def ticket_history(
    ticket_id: int,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[dict]:
    ticket = svc.get_visible_ticket(session, principal, ticket_id)
    return svc.list_history(session, principal, ticket)


@router.get("/stats", response_model=StatsOut)
def stats(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> dict:
    """Conteos de tickets visibles por estado, categoría y prioridad (dashboard)."""
    visible = visible_tickets_filter(principal)
    by_state = session.execute(
        select(WorkflowState.key, WorkflowState.category, func.count(Ticket.id))
        .join(Ticket, Ticket.status_id == WorkflowState.id)
        .where(visible)
        .group_by(WorkflowState.key, WorkflowState.category)
    ).all()
    by_priority = session.execute(
        select(Priority.key, func.count(Ticket.id))
        .join(Ticket, Ticket.priority_id == Priority.id)
        .where(visible)
        .group_by(Priority.key)
    ).all()
    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for key, cat, count in by_state:
        by_status[key] = by_status.get(key, 0) + count
        by_category[cat] = by_category.get(cat, 0) + count
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "by_priority": {key: count for key, count in by_priority},
        "by_category": by_category,
    }
