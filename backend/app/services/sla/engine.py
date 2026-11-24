"""Motor de SLA: selección de política, ciclo de vida de las instancias y recálculos.

Conceptos
---------
Cada ticket tiene como máximo una instancia *vigente* (no cancelada) por métrica:

- ``first_response``: se cumple cuando la fundación fija ``ticket.first_response_at``
  (primer comentario público de un agente o primera transición fuera de ``new`` por un agente).
- ``resolution``: se detiene cuando el ticket pasa a la categoría ``done``; si se reabre,
  se reanuda (el tiempo que estuvo resuelto no cuenta).

El reloj se pausa mientras el ticket está en una categoría/estado de pausa de la política
(por defecto ``waiting``, p.ej. "En espera" del solicitante).

Invariantes de una instancia:

- ``paused_business_seconds``: segundos hábiles de pausas YA cerradas.
- ``target_at = started_at + (objetivo + pausas cerradas)`` en tiempo hábil; se recalcula al
  reanudar. Mientras está pausada se conserva para calcular el tiempo restante congelado.
- ``warning_at``: instante en que se alcanza el umbral de advertencia (% del objetivo).
- ``status``: running | paused | met | breached | cancelled. Una instancia vencida
  (``breached``) sigue registrando pausas y su detención, pero no vuelve a ``running``.

Las funciones no hacen commit (las llaman suscriptores, routers y tareas).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.events import emit
from app.models.core import Priority, Ticket, TicketType, WorkflowState
from app.models.sla import (
    BusinessCalendar,
    CalendarHoliday,
    SlaEvent,
    SlaInstance,
    SlaPolicy,
)
from app.services.sla.calendar import (
    CalendarSpec,
    add_business_seconds,
    business_seconds_between,
)

logger = logging.getLogger(__name__)

METRIC_FIRST_RESPONSE = "first_response"
METRIC_RESOLUTION = "resolution"
METRICS = (METRIC_FIRST_RESPONSE, METRIC_RESOLUTION)

STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_MET = "met"
STATUS_BREACHED = "breached"
STATUS_CANCELLED = "cancelled"

EVENT_SLA_WARNING = "sla.warning"
EVENT_SLA_BREACHED = "sla.breached"

DONE_CATEGORY = "done"
PAUSABLE_CATEGORIES = ("todo", "in_progress", "waiting")

METRIC_LABELS = {METRIC_FIRST_RESPONSE: "Primera respuesta", METRIC_RESOLUTION: "Resolución"}


# --------------------------------------------------------------------------- calendarios


def build_spec(calendar: BusinessCalendar, holidays: Iterable[CalendarHoliday] | None = None) -> CalendarSpec:
    items = list(calendar.holidays if holidays is None else holidays)
    return CalendarSpec.build(
        calendar.timezone,
        calendar.windows or [],
        holidays=[h.date for h in items if not h.recurring],
        recurring=[h.date for h in items if h.recurring],
    )


class SpecCache:
    """Caché de ``CalendarSpec`` por calendario durante una operación."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._specs: dict[int, CalendarSpec] = {}

    def get(self, calendar_id: int) -> CalendarSpec:
        spec = self._specs.get(calendar_id)
        if spec is None:
            calendar = self.session.get(BusinessCalendar, calendar_id)
            if calendar is None:
                raise LookupError(f"Calendario {calendar_id} inexistente")
            holidays = self.session.scalars(
                select(CalendarHoliday).where(CalendarHoliday.calendar_id == calendar_id)
            ).all()
            spec = build_spec(calendar, holidays)
            self._specs[calendar_id] = spec
        return spec

    def invalidate(self, calendar_id: int | None = None) -> None:
        if calendar_id is None:
            self._specs.clear()
        else:
            self._specs.pop(calendar_id, None)


# --------------------------------------------------------------------------- políticas


def policy_minutes(policy: SlaPolicy, metric: str) -> int | None:
    value = policy.first_response_minutes if metric == METRIC_FIRST_RESPONSE else policy.resolution_minutes
    return value if value and value > 0 else None


