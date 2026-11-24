"""Integración de aprobaciones con la fundación.

- Guard ``ticket.transition``: exige aprobación según políticas (409 ``approval_required``).
- Extensión de visibilidad: los aprobadores ven los tickets donde tienen etapas asignadas.
- Enriquecedor de TicketOut: ``approvals: {pending: <n solicitudes pendientes>}``.
- ``ticket.transitioned``: cancela solicitudes pendientes cuyo estado de origen ya no aplica.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.events import Event, guard, register_ticket_enricher, subscribe
from app.core.permissions import register_visibility_extension
from app.core.security import Principal
from app.models.approvals import ApprovalRequest
from app.models.core import Ticket
from app.services.approvals import service


@guard("ticket.transition")
def require_approval(session: Session, ticket: Ticket, actor: Principal, data: dict[str, Any]) -> None:
    service.transition_guard(session, ticket, actor, data)


@register_visibility_extension
def approver_visibility(principal: Principal):
    # Solo aprobadores y agentes pueden figurar como aprobadores (las políticas lo validan);
    # para los demás no se añade la subconsulta (ahorra consultas en listados y en el WS).
    if not (principal.is_approver or principal.is_agent):
        return None
    return service.approver_visibility_clause(principal)


@register_ticket_enricher
def approvals_summary(session: Session, tickets: Sequence[Ticket]) -> dict[int, dict[str, Any]]:
    ids = [t.id for t in tickets]
    counts = dict(
        session.execute(
            select(ApprovalRequest.ticket_id, func.count(ApprovalRequest.id))
            .where(ApprovalRequest.ticket_id.in_(ids), ApprovalRequest.status == "pending")
            .group_by(ApprovalRequest.ticket_id)
        ).all()
    )
    return {ticket_id: {"approvals": {"pending": int(counts.get(ticket_id, 0))}} for ticket_id in ids}


@subscribe("ticket.transitioned")
def cancel_stale_approvals(session: Session, event: Event) -> None:
    service.cancel_stale_requests(session, event.ticket, event.actor_id)
