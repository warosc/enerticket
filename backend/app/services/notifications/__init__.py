"""Notificaciones: bandeja in-app (siempre) y correo electrónico (SMTP vía Celery).

API del servicio (no hace commit)::

    notify(session, user_ids, kind=..., title=..., body=..., ticket=None, actor_id=None, data=None)

- Crea una fila ``in_app`` por destinatario (deduplicados, activos, distinto del actor) y
  encola el evento ``notification.created`` (se publica en Redis tras el commit).
- Si ``SMTP_HOST`` está configurado y el usuario tiene correo, crea además una fila
  ``email`` (``delivery_status=pending``) que se envía tras el commit con la tarea Celery
  ``notifications.send_email`` (reintentos con backoff). Si ``SMTP_HOST`` está vacío solo se
  registra en el log.

Payload de ``notification.created`` (canal ``servicedesk.events``)::

    {"type": "notification.created", "ticket_id": <id del ticket o 0>, "ticket_key": ..., "actor_id": ...,
     "data": {"notification_id", "user_id", "kind", "title", "ticket_id", "ticket_key"},
     "ts", "department_id": null, "team_id": null, "requester_id": <destinatario>,
     "assignee_id": null}

El WebSocket (realtime) entrega ``notification.*`` exclusivamente a ``data.user_id``.
``requester_id`` = destinatario como respaldo para filtros genéricos por alcance.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.orm import Session, SessionTransaction

from app.core.accounts import is_stale
from app.core.config import settings
from app.core.db import utcnow
from app.core.events import PENDING_KEY
from app.models.automation import Notification
from app.models.core import Ticket, User

logger = logging.getLogger(__name__)

EVENT_NOTIFICATION_CREATED = "notification.created"
EMAIL_QUEUE_KEY = "flows_email_queue"
SYSTEM_SUB_PREFIX = "system:"

# Tipos de notificación conocidos (la UI los muestra con iconos/etiquetas).
KIND_TICKET_ASSIGNED = "ticket_assigned"
KIND_TICKET_COMMENTED = "ticket_commented"
KIND_TICKET_RESOLVED = "ticket_resolved"
KIND_APPROVAL_REQUESTED = "approval_requested"
KIND_APPROVAL_DECIDED = "approval_decided"
KIND_SLA_WARNING = "sla_warning"
KIND_SLA_BREACHED = "sla_breached"
KIND_AUTOMATION = "automation"

MAX_TITLE = 250
MAX_BODY = 4000


def smtp_enabled() -> bool:
    return bool((settings.smtp_host or "").strip())


def public_web_url() -> str:
    """URL pública del portal para enlaces en correos (``APP_PUBLIC_URL``)."""
    url = os.environ.get("APP_PUBLIC_URL", "").strip()
    if not url:
        origins = settings.cors_origin_list
        url = origins[0] if origins else "http://localhost:3000"
    return url.rstrip("/")


def ticket_url(ticket_id: int) -> str:
    return f"{public_web_url()}/tickets/{ticket_id}"


# --------------------------------------------------------------------------- despacho de correos

EmailDispatcher = Callable[[list[int]], None]
_executor: ThreadPoolExecutor | None = None


def _celery_dispatch(notification_ids: list[int]) -> None:
    """Encola los correos en Celery desde un hilo (no bloquea la petición si Redis está caído)."""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="flows-email")

    def _send() -> None:
        try:
            from app.tasks.notifications import send_email

            for notification_id in notification_ids:
                send_email.apply_async(args=[notification_id], retry=False)
        except Exception as exc:  # broker caído: el barrido periódico lo reintentará
            logger.warning(
                "No se pudieron encolar %d correos (%s); se reintentará con el barrido periódico",
                len(notification_ids),
                exc,
            )

    _executor.submit(_send)


_dispatcher: EmailDispatcher = _celery_dispatch


def set_email_dispatcher(fn: EmailDispatcher | None) -> None:
    """Reemplaza el despachador de correos (pruebas). ``None`` restaura Celery."""
    global _dispatcher
    _dispatcher = fn or _celery_dispatch


@sa_event.listens_for(Session, "after_commit")
def _dispatch_emails_after_commit(session: Session) -> None:
    if session.in_nested_transaction():
        return
    pending = session.info.pop(EMAIL_QUEUE_KEY, None)
    if not pending:
        return
    try:
        _dispatcher(list(dict.fromkeys(pending)))
    except Exception:
        logger.exception("Falló el despacho de correos de notificación")


@sa_event.listens_for(Session, "after_transaction_end")
def _discard_emails_on_end(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None and not transaction.nested:
        session.info.pop(EMAIL_QUEUE_KEY, None)


# --------------------------------------------------------------------------- creación


def _clip(text: str | None, limit: int) -> str:
    value = (text or "").strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _eligible_users(
    session: Session, user_ids: Iterable[int | None], exclude: Iterable[int | None]
) -> list[User]:
    excluded = {uid for uid in exclude if uid is not None}
    wanted = sorted({uid for uid in user_ids if uid is not None and uid not in excluded})
    if not wanted:
        return []
    users = session.scalars(select(User).where(User.id.in_(wanted))).all()
    return [
        u
        for u in users
        if u.active
        and not (u.keycloak_sub or "").startswith(SYSTEM_SUB_PREFIX)
        # Cuentas sin inicio de sesión reciente: sus roles/equipos pueden estar desactualizados.
        and not is_stale(u)
    ]


def notify(
    session: Session,
    user_ids: Iterable[int | None],
    *,
    kind: str,
    title: str,
    body: str = "",
    ticket: Ticket | None = None,
    actor_id: int | None = None,
    data: dict[str, Any] | None = None,
    email: bool = True,
    include_actor: bool = False,
) -> list[Notification]:
    """Crea notificaciones in-app (y de correo si aplica). Devuelve las filas ``in_app``."""
    exclude = [] if include_actor else [actor_id]
    recipients = _eligible_users(session, user_ids, exclude)
    if not recipients:
        return []
    title = _clip(title, MAX_TITLE)
    body = _clip(body, MAX_BODY)
    now = utcnow()
    ticket_id = ticket.id if ticket is not None else None
    ticket_key = ticket.key if ticket is not None else None
    extra = dict(data or {})
    if ticket_key:
        extra.setdefault("ticket_key", ticket_key)

    created: list[Notification] = []
    emails: list[Notification] = []
    for user in recipients:
        in_app = Notification(
            user_id=user.id,
            kind=kind,
            title=title,
            body=body,
            ticket_id=ticket_id,
            data=extra or None,
            channel="in_app",
            is_read=False,
            delivery_status="delivered",
            attempts=0,
            created_at=now,
        )
        session.add(in_app)
        created.append(in_app)
        if email and user.email:
            if smtp_enabled():
                mail = Notification(
                    user_id=user.id,
                    kind=kind,
                    title=title,
                    body=body,
                    ticket_id=ticket_id,
                    data=extra or None,
                    channel="email",
                    is_read=True,
                    delivery_status="pending",
                    attempts=0,
                    created_at=now,
                )
                session.add(mail)
                emails.append(mail)
            else:
                logger.info(
                    "SMTP_HOST vacío: se omite el correo '%s' para %s", title, user.username
                )
    session.flush()

    pending_events: list[dict[str, Any]] = session.info.setdefault(PENDING_KEY, [])
    for notification in created:
        pending_events.append(
            {
                "type": EVENT_NOTIFICATION_CREATED,
                # El cliente realtime exige ticket_id numérico (0 = sin ticket). El WS entrega
                # notification.* solo a data.user_id, nunca a otros usuarios del ticket.
                "ticket_id": ticket_id or 0,
                "ticket_key": ticket_key,
                "actor_id": actor_id,
                "data": {
                    "notification_id": notification.id,
                    "user_id": notification.user_id,
                    "kind": kind,
                    "title": title,
                    "ticket_id": ticket_id,
                    "ticket_key": ticket_key,
                },
                "ts": now.isoformat(),
                "department_id": None,
                "team_id": None,
                "requester_id": notification.user_id,
                "assignee_id": None,
            }
        )
    if emails:
        session.info.setdefault(EMAIL_QUEUE_KEY, []).extend(m.id for m in emails)
    return created


# --------------------------------------------------------------------------- consultas


def serialize_notification(notification: Notification) -> dict[str, Any]:
    data = notification.data or {}
    ticket = None
    if notification.ticket_id is not None:
        ticket = {"id": notification.ticket_id, "key": data.get("ticket_key") or f"ESD-{notification.ticket_id}"}
    return {
        "id": notification.id,
        "kind": notification.kind,
        "title": notification.title,
        "body": notification.body or "",
        "ticket": ticket,
        "data": data,
        "read": bool(notification.is_read),
        "read_at": notification.read_at,
        "created_at": notification.created_at,
    }


def team_lead_ids(session: Session, team_id: int | None) -> list[int]:
    from app.models.core import TeamMember

    if team_id is None:
        return []
    return list(
        session.scalars(
            select(TeamMember.user_id).where(
                TeamMember.team_id == team_id, TeamMember.role == "lead"
            )
        ).all()
    )