def policy_matches(
    policy: SlaPolicy, *, priority_key: str | None, type_key: str | None, department_id: int | None
) -> bool:
    """Todas las condiciones no vacías deben cumplirse (lista vacía = cualquiera)."""
    if policy.priority_keys and priority_key not in policy.priority_keys:
        return False
    if policy.type_keys and type_key not in policy.type_keys:
        return False
    if policy.department_ids and department_id not in policy.department_ids:
        return False
    return True


def select_policy(session: Session, ticket: Ticket) -> SlaPolicy | None:
    """Primera política activa (por ``precedence`` y luego ``id``) cuyas condiciones cumple el ticket."""
    priority = session.get(Priority, ticket.priority_id)
    ticket_type = session.get(TicketType, ticket.type_id)
    policies = session.scalars(
        select(SlaPolicy).where(SlaPolicy.active.is_(True)).order_by(SlaPolicy.precedence, SlaPolicy.id)
    ).all()
    for policy in policies:
        if policy_matches(
            policy,
            priority_key=priority.key if priority else None,
            type_key=ticket_type.key if ticket_type else None,
            department_id=ticket.department_id,
        ):
            return policy
    return None


def is_pausing_state(policy: SlaPolicy | None, state: WorkflowState) -> bool:
    if policy is None or state.category == DONE_CATEGORY:
        return False
    return state.category in (policy.pause_categories or []) or state.key in (policy.pause_states or [])


# --------------------------------------------------------------------------- instancias: cálculo


def target_seconds(instance: SlaInstance) -> int:
    return int(instance.target_minutes) * 60


def clock_reference(instance: SlaInstance, now: datetime) -> datetime:
    """Momento hasta el que corre el reloj: detención, pausa o ``now``."""
    if instance.stopped_at is not None:
        return instance.stopped_at
    if instance.paused_at is not None:
        return instance.paused_at
    return now


def is_closed(instance: SlaInstance) -> bool:
    return instance.status in (STATUS_MET, STATUS_CANCELLED) or instance.stopped_at is not None


def recompute_targets(spec: CalendarSpec, instance: SlaInstance) -> None:
    """Recalcula ``target_at``/``warning_at`` con las pausas cerradas (no en vencidas/canceladas)."""
    if instance.status in (STATUS_BREACHED, STATUS_CANCELLED):
        return
    base = int(instance.paused_business_seconds or 0)
    total = target_seconds(instance)
    instance.target_at = add_business_seconds(spec, instance.started_at, total + base)
    if instance.warned_at is None:
        warn = math.ceil(total * int(instance.warning_threshold_pct or 75) / 100)
        instance.warning_at = add_business_seconds(spec, instance.started_at, warn + base)


def elapsed_business_seconds(spec: CalendarSpec, instance: SlaInstance, now: datetime) -> int:
    """Segundos hábiles consumidos (excluye pausas)."""
    reference = clock_reference(instance, now)
    elapsed = business_seconds_between(spec, instance.started_at, reference)
    return max(0, elapsed - int(instance.paused_business_seconds or 0))


def remaining_business_seconds(spec: CalendarSpec, instance: SlaInstance, now: datetime) -> int:
    """Segundos hábiles restantes (negativo = excedido). Congelado si está pausada o detenida."""
    if instance.status != STATUS_BREACHED and instance.target_at is not None:
        reference = clock_reference(instance, now)
        if reference <= instance.target_at:
            return business_seconds_between(spec, reference, instance.target_at)
        return -business_seconds_between(spec, instance.target_at, reference)
    return target_seconds(instance) - elapsed_business_seconds(spec, instance, now)


# --------------------------------------------------------------------------- instancias: bitácora


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def log_event(session: Session, instance: SlaInstance, event_type: str, at: datetime, **data: Any) -> SlaEvent:
    if instance.id is None:
        session.flush()
    clean = {k: v for k, v in data.items() if v is not None}
    event = SlaEvent(instance_id=instance.id, type=event_type, at=at, data=clean or None)
    session.add(event)
    return event


