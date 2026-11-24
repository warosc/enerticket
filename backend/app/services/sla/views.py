"""Serialización de SLA: resumen por métrica (enriquecedor de TicketOut), detalle y reportes."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models.core import Ticket
from app.models.sla import SlaEvent, SlaInstance, SlaPolicy
from app.services.sla.calendar import CalendarError
from app.services.sla.engine import (
    METRIC_LABELS,
    METRICS,
    STATUS_BREACHED,
    STATUS_CANCELLED,
    STATUS_MET,
    STATUS_PAUSED,
    STATUS_RUNNING,
    SpecCache,
    remaining_business_seconds,
    target_seconds,
)

logger = logging.getLogger(__name__)


def instance_summary(cache: SpecCache, instance: SlaInstance, now: datetime) -> dict[str, Any]:
    """Estado de una métrica tal como lo consume el frontend (``TicketOut.sla.<métrica>``)."""
    total = target_seconds(instance)
    try:
        remaining: int | None = remaining_business_seconds(cache.get(instance.calendar_id), instance, now)
    except (CalendarError, LookupError):
        logger.exception("No se pudo calcular el tiempo restante de la instancia SLA %s", instance.id)
        remaining = None
    paused = instance.paused_at is not None and instance.stopped_at is None
    open_ = instance.stopped_at is None and instance.status != STATUS_CANCELLED
    threshold_left = total * (100 - int(instance.warning_threshold_pct or 75)) / 100
    at_risk = bool(
        open_
        and instance.status in (STATUS_RUNNING, STATUS_PAUSED)
        and (instance.warned_at is not None or (remaining is not None and remaining <= threshold_left))
    )
    return {
        "instance_id": instance.id,
        "metric": instance.metric,
        "label": METRIC_LABELS.get(instance.metric, instance.metric),
        "status": instance.status,
        "policy_id": instance.policy_id,
        "target_minutes": instance.target_minutes,
        "warning_threshold_pct": instance.warning_threshold_pct,
        "started_at": instance.started_at,
        # Mientras está pausada el objetivo no es definitivo: se recalcula al reanudar.
        "target_at": None if paused and instance.status != STATUS_BREACHED else instance.target_at,
        "paused": paused,
        "paused_at": instance.paused_at if paused else None,
        "paused_business_seconds": int(instance.paused_business_seconds or 0),
        "stopped_at": instance.stopped_at,
        "remaining_business_seconds": remaining,
        "elapsed_business_seconds": (total - remaining) if remaining is not None else None,
        "breached": instance.status == STATUS_BREACHED,
        "breached_at": instance.breached_at,
        "warned": instance.warned_at is not None,
        "warned_at": instance.warned_at,
        "at_risk": at_risk,
        "open": open_,
    }


def _current_by_ticket(session: Session, ticket_ids: Sequence[int]) -> dict[int, dict[str, SlaInstance]]:
    result: dict[int, dict[str, SlaInstance]] = {}
    if not ticket_ids:
        return result
    rows = session.scalars(
        select(SlaInstance)
        .where(SlaInstance.ticket_id.in_(list(ticket_ids)), SlaInstance.status != STATUS_CANCELLED)
        .order_by(SlaInstance.id)
    ).all()
    for row in rows:
        result.setdefault(row.ticket_id, {})[row.metric] = row
    return result


def _policy_ref(session: Session, policy_id: int | None) -> dict[str, Any] | None:
    if policy_id is None:
        return None
    policy = session.get(SlaPolicy, policy_id)
    return {"id": policy.id, "name": policy.name} if policy else None


def ticket_sla_map(session: Session, tickets: Sequence[Ticket], now: datetime | None = None) -> dict[int, dict[str, Any]]:
    """``{ticket_id: {"sla": {...} | None}}`` para el enriquecedor de TicketOut."""
    now = now or utcnow()
    cache = SpecCache(session)
    by_ticket = _current_by_ticket(session, [t.id for t in tickets])
    result: dict[int, dict[str, Any]] = {}
    for ticket in tickets:
        instances = by_ticket.get(ticket.id)
        if not instances:
            result[ticket.id] = {"sla": None}
            continue
        # La política vigente es la de la instancia más reciente.
        latest = max(instances.values(), key=lambda i: i.id)
        sla: dict[str, Any] = {"policy": _policy_ref(session, latest.policy_id)}
        for metric in METRICS:
            instance = instances.get(metric)
            sla[metric] = instance_summary(cache, instance, now) if instance else None
        result[ticket.id] = {"sla": sla}
    return result


def ticket_sla_detail(session: Session, ticket: Ticket, now: datetime | None = None) -> dict[str, Any]:
    """Detalle para ``GET /tickets/{id}/sla``: vigentes, todas las instancias y su bitácora."""
    now = now or utcnow()
    cache = SpecCache(session)
    instances = session.scalars(
        select(SlaInstance).where(SlaInstance.ticket_id == ticket.id).order_by(SlaInstance.id)
    ).all()
    current = ticket_sla_map(session, [ticket], now)[ticket.id]["sla"]
    events = []
    if instances:
        by_id = {i.id: i for i in instances}
        rows = session.scalars(
            select(SlaEvent)
            .where(SlaEvent.instance_id.in_(list(by_id)))
            .order_by(SlaEvent.at, SlaEvent.id)
        ).all()
        events = [
            {
                "id": row.id,
                "instance_id": row.instance_id,
                "metric": by_id[row.instance_id].metric,
                "type": row.type,
                "at": row.at,
                "data": row.data or {},
            }
            for row in rows
        ]
    policies: dict[int, dict[str, Any] | None] = {}
    serialized = []
    for instance in instances:
        if instance.policy_id not in policies:
            policies[instance.policy_id] = _policy_ref(session, instance.policy_id)
        item = instance_summary(cache, instance, now)
        item["policy"] = policies[instance.policy_id]
        item["cancelled"] = instance.status == STATUS_CANCELLED
        serialized.append(item)
    return {
        "ticket_id": ticket.id,
        "ticket_key": ticket.key,
        "computed_at": now,
        "policy": current["policy"] if current else None,
        "first_response": current["first_response"] if current else None,
        "resolution": current["resolution"] if current else None,
        "instances": serialized,
        "events": events,
    }


def empty_counts() -> dict[str, Any]:
    return {"total": 0, "met": 0, "breached": 0, "running": 0, "paused": 0, "compliance_pct": None}


def add_count(bucket: dict[str, Any], status: str, count: int) -> None:
    bucket["total"] += count
    if status in (STATUS_MET, STATUS_BREACHED, STATUS_RUNNING, STATUS_PAUSED):
        bucket[status] += count


def finalize_counts(bucket: dict[str, Any]) -> dict[str, Any]:
    decided = bucket["met"] + bucket["breached"]
    bucket["compliance_pct"] = round(bucket["met"] * 100 / decided, 1) if decided else None
    return bucket
