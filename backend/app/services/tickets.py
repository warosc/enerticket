"""Lógica de negocio de tickets: creación, edición, transiciones, comentarios y serialización.

Otros módulos pueden reutilizar estas funciones (p.ej. flows al aprobar una transición).
Las funciones no hacen commit: el llamador decide cuándo confirmar la transacción.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.orm import Session, joinedload

from app.core.db import utcnow
from app.core.events import collect_enrichments, emit, run_guards
from app.core.permissions import (
    can_edit_ticket,
    can_requester_transition,
    can_view_internal_comments,
    can_view_ticket,
)
from app.core.security import ROLE_ADMIN, ROLE_AGENT, Principal
from app.models.core import (
    Department,
    Priority,
    Project,
    Team,
    Ticket,
    TicketComment,
    TicketHistory,
    TicketType,
    User,
    WorkflowDefinition,
    WorkflowState,
    WorkflowTransition,
)

TICKET_LOAD_OPTIONS = (
    joinedload(Ticket.status),
    joinedload(Ticket.priority),
    joinedload(Ticket.type),
    joinedload(Ticket.department),
    joinedload(Ticket.team),
    joinedload(Ticket.project),
    joinedload(Ticket.requester),
    joinedload(Ticket.assignee),
)

EVENT_CREATED = "ticket.created"
EVENT_UPDATED = "ticket.updated"
EVENT_ASSIGNED = "ticket.assigned"
EVENT_TRANSITIONED = "ticket.transitioned"
EVENT_COMMENTED = "ticket.commented"
GUARD_TRANSITION = "ticket.transition"


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Ticket no encontrado")


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


# --------------------------------------------------------------------------- serialización


def _user_ref(user: User | None) -> dict[str, Any] | None:
    return {"id": user.id, "display_name": user.display_name} if user else None


def serialize_ticket(ticket: Ticket) -> dict[str, Any]:
    """Serializa a ``TicketOut`` (sin enriquecedores)."""
    return {
        "id": ticket.id,
        "key": ticket.key,
        "title": ticket.title,
        "description": ticket.description or "",
        "status": {
            "id": ticket.status.id,
            "key": ticket.status.key,
            "name": ticket.status.name,
            "category": ticket.status.category,
        },
        "priority": {
            "id": ticket.priority.id,
            "key": ticket.priority.key,
            "name": ticket.priority.name,
            "rank": ticket.priority.rank,
        },
        "type": {"id": ticket.type.id, "key": ticket.type.key, "name": ticket.type.name},
        "department": {"id": ticket.department.id, "name": ticket.department.name},
        "team": {"id": ticket.team.id, "name": ticket.team.name} if ticket.team else None,
        "project": (
            {"id": ticket.project.id, "code": ticket.project.code, "name": ticket.project.name}
            if ticket.project
            else None
        ),
        "requester": _user_ref(ticket.requester),
        "assignee": _user_ref(ticket.assignee),
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
        "first_response_at": ticket.first_response_at,
        "resolved_at": ticket.resolved_at,
        "closed_at": ticket.closed_at,
    }


def serialize_tickets(session: Session, tickets: Sequence[Ticket]) -> list[dict[str, Any]]:
    """Serializa tickets aplicando los enriquecedores registrados (``register_ticket_enricher``)."""
    extras = collect_enrichments(session, tickets)
    result = []
    for ticket in tickets:
        item = serialize_ticket(ticket)
        for field_name, value in extras.get(ticket.id, {}).items():
            item.setdefault(field_name, value)
        result.append(item)
    return result


def load_ticket(session: Session, ticket_id: int) -> Ticket | None:
    """Carga un ticket con sus relaciones (refresca valores de la BD)."""
    stmt = (
        select(Ticket)
        .options(*TICKET_LOAD_OPTIONS)
        .where(Ticket.id == ticket_id)
        .execution_options(populate_existing=True)
    )
    return session.scalars(stmt).unique().first()


def ticket_out(session: Session, ticket_id: int) -> dict[str, Any]:
    ticket = load_ticket(session, ticket_id)
    if ticket is None:
        raise _not_found()
    return serialize_tickets(session, [ticket])[0]


def snapshot(ticket: Ticket) -> dict[str, Any]:
    """Estado editable del ticket para ``ticket_history`` (before/after)."""
    return {
        "title": ticket.title,
        "description": ticket.description,
        "priority": ticket.priority.key if ticket.priority else None,
        "status": ticket.status.key if ticket.status else None,
        "type": ticket.type.key if ticket.type else None,
        "department_id": ticket.department_id,
        "team_id": ticket.team_id,
        "assignee_id": ticket.assignee_id,
        "project_id": ticket.project_id,
    }


# --------------------------------------------------------------------------- acceso


def get_visible_ticket(
    session: Session, principal: Principal, ticket_id: int, *, for_update: bool = False
) -> Ticket:
    """Devuelve el ticket si existe y es visible; si no, 404 (no revela su existencia)."""
    stmt: Select[tuple[Ticket]] = select(Ticket).where(Ticket.id == ticket_id)
    if for_update:
        stmt = stmt.with_for_update()
    ticket = session.scalars(stmt).first()
    if ticket is None or not can_view_ticket(session, principal, ticket):
        raise _not_found()
    return ticket


def get_default_workflow(session: Session) -> WorkflowDefinition:
    workflow = session.scalars(
        select(WorkflowDefinition).order_by(
            WorkflowDefinition.is_default.desc(), WorkflowDefinition.id
        )
    ).first()
    if workflow is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No hay flujo de trabajo configurado (ejecute los seeds)",
        )
    return workflow


def get_initial_state(session: Session, workflow: WorkflowDefinition) -> WorkflowState:
    state = session.scalars(
        select(WorkflowState)
        .where(WorkflowState.workflow_id == workflow.id)
        .order_by(WorkflowState.is_initial.desc(), WorkflowState.order, WorkflowState.id)
    ).first()
    if state is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="El flujo de trabajo no tiene estados"
        )
    return state


def default_team_for_department(session: Session, department_id: int) -> Team | None:
    return session.scalars(
        select(Team).where(Team.department_id == department_id).order_by(Team.id)
    ).first()


def _priority_by_key(session: Session, key: str) -> Priority:
    priority = session.scalar(select(Priority).where(Priority.key == key))
    if priority is None:
        raise _unprocessable(f"Prioridad desconocida: {key}")
    return priority


def _validate_assignee(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None or not user.active:
        raise _unprocessable("El usuario asignado no existe o está inactivo")
    roles = set(user.roles or [])
    if not roles.intersection({ROLE_AGENT, ROLE_ADMIN}):
        raise _unprocessable("El usuario asignado debe tener rol de agente")
    return user


def _validate_team(session: Session, team_id: int, department_id: int) -> Team:
    team = session.get(Team, team_id)
    if team is None:
        raise _unprocessable("El equipo no existe")
    if team.department_id != department_id:
        raise _unprocessable("El equipo no pertenece al departamento del ticket")
    return team


# --------------------------------------------------------------------------- operaciones


def create_ticket(
    session: Session,
    principal: Principal,
    *,
    title: str,
    description: str = "",
    type_key: str = "incident",
    priority_key: str = "medium",
    department_id: int,
    project_id: int | None = None,
) -> Ticket:
    """Crea un ticket en el estado inicial del flujo por defecto y emite ``ticket.created``."""
    ticket_type = session.scalar(select(TicketType).where(TicketType.key == type_key))
    if ticket_type is None:
        raise _unprocessable(f"Tipo de ticket desconocido: {type_key}")
    priority = _priority_by_key(session, priority_key)
    department = session.get(Department, department_id)
    if department is None:
        raise _unprocessable("El departamento no existe")
    project = None
    if project_id is not None:
        project = session.get(Project, project_id)
        if project is None:
            raise _unprocessable("El proyecto no existe")
    workflow = get_default_workflow(session)
    state = get_initial_state(session, workflow)
    team = default_team_for_department(session, department.id)

    now = utcnow()
    ticket = Ticket(
        title=title,
        description=description or "",
        type_id=ticket_type.id,
        priority_id=priority.id,
        status_id=state.id,
        department_id=department.id,
        team_id=team.id if team else None,
        project_id=project.id if project else None,
        requester_id=principal.user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(ticket)
    session.flush()
    ticket.public_key = f"ESD-{ticket.id}"
    session.flush()
    session.expire(ticket)
    emit(session, EVENT_CREATED, ticket, principal.user_id, {"after": snapshot(ticket)})
    return ticket


def update_ticket(
    session: Session, principal: Principal, ticket: Ticket, changes: dict[str, Any]
) -> Ticket:
    """Aplica cambios de PATCH (solo claves presentes). Emite ``ticket.updated`` / ``ticket.assigned``."""
    if not can_edit_ticket(principal, ticket):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No puede editar este ticket")
    before = snapshot(ticket)
    old_assignee = ticket.assignee_id

    if "title" in changes and changes["title"] is not None:
        ticket.title = changes["title"]
    if "description" in changes and changes["description"] is not None:
        ticket.description = changes["description"]
    if changes.get("priority_key") is not None:
        ticket.priority_id = _priority_by_key(session, changes["priority_key"]).id

    department_id = ticket.department_id
    if changes.get("department_id") is not None and changes["department_id"] != ticket.department_id:
        if session.get(Department, changes["department_id"]) is None:
            raise _unprocessable("El departamento no existe")
        department_id = changes["department_id"]
        ticket.department_id = department_id
        if "team_id" not in changes:
            default_team = default_team_for_department(session, department_id)
            ticket.team_id = default_team.id if default_team else None
    if "team_id" in changes:
        team_id = changes["team_id"]
        ticket.team_id = _validate_team(session, team_id, department_id).id if team_id else None
    elif ticket.team_id is not None:
        current_team = session.get(Team, ticket.team_id)
        if current_team is not None and current_team.department_id != department_id:
            ticket.team_id = None
    if "assignee_id" in changes:
        assignee_id = changes["assignee_id"]
        ticket.assignee_id = _validate_assignee(session, assignee_id).id if assignee_id else None

    session.flush()
    session.expire(ticket)
    after = snapshot(ticket)
    diff = {
        field: {"from": before[field], "to": after[field]}
        for field in after
        if before[field] != after[field]
    }
    if not diff:
        return ticket
    ticket.updated_at = utcnow()
    session.flush()
    emit(
        session,
        EVENT_UPDATED,
        ticket,
        principal.user_id,
        {"before": before, "after": after, "changes": diff},
    )
    if ticket.assignee_id != old_assignee:
        emit(
            session,
            EVENT_ASSIGNED,
            ticket,
            principal.user_id,
            {
                "before": {"assignee_id": old_assignee},
                "after": {"assignee_id": ticket.assignee_id},
                "from_assignee_id": old_assignee,
                "to_assignee_id": ticket.assignee_id,
            },
        )
    return ticket


def find_transition(
    session: Session, from_state: WorkflowState, to_state_key: str
) -> tuple[WorkflowState, WorkflowTransition]:
    """Busca el estado destino (mismo flujo) y la transición permitida; 422/409 si no aplica."""
    to_state = session.scalar(
        select(WorkflowState).where(
            WorkflowState.workflow_id == from_state.workflow_id, WorkflowState.key == to_state_key
        )
    )
    if to_state is None:
        raise _unprocessable(f"Estado desconocido: {to_state_key}")
    transition = session.scalar(
        select(WorkflowTransition).where(
            WorkflowTransition.workflow_id == from_state.workflow_id,
            WorkflowTransition.from_state_id == from_state.id,
            WorkflowTransition.to_state_id == to_state.id,
        )
    )
    if transition is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Transición no permitida: {from_state.key} → {to_state.key}",
        )
    return to_state, transition


def transition_ticket(
    session: Session,
    principal: Principal,
    ticket: Ticket,
    to_state_key: str,
    comment: str | None = None,
    *,
    extra_guard_data: dict[str, Any] | None = None,
) -> Ticket:
    """Cambia el estado validando ``workflow_transitions``, permisos y ``run_guards``.

    Datos que reciben los guards de ``ticket.transition``: ``transition_id``, ``transition_name``,
    ``requires_approval``, ``from_state``, ``to_state``, ``from_category``, ``to_category``,
    ``comment`` (+ ``extra_guard_data``).
    """
    from_state = session.get(WorkflowState, ticket.status_id)
    if from_state is None:  # pragma: no cover - integridad referencial
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Estado actual inválido")
    to_state, transition = find_transition(session, from_state, to_state_key)

    if not can_edit_ticket(principal, ticket) and not can_requester_transition(
        principal, ticket, from_state, to_state
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="No tiene permiso para esta transición"
        )

    guard_data: dict[str, Any] = {
        "transition_id": transition.id,
        "transition_name": transition.name,
        "requires_approval": transition.requires_approval,
        "from_state": from_state.key,
        "to_state": to_state.key,
        "from_category": from_state.category,
        "to_category": to_state.category,
        "comment": comment,
    }
    if extra_guard_data:
        guard_data.update(extra_guard_data)
    run_guards(session, GUARD_TRANSITION, ticket, principal, guard_data)

    now = utcnow()
    before = {
        "status": from_state.key,
        "resolved_at": ticket.resolved_at,
        "closed_at": ticket.closed_at,
    }
    ticket.status_id = to_state.id
    if to_state.category == "done":
        if ticket.resolved_at is None:
            ticket.resolved_at = now
        if to_state.is_final:
            ticket.closed_at = now
    elif from_state.category == "done":
        # Reapertura: se limpian las marcas de resolución/cierre.
        ticket.resolved_at = None
        ticket.closed_at = None
    if (
        ticket.first_response_at is None
        and from_state.is_initial
        and principal.is_agent
    ):
        ticket.first_response_at = now
    ticket.updated_at = now
    session.flush()
    session.expire(ticket)

    emit(
        session,
        EVENT_TRANSITIONED,
        ticket,
        principal.user_id,
        {
            "before": before,
            "after": {
                "status": to_state.key,
                "resolved_at": ticket.resolved_at,
                "closed_at": ticket.closed_at,
            },
            "transition_id": transition.id,
            "from_state": from_state.key,
            "to_state": to_state.key,
            "from_category": from_state.category,
            "to_category": to_state.category,
        },
    )
    if comment and comment.strip():
        add_comment(session, principal, ticket, comment.strip(), "public")
    return ticket


def add_comment(
    session: Session, principal: Principal, ticket: Ticket, body: str, visibility: str = "public"
) -> TicketComment:
    """Agrega un comentario. Solo agent/admin pueden crear comentarios ``internal``."""
    if visibility not in ("public", "internal"):
        raise _unprocessable("Visibilidad inválida")
    if visibility == "internal" and not can_view_internal_comments(principal):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Solo agentes pueden crear comentarios internos"
        )
    now = utcnow()
    comment = TicketComment(
        ticket_id=ticket.id,
        author_id=principal.user_id,
        body=body,
        visibility=visibility,
        created_at=now,
    )
    session.add(comment)
    if (
        visibility == "public"
        and ticket.first_response_at is None
        and principal.is_agent
        and principal.user_id != ticket.requester_id
    ):
        ticket.first_response_at = now
    ticket.updated_at = now
    session.flush()
    emit(
        session,
        EVENT_COMMENTED,
        ticket,
        principal.user_id,
        {
            "comment_id": comment.id,
            "visibility": visibility,
            "author_is_agent": principal.is_agent,
        },
    )
    return comment


def serialize_comment(comment: TicketComment) -> dict[str, Any]:
    return {
        "id": comment.id,
        "ticket_id": comment.ticket_id,
        "author": _user_ref(comment.author),
        "body": comment.body,
        "visibility": comment.visibility,
        "created_at": comment.created_at,
    }


def list_comments(session: Session, principal: Principal, ticket: Ticket) -> list[dict[str, Any]]:
    stmt = (
        select(TicketComment)
        .options(joinedload(TicketComment.author))
        .where(TicketComment.ticket_id == ticket.id)
        .order_by(TicketComment.created_at, TicketComment.id)
    )
    if not can_view_internal_comments(principal):
        stmt = stmt.where(TicketComment.visibility == "public")
    return [serialize_comment(c) for c in session.scalars(stmt).all()]


def _is_internal_history(entry: TicketHistory) -> bool:
    for payload in (entry.after_json, entry.before_json):
        if isinstance(payload, dict) and payload.get("visibility") == "internal":
            return True
    return False


def list_history(session: Session, principal: Principal, ticket: Ticket) -> list[dict[str, Any]]:
    """Historial del ticket; oculta eventos de comentarios internos a no agentes."""
    entries = session.scalars(
        select(TicketHistory)
        .options(joinedload(TicketHistory.actor))
        .where(TicketHistory.ticket_id == ticket.id)
        .order_by(TicketHistory.timestamp, TicketHistory.id)
    ).all()
    show_internal = can_view_internal_comments(principal)
    return [
        {
            "id": entry.id,
            "ticket_id": entry.ticket_id,
            "event_type": entry.event_type,
            "actor": _user_ref(entry.actor),
            "before": entry.before_json,
            "after": entry.after_json,
            "timestamp": entry.timestamp,
        }
        for entry in entries
        if show_internal or not _is_internal_history(entry)
    ]