def pause_intervals(session: Session, instance: SlaInstance) -> list[tuple[datetime, datetime | None]]:
    """Intervalos de pausa de la instancia (heredados + propios; el abierto termina en None)."""
    intervals: list[tuple[datetime, datetime | None]] = []
    if instance.id is not None:
        session.flush()
        events = session.scalars(
            select(SlaEvent).where(SlaEvent.instance_id == instance.id).order_by(SlaEvent.id)
        ).all()
        for event in events:
            data = event.data or {}
            for start, end in data.get("inherited_pauses") or []:
                intervals.append((datetime.fromisoformat(start), datetime.fromisoformat(end)))
            pause = data.get("pause")
            if pause and len(pause) == 2:
                intervals.append((datetime.fromisoformat(pause[0]), datetime.fromisoformat(pause[1])))
    if instance.paused_at is not None:
        intervals.append((instance.paused_at, None))
    return intervals


# --------------------------------------------------------------------------- instancias: transiciones


def create_instance(
    session: Session,
    cache: SpecCache,
    ticket: Ticket,
    policy: SlaPolicy,
    metric: str,
    *,
    started_at: datetime,
    inherited: Iterable[tuple[datetime, datetime | None]] = (),
    at: datetime,
    reason: str = "created",
) -> SlaInstance:
    minutes = policy_minutes(policy, metric)
    if minutes is None:
        raise ValueError(f"La política {policy.name} no define objetivo para {metric}")
    spec = cache.get(policy.calendar_id)
    closed = [(s, e) for s, e in inherited if e is not None]
    open_start = next((s for s, e in inherited if e is None), None)
    instance = SlaInstance(
        ticket_id=ticket.id,
        policy_id=policy.id,
        calendar_id=policy.calendar_id,
        metric=metric,
        target_minutes=minutes,
        warning_threshold_pct=policy.warning_threshold_pct,
        started_at=started_at,
        paused_business_seconds=sum(business_seconds_between(spec, s, e) for s, e in closed),
        paused_at=open_start,
        status=STATUS_PAUSED if open_start is not None else STATUS_RUNNING,
        created_at=at,
        updated_at=at,
    )
    session.add(instance)
    session.flush()
    recompute_targets(spec, instance)
    log_event(
        session,
        instance,
        "started",
        at,
        reason=reason,
        policy_id=policy.id,
        policy_name=policy.name,
        target_minutes=minutes,
        target_at=_iso(instance.target_at),
        inherited_pauses=[[_iso(s), _iso(e)] for s, e in closed] or None,
    )
    return instance


def pause(session: Session, instance: SlaInstance, at: datetime, *, reason: str) -> bool:
    if is_closed(instance) or instance.paused_at is not None:
        return False
    at = max(at, instance.started_at)
    instance.paused_at = at
    if instance.status == STATUS_RUNNING:
        instance.status = STATUS_PAUSED
    instance.updated_at = at
    log_event(session, instance, "paused", at, reason=reason)
    return True


def resume(
    session: Session, spec: CalendarSpec, instance: SlaInstance, at: datetime, *, reason: str, allow_reopen: bool
) -> bool:
    """Reanuda una pausa o (si ``allow_reopen``) una instancia detenida por resolución."""
    if instance.status == STATUS_CANCELLED:
        return False
    intervals: list[tuple[datetime, datetime]] = []
    if instance.paused_at is not None:
        intervals.append((instance.paused_at, max(at, instance.paused_at)))
        instance.paused_at = None
    if instance.stopped_at is not None:
        if not allow_reopen:
            return bool(intervals)
        intervals.append((instance.stopped_at, max(at, instance.stopped_at)))
        instance.stopped_at = None
        event_type = "reopened"
    else:
        event_type = "resumed"
    if not intervals:
        return False
    for start, end in intervals:
        instance.paused_business_seconds = int(instance.paused_business_seconds or 0) + business_seconds_between(
            spec, start, end
        )
    if instance.status in (STATUS_PAUSED, STATUS_MET):
        instance.status = STATUS_RUNNING
    recompute_targets(spec, instance)
    instance.updated_at = at
    for start, end in intervals:
        log_event(
            session,
            instance,
            event_type,
            at,
            reason=reason,
            pause=[_iso(start), _iso(end)],
            paused_business_seconds=instance.paused_business_seconds,
            target_at=_iso(instance.target_at),
        )
    return True


