"""Reglas de visibilidad y edición de tickets por área (departamento/equipo).

- admin: ve y edita todo.
- agent: ve tickets de sus equipos o departamentos, asignados a él o creados por él;
  edita/transiciona los de su área (equipo, departamento) o asignados a él.
- comentarios ``internal``: admin y agentes que atienden el ticket (misma regla que
  editar); ver el ticket como solicitante o aprobador no da acceso a las notas internas.
- requester: ve solo los propios; comenta y puede cerrar/reabrir los suyos.
- Otros módulos amplían la visibilidad con ``register_visibility_extension``
  (p.ej. flows: aprobadores ven tickets con aprobaciones dirigidas a ellos).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement, and_, false, or_, select, true
from sqlalchemy.orm import Session

from app.core.security import Principal
from app.models.core import Ticket, WorkflowState

logger = logging.getLogger(__name__)

VisibilityExtension = Callable[[Principal], "ColumnElement[bool] | None"]
_visibility_extensions: list[VisibilityExtension] = []


def register_visibility_extension(fn: VisibilityExtension) -> VisibilityExtension:
    """Registra ``fn(principal) -> ColumnElement[bool] | None`` que amplía la visibilidad.

    La expresión devuelta se combina con OR sobre ``Ticket`` (puede usar subconsultas,
    p.ej. ``Ticket.id.in_(select(...))``). Devolver ``None`` = sin ampliación.
    Se puede usar como decorador.
    """
    if fn not in _visibility_extensions:
        _visibility_extensions.append(fn)
    return fn


def unregister_visibility_extension(fn: VisibilityExtension) -> None:
    if fn in _visibility_extensions:
        _visibility_extensions.remove(fn)


def _extension_clauses(principal: Principal) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []
    for extension in list(_visibility_extensions):
        clause = extension(principal)
        if clause is not None:
            clauses.append(clause)
    return clauses


def _base_scope_clauses(principal: Principal) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = [
        Ticket.requester_id == principal.user_id,
        Ticket.assignee_id == principal.user_id,
    ]
    if principal.is_agent:
        if principal.team_ids:
            clauses.append(Ticket.team_id.in_(sorted(principal.team_ids)))
        if principal.department_ids:
            clauses.append(Ticket.department_id.in_(sorted(principal.department_ids)))
    return clauses


def visible_tickets_filter(principal: Principal) -> ColumnElement[bool]:
    """Expresión WHERE sobre ``Ticket`` con los tickets que ``principal`` puede ver."""
    if principal.is_admin:
        return true()
    clauses = _base_scope_clauses(principal) + _extension_clauses(principal)
    return or_(*clauses) if clauses else false()


def can_view_scope(
    principal: Principal,
    *,
    department_id: int | None,
    team_id: int | None,
    requester_id: int | None,
    assignee_id: int | None,
) -> bool:
    """Regla base de visibilidad sin BD (sin extensiones); útil para filtrar eventos."""
    if principal.is_admin:
        return True
    if principal.user_id in (requester_id, assignee_id):
        return True
    if principal.is_agent:
        if team_id is not None and team_id in principal.team_ids:
            return True
        if department_id is not None and department_id in principal.department_ids:
            return True
    return False


def can_view_event(principal: Principal, payload: dict[str, Any], session: Session | None = None) -> bool:
    """¿Puede ``principal`` recibir este evento? Usa los campos de alcance del payload.

    Si la regla base no lo permite y se pasa ``session``, se consultan las extensiones
    de visibilidad sobre el ticket del evento.
    """
    if can_view_scope(
        principal,
        department_id=payload.get("department_id"),
        team_id=payload.get("team_id"),
        requester_id=payload.get("requester_id"),
        assignee_id=payload.get("assignee_id"),
    ):
        return True
    ticket_id = payload.get("ticket_id")
    if session is None or ticket_id is None or not _visibility_extensions:
        return False
    return _ticket_matches(session, principal, int(ticket_id))


def _ticket_matches(session: Session, principal: Principal, ticket_id: int) -> bool:
    stmt = select(Ticket.id).where(and_(Ticket.id == ticket_id, visible_tickets_filter(principal)))
    return session.scalar(stmt) is not None


def can_view_ticket(session: Session, principal: Principal, ticket: Ticket) -> bool:
    """¿Puede ``principal`` ver el ticket? (regla base + extensiones registradas)."""
    if can_view_scope(
        principal,
        department_id=ticket.department_id,
        team_id=ticket.team_id,
        requester_id=ticket.requester_id,
        assignee_id=ticket.assignee_id,
    ):
        return True
    if not _visibility_extensions or ticket.id is None:
        return False
    return _ticket_matches(session, principal, ticket.id)


def can_edit_scope(
    principal: Principal,
    *,
    department_id: int | None,
    team_id: int | None,
    assignee_id: int | None,
) -> bool:
    """Regla de edición sin BD: admin todo; agent su área (equipo/departamento) o asignado."""
    if principal.is_admin:
        return True
    if not principal.is_agent:
        return False
    return (
        (assignee_id is not None and assignee_id == principal.user_id)
        or (team_id is not None and team_id in principal.team_ids)
        or (department_id is not None and department_id in principal.department_ids)
    )


def can_edit_ticket(principal: Principal, ticket: Ticket) -> bool:
    """¿Puede editar/transicionar libremente? admin todo; agent los de su área o asignados."""
    return can_edit_scope(
        principal,
        department_id=ticket.department_id,
        team_id=ticket.team_id,
        assignee_id=ticket.assignee_id,
    )


def can_requester_transition(
    principal: Principal, ticket: Ticket, from_state: WorkflowState, to_state: WorkflowState
) -> bool:
    """El solicitante puede cerrar (destino final) o reabrir (origen ``done``) sus tickets."""
    if ticket.requester_id != principal.user_id:
        return False
    closing = to_state.is_final
    reopening = from_state.category == "done" and to_state.category != "done"
    return closing or reopening


def can_view_internal_scope(
    principal: Principal,
    *,
    department_id: int | None,
    team_id: int | None,
    assignee_id: int | None,
) -> bool:
    """Notas internas sin BD (para eventos): admin, o agente del área del ticket o asignado.

    Ver el ticket no basta: un agente que lo ve solo por ser su solicitante o por una
    aprobación (extensión de visibilidad) no lee las notas internas de otra área.
    """
    return can_edit_scope(
        principal, department_id=department_id, team_id=team_id, assignee_id=assignee_id
    )


def can_view_internal_comments(principal: Principal, ticket: Ticket | None = None) -> bool:
    """¿Puede leer/crear comentarios ``internal`` de este ticket?

    admin siempre; agent solo si atiende el ticket (``can_edit_ticket``: equipo o
    departamento del ticket, o asignado). Sin ticket solo admin (regla conservadora).
    """
    if principal.is_admin:
        return True
    if ticket is None or not principal.is_agent:
        return False
    return can_edit_ticket(principal, ticket)
