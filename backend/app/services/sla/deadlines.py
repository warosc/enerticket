"""Detección de advertencias y vencimientos de SLA (la ejecuta Celery beat cada minuto).

Idempotente: una instancia vencida pasa a ``breached`` (y no vuelve a seleccionarse) y una
advertida queda con ``warned_at``; ejecutar la tarea dos veces no duplica eventos.

Concurrencia (PostgreSQL): se respeta el mismo orden de bloqueo que las peticiones HTTP
(fila del ticket y después sus instancias). Por cada candidata se bloquea el ticket con
``FOR UPDATE SKIP LOCKED`` (si una petición lo está modificando se deja para la siguiente
pasada) y se recarga la instancia bloqueada para volver a comprobar su estado. Así una
transición concurrente nunca pisa un ``breached`` ni se producen interbloqueos.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, utcnow
from app.models.core import Ticket
from app.models.sla import SlaInstance
from app.services.sla.engine import (
    EVENT_SLA_BREACHED,
    EVENT_SLA_WARNING,
    STATUS_RUNNING,
    SpecCache,
    emit_sla_event,
    mark_breached,
    mark_warned,
    remaining_business_seconds,
)

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 500


def _due_breaches(session: Session, now: datetime, limit: int) -> list[tuple[int, int]]:
    stmt = (
        select(SlaInstance.id, SlaInstance.ticket_id)
        .where(
            SlaInstance.status == STATUS_RUNNING,
            SlaInstance.target_at.is_not(None),
            SlaInstance.target_at <= now,
            SlaInstance.paused_at.is_(None),
            SlaInstance.stopped_at.is_(None),
        )
        .order_by(SlaInstance.target_at, SlaInstance.id)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def _due_warnings(session: Session, now: datetime, limit: int) -> list[tuple[int, int]]:
    stmt = (
        select(SlaInstance.id, SlaInstance.ticket_id)
        .where(
            SlaInstance.status == STATUS_RUNNING,
            SlaInstance.warned_at.is_(None),
            SlaInstance.warning_at.is_not(None),
            SlaInstance.warning_at <= now,
            SlaInstance.paused_at.is_(None),
            SlaInstance.stopped_at.is_(None),
        )
        .order_by(SlaInstance.warning_at, SlaInstance.id)
        .limit(limit)
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def _lock(session: Session, instance_id: int, ticket_id: int) -> tuple[Ticket, SlaInstance] | None:
    """Bloquea ticket (SKIP LOCKED) e instancia, en ese orden. None = ocupado o ya no existe."""
    ticket = session.scalar(
        select(Ticket)
        .where(Ticket.id == ticket_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if ticket is None:
        return None
    instance = session.scalar(
        select(SlaInstance)
        .where(SlaInstance.id == instance_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if instance is None:
        return None
    return ticket, instance


def process_deadlines(session: Session, now: datetime | None = None, *, batch_size: int = DEFAULT_BATCH_SIZE) -> dict[str, int]:
    """Marca vencimientos y advertencias pendientes y emite ``sla.breached`` / ``sla.warning``.

    No hace commit. Cada instancia se procesa en un SAVEPOINT: un fallo aislado se registra
    y no impide procesar las demás.
    """
    now = now or utcnow()
    cache = SpecCache(session)
    counts = {"breached": 0, "warned": 0, "errors": 0}

    for instance_id, ticket_id in _due_breaches(session, now, batch_size):
        try:
            with session.begin_nested():
                locked = _lock(session, instance_id, ticket_id)
                if locked is None:
                    continue
                ticket, instance = locked
                if mark_breached(session, instance, now):
                    emit_sla_event(session, ticket, EVENT_SLA_BREACHED, instance, detected="scheduler")
                    counts["breached"] += 1
        except Exception:
            counts["errors"] += 1
            logger.exception("No se pudo marcar como vencida la instancia SLA %s", instance_id)

    for instance_id, ticket_id in _due_warnings(session, now, batch_size):
        try:
            with session.begin_nested():
                locked = _lock(session, instance_id, ticket_id)
                if locked is None:
                    continue
                ticket, instance = locked
                if mark_warned(session, instance, now):
                    remaining = remaining_business_seconds(cache.get(instance.calendar_id), instance, now)
                    emit_sla_event(
                        session, ticket, EVENT_SLA_WARNING, instance,
                        remaining_business_seconds=remaining,
                        warning_threshold_pct=instance.warning_threshold_pct,
                    )
                    counts["warned"] += 1
        except Exception:
            counts["errors"] += 1
            logger.exception("No se pudo registrar la advertencia de la instancia SLA %s", instance_id)
    return counts


def check_deadlines(
    now: datetime | None = None,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    """Abre una sesión, procesa los vencimientos/advertencias y confirma."""
    with session_factory() as session:
        try:
            counts = process_deadlines(session, now, batch_size=batch_size)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("Falló la revisión de vencimientos SLA")
            raise
    if counts["breached"] or counts["warned"] or counts["errors"]:
        logger.info(
            "SLA: %d vencidas, %d advertencias, %d errores", counts["breached"], counts["warned"], counts["errors"]
        )
    return counts