def stop(session: Session, spec: CalendarSpec, instance: SlaInstance, at: datetime, *, reason: str) -> bool:
    """Detiene el reloj (métrica cumplida). Devuelve True si al detenerla se descubre un vencimiento."""
    if is_closed(instance):
        return False
    at = max(at, instance.started_at)
    pause_data = None
    if instance.paused_at is not None:
        start = instance.paused_at
        end = max(at, start)
        instance.paused_business_seconds = int(instance.paused_business_seconds or 0) + business_seconds_between(
            spec, start, end
        )
        instance.paused_at = None
        pause_data = [_iso(start), _iso(end)]
    instance.stopped_at = at
    instance.updated_at = at
    newly_breached = False
    if instance.status == STATUS_BREACHED:
        log_event(session, instance, "stopped", at, reason=reason, pause=pause_data)
        return False
    recompute_targets(spec, instance)
    if instance.target_at is not None and at > instance.target_at:
        instance.status = STATUS_BREACHED
        instance.breached_at = instance.target_at
        newly_breached = True
        log_event(
            session, instance, "breached", at, reason=reason, pause=pause_data,
            target_at=_iso(instance.target_at), detected="on_stop",
        )
    else:
        instance.status = STATUS_MET
        log_event(session, instance, "met", at, reason=reason, pause=pause_data, target_at=_iso(instance.target_at))
    return newly_breached


def cancel(session: Session, instance: SlaInstance, at: datetime, *, reason: str, **data: Any) -> bool:
    if instance.status == STATUS_CANCELLED:
        return False
    instance.status = STATUS_CANCELLED
    if instance.stopped_at is None:
        instance.stopped_at = max(at, instance.started_at)
    instance.updated_at = at
    log_event(session, instance, "cancelled", at, reason=reason, **data)
    return True


def mark_breached(session: Session, instance: SlaInstance, at: datetime) -> bool:
    if instance.status != STATUS_RUNNING or instance.target_at is None or instance.target_at > at:
        return False
    instance.status = STATUS_BREACHED
    instance.breached_at = instance.target_at
    if instance.warned_at is None:
        instance.warned_at = at
    instance.updated_at = at
    log_event(session, instance, "breached", at, target_at=_iso(instance.target_at), detected="scheduler")
    return True


def mark_warned(session: Session, instance: SlaInstance, at: datetime) -> bool:
    if (
        instance.status != STATUS_RUNNING
        or instance.warned_at is not None
        or instance.warning_at is None
        or instance.warning_at > at
    ):
        return False
    instance.warned_at = at
    instance.updated_at = at
    log_event(
        session, instance, "warning", at,
        warning_threshold_pct=instance.warning_threshold_pct, target_at=_iso(instance.target_at),
    )
    return True


# --------------------------------------------------------------------------- consultas


