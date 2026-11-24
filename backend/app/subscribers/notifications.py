"""Notificaciones automáticas a partir de eventos de tickets, aprobaciones y SLA.

- ``ticket.assigned`` → al nuevo asignado.
- ``ticket.commented`` (público) → al solicitante; si comenta el solicitante, al asignado.
- ``ticket.transitioned`` a resuelto/finalizado → al solicitante.
- ``approval.requested`` → a los aprobadores de la etapa activa.
- ``approval.decided`` (resultado final) → a quien solicitó la transición y al asignado.
- ``sla.warning`` / ``sla.breached`` → al asignado y a los líderes del equipo.

Nunca se notifica al propio actor del evento.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.events import Event, subscribe
from app.models.core import TicketComment, User
from app.services.notifications import (
    KIND_APPROVAL_DECIDED,
    KIND_APPROVAL_REQUESTED,
    KIND_SLA_BREACHED,
    KIND_SLA_WARNING,
    KIND_TICKET_ASSIGNED,
    KIND_TICKET_COMMENTED,
    KIND_TICKET_RESOLVED,
    notify,
    team_lead_ids,
)

SLA_METRICS = {
    "response": "primera respuesta",
    "first_response": "primera respuesta",
    "resolution": "resolución",
}


def _actor_name(session: Session, actor_id: int | None) -> str:
    if actor_id is None:
        return "El sistema"
    user = session.get(User, actor_id)
    return user.display_name if user else "Un usuario"


def _excerpt(text: str, limit: int = 280) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@subscribe("ticket.assigned")
def notify_assignment(session: Session, event: Event) -> None:
    ticket = event.ticket
    assignee_id = event.data.get("to_assignee_id") or ticket.assignee_id
    if not assignee_id:
        return
    notify(
        session,
        [assignee_id],
        kind=KIND_TICKET_ASSIGNED,
        title=f"Se le asignó el ticket {ticket.key}",
        body=(
            f"{_actor_name(session, event.actor_id)} le asignó «{ticket.title}» "
            f"(prioridad {ticket.priority.name.lower()}, {ticket.department.name})."
        ),
        ticket=ticket,
        actor_id=event.actor_id,
    )


@subscribe("ticket.commented")
def notify_comment(session: Session, event: Event) -> None:
    if event.data.get("visibility") != "public":
        return
    ticket = event.ticket
    comment = session.get(TicketComment, event.data.get("comment_id")) if event.data.get("comment_id") else None
    body_text = _excerpt(comment.body) if comment else ""
    author = _actor_name(session, event.actor_id)
    if event.actor_id == ticket.requester_id:
        recipients = [ticket.assignee_id]
        title = f"El solicitante comentó en {ticket.key}"
    else:
        recipients = [ticket.requester_id]
        title = f"Nuevo comentario en su ticket {ticket.key}"
    notify(
        session,
        recipients,
        kind=KIND_TICKET_COMMENTED,
        title=title,
        body=f"{author}: {body_text}" if body_text else f"{author} agregó un comentario.",
        ticket=ticket,
        actor_id=event.actor_id,
        data={"comment_id": event.data.get("comment_id")},
    )


@subscribe("ticket.transitioned")
def notify_resolution(session: Session, event: Event) -> None:
    data = event.data
    resolved = data.get("to_state") == "resolved" or (
        data.get("to_category") == "done" and data.get("from_category") != "done"
    )
    if not resolved:
        return
    ticket = event.ticket
    notify(
        session,
        [ticket.requester_id],
        kind=KIND_TICKET_RESOLVED,
        title=f"Su ticket {ticket.key} fue {'resuelto' if data.get('to_state') == 'resolved' else 'cerrado'}",
        body=(
            f"«{ticket.title}» pasó a «{ticket.status.name}». Si el problema persiste puede "
            "reabrirlo desde el portal."
        ),
        ticket=ticket,
        actor_id=event.actor_id,
    )


@subscribe("approval.requested")
def notify_approval_requested(session: Session, event: Event) -> None:
    ticket = event.ticket
    data = event.data
    approvers = data.get("approver_ids") or []
    reason = data.get("reason")
    body = (
        f"{_actor_name(session, data.get('requested_by_id'))} solicita aprobar «{data.get('transition_name')}» "
        f"en «{ticket.title}» (etapa: {data.get('step_name')})."
    )
    if reason:
        body += f"\nMotivo: {_excerpt(str(reason))}"
    notify(
        session,
        approvers,
        kind=KIND_APPROVAL_REQUESTED,
        title=f"Aprobación requerida: {ticket.key}",
        body=body,
        ticket=ticket,
        actor_id=event.actor_id,
        data={"approval_request_id": data.get("approval_request_id")},
    )


@subscribe("approval.decided")
def notify_approval_decided(session: Session, event: Event) -> None:
    data = event.data
    status = data.get("request_status")
    if status not in ("approved", "rejected"):
        return
    ticket = event.ticket
    approved = status == "approved"
    decider = _actor_name(session, event.actor_id)
    body = (
        f"{decider} {'aprobó' if approved else 'rechazó'} «{data.get('transition_name')}» en «{ticket.title}»."
    )
    if approved:
        body += " La transición se aplicó automáticamente si el ticket seguía en el estado de origen."
    if data.get("comment"):
        body += f"\nComentario: {_excerpt(str(data['comment']))}"
    recipients: list[Any] = [data.get("requested_by_id"), ticket.assignee_id]
    notify(
        session,
        recipients,
        kind=KIND_APPROVAL_DECIDED,
        title=f"Solicitud {'aprobada' if approved else 'rechazada'}: {ticket.key}",
        body=body,
        ticket=ticket,
        actor_id=event.actor_id,
        data={"approval_request_id": data.get("approval_request_id"), "decision": status},
    )


@subscribe("sla.warning", "sla.breached")
def notify_sla(session: Session, event: Event) -> None:
    ticket = event.ticket
    breached = event.type == "sla.breached"
    metric = SLA_METRICS.get(str(event.data.get("metric") or event.data.get("target") or ""), None)
    what = f"de {metric}" if metric else ""
    title = (
        f"SLA {what} incumplido: {ticket.key}" if breached else f"SLA {what} en riesgo: {ticket.key}"
    ).replace("  ", " ")
    body = (
        f"«{ticket.title}» ({ticket.priority.name.lower()}) "
        + ("superó el tiempo objetivo." if breached else "está próximo a vencer su tiempo objetivo.")
    )
    recipients = [ticket.assignee_id, *team_lead_ids(session, ticket.team_id)]
    notify(
        session,
        recipients,
        kind=KIND_SLA_BREACHED if breached else KIND_SLA_WARNING,
        title=title,
        body=body,
        ticket=ticket,
        actor_id=event.actor_id,
        data={k: v for k, v in event.data.items() if k in ("metric", "target", "due_at", "policy")},
    )
