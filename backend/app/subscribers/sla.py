"""Suscriptores del módulo SLA y enriquecedor ``sla`` de TicketOut.

- ``ticket.created``      → selecciona la política y crea las instancias (primera respuesta, resolución).
- ``ticket.transitioned`` → pausa/reanuda según categoría/estado, detiene la resolución al
  resolver (reabrir la reanuda) y cumple la primera respuesta (``ticket.first_response_at``).
- ``ticket.commented``    → cumple la primera respuesta si la fundación fijó ``first_response_at``.
- ``ticket.updated``      → si cambió la prioridad, el tipo o el departamento, re-evalúa la política.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.core.events import Event, register_ticket_enricher, subscribe
from app.models.core import Ticket
from app.services.sla.engine import ensure_ticket_sla, sync_ticket
from app.services.sla.views import ticket_sla_map

logger = logging.getLogger(__name__)

# Claves de ``snapshot()`` de la fundación que afectan a la selección de política.
POLICY_FIELDS = ("priority", "type", "department_id")


@subscribe("ticket.created")
def on_ticket_created(session: Session, event: Event) -> None:
    ensure_ticket_sla(session, event.ticket, event.ts)


@subscribe("ticket.transitioned", "ticket.commented")
def on_ticket_progress(session: Session, event: Event) -> None:
    sync_ticket(session, event.ticket, event.ts)


@subscribe("ticket.updated")
def on_ticket_updated(session: Session, event: Event) -> None:
    changes: dict[str, Any] = event.data.get("changes") or {}
    if any(field in changes for field in POLICY_FIELDS):
        ensure_ticket_sla(session, event.ticket, event.ts)


@register_ticket_enricher
def enrich_tickets_with_sla(session: Session, tickets: Sequence[Ticket]) -> dict[int, dict[str, Any]]:
    return ticket_sla_map(session, tickets)