def current_instances(
    session: Session, ticket_id: int, *, for_update: bool = False
) -> dict[str, SlaInstance]:
    """Instancia vigente (no cancelada, la más reciente) por métrica.

    Con ``for_update`` las filas se bloquean (``FOR UPDATE``) y se recargan desde la BD
    (``populate_existing``): así una transición concurrente con la tarea de vencimientos no
    pisa un ``breached`` recién confirmado. Orden de bloqueo en todo el módulo: ticket y
    después sus instancias.
    """
    session.flush()
    stmt = (
        select(SlaInstance)
        .where(SlaInstance.ticket_id == ticket_id, SlaInstance.status != STATUS_CANCELLED)
        .order_by(SlaInstance.id)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    rows = session.scalars(stmt).all()
    result: dict[str, SlaInstance] = {}
    for row in rows:
        result[row.metric] = row
    return result


def _state(session: Session, ticket: Ticket) -> WorkflowState:
    state = session.get(WorkflowState, ticket.status_id)
    if state is None:  # pragma: no cover - integridad referencial
        raise LookupError(f"Estado {ticket.status_id} inexistente")
    return state


# --------------------------------------------------------------------------- eventos de dominio


def emit_sla_event(session: Session, ticket: Ticket, event_type: str, instance: SlaInstance, **extra: Any) -> None:
    policy = session.get(SlaPolicy, instance.policy_id)
    emit(
        session,
        event_type,
        ticket,
        None,
        {
            "metric": instance.metric,
            "metric_label": METRIC_LABELS.get(instance.metric, instance.metric),
            "instance_id": instance.id,
            "policy_id": instance.policy_id,
            "policy_name": policy.name if policy else None,
            # Alias usados por notificaciones/automatizaciones (módulo flows).
            "policy": policy.name if policy else None,
            "status": instance.status,
            "target_at": instance.target_at,
            "due_at": instance.target_at,
            "breached_at": instance.breached_at,
            "warned_at": instance.warned_at,
            **extra,
        },
    )


# --------------------------------------------------------------------------- nivel ticket


@dataclass
class SyncResult:
    created: int = 0
    cancelled: int = 0
    changed: int = 0
    breached: int = 0


def _first_response_closed_without_response(session: Session, ticket_id: int) -> bool:
    """¿Se canceló la primera respuesta porque el ticket se cerró sin respuesta?

    Al reabrir ese ticket no se vuelve a crear la métrica (se crearía desde ``created_at``
    y vencería al instante).
    """
    rows = session.execute(
        select(SlaEvent.data)
        .join(SlaInstance, SlaInstance.id == SlaEvent.instance_id)
        .where(
            SlaInstance.ticket_id == ticket_id,
            SlaInstance.metric == METRIC_FIRST_RESPONSE,
            SlaInstance.status == STATUS_CANCELLED,
            SlaEvent.type == "cancelled",
        )
    ).scalars().all()
    return any(isinstance(data, dict) and data.get("reason") == "done_without_response" for data in rows)


def reevaluate_ticket(session: Session, ticket: Ticket, at: datetime | None = None, *, cache: SpecCache | None = None) -> SyncResult:
    """Selecciona la política vigente y crea/reemplaza instancias si cambió (política, objetivo o calendario).

    Las instancias ya cerradas (cumplidas o detenidas) y las VENCIDAS se conservan: un
    incumplimiento es un hecho histórico y re-evaluar (cambio de prioridad/departamento o
    ``POST /sla/recalculate``) no lo borra ni vuelve a notificarlo. Al reemplazar una
    instancia abierta se conserva su inicio y sus pausas (recalculadas en el nuevo calendario).
    """
    at = at or utcnow()
    cache = cache or SpecCache(session)
    result = SyncResult()
    state = _state(session, ticket)
    policy = select_policy(session, ticket)
    current = current_instances(session, ticket.id, for_update=True)
    for metric in METRICS:
        instance = current.get(metric)
        minutes = policy_minutes(policy, metric) if policy is not None else None
        inherited: list[tuple[datetime, datetime | None]] = []
        started_at = ticket.created_at
        previous_warned_at: datetime | None = None
        if instance is not None:
            if is_closed(instance) or instance.status == STATUS_BREACHED:
                continue
            if (
                policy is not None
                and minutes is not None
                and instance.policy_id == policy.id
                and instance.target_minutes == minutes
                and instance.calendar_id == policy.calendar_id
                and instance.warning_threshold_pct == policy.warning_threshold_pct
            ):
                continue
            inherited = pause_intervals(session, instance)
            started_at = instance.started_at
            previous_warned_at = instance.warned_at
            cancel(
                session, instance, at, reason="policy_changed",
                new_policy_id=policy.id if policy else None,
                new_policy_name=policy.name if policy else None,
            )
            result.cancelled += 1
        elif state.category == DONE_CATEGORY:
            continue
        elif metric == METRIC_FIRST_RESPONSE and _first_response_closed_without_response(
            session, ticket.id
        ):
            continue
        if policy is None or minutes is None:
            continue
        new_instance = create_instance(
            session, cache, ticket, policy, metric,
            started_at=started_at, inherited=inherited, at=at,
            reason="created" if instance is None else "recalculated",
        )
        # Si ya se advirtió y el nuevo umbral también pasó, no se repite la advertencia.
        if (
            previous_warned_at is not None
            and new_instance.warning_at is not None
            and new_instance.warning_at <= at
        ):
            new_instance.warned_at = previous_warned_at
        result.created += 1
        if new_instance.status == STATUS_RUNNING and mark_breached(session, new_instance, at):
            result.breached += 1
            emit_sla_event(session, ticket, EVENT_SLA_BREACHED, new_instance, detected="recalculation")
    return result


def sync_ticket(session: Session, ticket: Ticket, at: datetime | None = None, *, cache: SpecCache | None = None) -> SyncResult:
    """Aplica el estado actual del ticket a sus instancias: pausa, reanuda, cumple o cancela."""
    at = at or utcnow()
    cache = cache or SpecCache(session)
    result = SyncResult()
    state = _state(session, ticket)
    done = state.category == DONE_CATEGORY
    breached: list[SlaInstance] = []
    for metric, instance in current_instances(session, ticket.id, for_update=True).items():
        policy = session.get(SlaPolicy, instance.policy_id)
        spec = cache.get(instance.calendar_id)
        pausing = is_pausing_state(policy, state)
        changed = False
        if metric == METRIC_FIRST_RESPONSE:
            if instance.stopped_at is not None:
                continue
            if ticket.first_response_at is not None:
                if stop(session, spec, instance, ticket.first_response_at, reason="first_response"):
                    breached.append(instance)
                changed = True
            elif done:
                if instance.status == STATUS_BREACHED:
                    changed = stop(session, spec, instance, at, reason="ticket_done")
                else:
                    changed = cancel(session, instance, at, reason="done_without_response")
            elif pausing:
                changed = pause(session, instance, at, reason=f"state:{state.key}")
            else:
                changed = resume(session, spec, instance, at, reason=f"state:{state.key}", allow_reopen=False)
        else:
            if done:
                if instance.stopped_at is None:
                    stop_at = ticket.resolved_at or at
                    if stop(session, spec, instance, stop_at, reason=f"state:{state.key}"):
                        breached.append(instance)
                    changed = True
            elif pausing:
                if instance.stopped_at is not None:
                    resume(session, spec, instance, at, reason="reopened", allow_reopen=True)
                    changed = True
                changed = pause(session, instance, at, reason=f"state:{state.key}") or changed
            else:
                changed = resume(session, spec, instance, at, reason=f"state:{state.key}", allow_reopen=True)
        if changed:
            result.changed += 1
    for instance in breached:
        result.breached += 1
        emit_sla_event(session, ticket, EVENT_SLA_BREACHED, instance, detected="on_stop")
    return result


def ensure_ticket_sla(session: Session, ticket: Ticket, at: datetime | None = None) -> SyncResult:
    """Re-evalúa la política y sincroniza con el estado actual (alta de ticket, cambio de prioridad...)."""
    at = at or utcnow()
    cache = SpecCache(session)
    first = reevaluate_ticket(session, ticket, at, cache=cache)
    second = sync_ticket(session, ticket, at, cache=cache)
    return SyncResult(
        created=first.created,
        cancelled=first.cancelled,
        changed=first.changed + second.changed,
        breached=first.breached + second.breached,
    )


def refresh_calendar_targets(session: Session, calendar_id: int) -> int:
    """Recalcula objetivos de instancias abiertas de un calendario tras editarlo (ventanas/feriados).

    Las pausas ya cerradas se vuelven a medir con el calendario nuevo (un día que pasa a ser
    feriado deja de contar como pausa hábil) antes de recalcular ``target_at``/``warning_at``.
    """
    cache = SpecCache(session)
    spec = cache.get(calendar_id)
    instances = session.scalars(
        select(SlaInstance)
        .where(
            SlaInstance.calendar_id == calendar_id,
            SlaInstance.status.in_((STATUS_RUNNING, STATUS_PAUSED)),
            SlaInstance.stopped_at.is_(None),
        )
        .order_by(SlaInstance.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for instance in instances:
        closed = [(start, end) for start, end in pause_intervals(session, instance) if end is not None]
        instance.paused_business_seconds = sum(
            business_seconds_between(spec, start, end) for start, end in closed
        )
        recompute_targets(spec, instance)
    return len(instances)
