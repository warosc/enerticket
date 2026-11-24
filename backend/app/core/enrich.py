"""Enriquecedores de ``TicketOut``: los módulos añaden campos opcionales a los tickets.

Ejemplo (módulo SLA)::

    from app.core.events import register_ticket_enricher

    @register_ticket_enricher
    def add_sla(session, tickets):
        return {t.id: {"sla": {...}} for t in tickets}
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.core import Ticket

logger = logging.getLogger(__name__)

TicketEnricher = Callable[[Session, Sequence[Ticket]], dict[int, dict[str, Any]]]
_enrichers: list[TicketEnricher] = []


def register_ticket_enricher(fn: TicketEnricher) -> TicketEnricher:
    """Registra ``fn(session, tickets) -> {ticket_id: {campo: valor}}``. Usable como decorador."""
    if fn not in _enrichers:
        _enrichers.append(fn)
    return fn


def unregister_ticket_enricher(fn: TicketEnricher) -> None:
    if fn in _enrichers:
        _enrichers.remove(fn)


def collect_enrichments(session: Session, tickets: Sequence[Ticket]) -> dict[int, dict[str, Any]]:
    """Ejecuta todos los enriquecedores; un enriquecedor que falla se registra y se omite."""
    result: dict[int, dict[str, Any]] = {}
    if not tickets:
        return result
    for enricher in list(_enrichers):
        try:
            # SAVEPOINT: un error SQL en un enriquecedor no invalida la transacción de la petición.
            with session.begin_nested():
                extra = enricher(session, tickets) or {}
        except Exception:
            logger.exception("Falló el enriquecedor de tickets %s", getattr(enricher, "__name__", enricher))
            if settings.events_raise_errors:
                raise
            continue
        for ticket_id, fields in extra.items():
            if fields:
                result.setdefault(ticket_id, {}).update(fields)
    return result
