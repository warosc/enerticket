"""API del módulo SLA.

Lectura de catálogo (agent+): calendarios, feriados, políticas, vista previa de cálculo y
tickets en riesgo. Administración (admin): CRUD de calendarios, feriados y políticas,
importación de feriados oficiales y recálculo de tickets abiertos.
Cualquier usuario autenticado: ``GET /tickets/{id}/sla`` (si puede ver el ticket) y
``GET /sla/summary`` (cumplimiento sobre los tickets que puede ver).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.permissions import visible_tickets_filter
from app.core.security import Principal, get_principal, require_roles
from app.models.core import Department, Priority, Ticket, TicketType, WorkflowState
from app.models.sla import BusinessCalendar, CalendarHoliday, SlaInstance, SlaPolicy
from app.services import tickets as ticket_svc
from app.services.sla.calendar import CalendarError, add_business_minutes, business_seconds_between, get_zone
from app.services.sla.catalog import calendar_out, holiday_out, import_official_holidays, policy_out
from app.services.sla.engine import (
    METRIC_LABELS,
    METRICS,
    STATUS_BREACHED,
    STATUS_CANCELLED,
    STATUS_PAUSED,
    STATUS_RUNNING,
    SpecCache,
    ensure_ticket_sla,
    refresh_calendar_targets,
    remaining_business_seconds,
)
from app.services.sla.schemas import (
    CalendarIn,
    CalendarOut,
    CalendarPatch,
    HolidayImportIn,
    HolidayImportOut,
    HolidayIn,
    HolidayOut,
    HolidayPatch,
    PolicyIn,
    PolicyOut,
    PolicyPatch,
    PreviewOut,
    RecalculateOut,
)
from app.services.sla.views import add_count, empty_counts, finalize_counts, ticket_sla_detail

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sla"])

require_agent = require_roles("agent")
require_admin = require_roles("admin")

MAX_SUMMARY_DAYS = 731


# --------------------------------------------------------------------------- utilidades


def _commit(session: Session, conflict_detail: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=conflict_detail) from exc


def _get_or_404(session: Session, model: type, obj_id: int, label: str) -> Any:
    obj = session.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{label} no encontrado")
    return obj


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _name_taken(session: Session, model: type, name: str, exclude_id: int | None = None) -> bool:
    stmt = select(model.id).where(func.lower(model.name) == name.strip().lower())
    if exclude_id is not None:
        stmt = stmt.where(model.id != exclude_id)
    return session.scalar(stmt) is not None


# --------------------------------------------------------------------------- calendarios


@router.get("/sla/calendars", response_model=list[CalendarOut], dependencies=[Depends(require_agent)])
def list_calendars(session: Session = Depends(get_db)) -> list[dict]:
    calendars = session.scalars(select(BusinessCalendar).order_by(BusinessCalendar.name)).all()
    return [calendar_out(session, c) for c in calendars]


@router.get("/sla/calendars/{calendar_id}", response_model=CalendarOut, dependencies=[Depends(require_agent)])
def get_calendar(calendar_id: int, session: Session = Depends(get_db)) -> dict:
    return calendar_out(session, _get_or_404(session, BusinessCalendar, calendar_id, "Calendario"))


@router.post(
    "/sla/calendars", response_model=CalendarOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_calendar(payload: CalendarIn, session: Session = Depends(get_db)) -> dict:
    if _name_taken(session, BusinessCalendar, payload.name):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Ya existe un calendario con ese nombre")
    now = utcnow()
    calendar = BusinessCalendar(
        name=payload.name,
        description=payload.description.strip(),
        timezone=payload.timezone,
        windows=payload.normalized_windows(),
        created_at=now,
        updated_at=now,
    )
    session.add(calendar)
    _commit(session, "Ya existe un calendario con ese nombre")
    return calendar_out(session, calendar)


@router.patch("/sla/calendars/{calendar_id}", response_model=CalendarOut, dependencies=[Depends(require_admin)])
def update_calendar(calendar_id: int, payload: CalendarPatch, session: Session = Depends(get_db)) -> dict:
    calendar = _get_or_404(session, BusinessCalendar, calendar_id, "Calendario")
    if payload.name is not None:
        name = payload.name.strip()
        if _name_taken(session, BusinessCalendar, name, exclude_id=calendar.id):
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Ya existe un calendario con ese nombre")
        calendar.name = name
    if payload.description is not None:
        calendar.description = payload.description.strip()
    affects_targets = False
    if payload.timezone is not None and payload.timezone != calendar.timezone:
        calendar.timezone = payload.timezone
        affects_targets = True
    windows = payload.normalized_windows()
    if windows is not None and windows != (calendar.windows or []):
        calendar.windows = windows
        affects_targets = True
    calendar.updated_at = utcnow()
    session.flush()
    if affects_targets:
        _refresh_targets(session, calendar.id)
    _commit(session, "Ya existe un calendario con ese nombre")
    return calendar_out(session, calendar)


@router.delete(
    "/sla/calendars/{calendar_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
def delete_calendar(calendar_id: int, session: Session = Depends(get_db)) -> Response:
    calendar = _get_or_404(session, BusinessCalendar, calendar_id, "Calendario")
    in_policies = session.scalar(select(func.count()).select_from(SlaPolicy).where(SlaPolicy.calendar_id == calendar.id))
    in_instances = session.scalar(
        select(func.count()).select_from(SlaInstance).where(SlaInstance.calendar_id == calendar.id)
    )
    if in_policies or in_instances:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="El calendario está en uso por políticas o tickets; no se puede eliminar",
        )
    session.delete(calendar)
    _commit(session, "El calendario está en uso; no se puede eliminar")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _refresh_targets(session: Session, calendar_id: int) -> None:
    try:
        refresh_calendar_targets(session, calendar_id)
    except CalendarError as exc:
        session.rollback()
        raise _unprocessable(str(exc)) from exc


@router.get(
    "/sla/calendars/{calendar_id}/preview", response_model=PreviewOut, dependencies=[Depends(require_agent)]
)
def preview_calendar(
    calendar_id: int,
    minutes: int = Query(ge=1, le=60 * 24 * 365),
    start: Optional[datetime] = Query(default=None, description="Inicio (ISO 8601); por defecto ahora"),
    session: Session = Depends(get_db),
) -> dict:
    """Calcula cuándo vence un objetivo de ``minutes`` minutos hábiles iniciado en ``start``."""
    _get_or_404(session, BusinessCalendar, calendar_id, "Calendario")
    begin = start or utcnow()
    if begin.tzinfo is None:
        begin = begin.replace(tzinfo=timezone.utc)
    try:
        spec = SpecCache(session).get(calendar_id)
        target = add_business_minutes(spec, begin, minutes)
    except CalendarError as exc:
        raise _unprocessable(str(exc)) from exc
    return {
        "calendar_id": calendar_id,
        "start": begin,
        "minutes": minutes,
        "target_at": target,
        "business_seconds": business_seconds_between(spec, begin, target),
    }


# --------------------------------------------------------------------------- feriados


@router.get(
    "/sla/calendars/{calendar_id}/holidays", response_model=list[HolidayOut], dependencies=[Depends(require_agent)]
)
def list_holidays(
    calendar_id: int,
    year: Optional[int] = Query(default=None, ge=2000, le=2100),
    session: Session = Depends(get_db),
) -> list[dict]:
    _get_or_404(session, BusinessCalendar, calendar_id, "Calendario")
    stmt = select(CalendarHoliday).where(CalendarHoliday.calendar_id == calendar_id)
    if year is not None:
        stmt = stmt.where(
            or_(
                CalendarHoliday.recurring.is_(True),
                and_(CalendarHoliday.date >= date(year, 1, 1), CalendarHoliday.date <= date(year, 12, 31)),
            )
        )
    holidays = session.scalars(stmt.order_by(CalendarHoliday.date)).all()
    return [holiday_out(h) for h in holidays]


@router.post(
    "/sla/calendars/{calendar_id}/holidays", response_model=HolidayOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_holiday(calendar_id: int, payload: HolidayIn, session: Session = Depends(get_db)) -> dict:
    calendar = _get_or_404(session, BusinessCalendar, calendar_id, "Calendario")
    _ensure_free_date(session, calendar.id, payload.date)
    holiday = CalendarHoliday(
        calendar_id=calendar.id, date=payload.date, name=payload.name.strip(), recurring=payload.recurring
    )
    session.add(holiday)
    session.flush()
    _refresh_targets(session, calendar.id)
    _commit(session, "Ya existe un feriado en esa fecha")
    return holiday_out(holiday)


@router.post(
    "/sla/calendars/{calendar_id}/holidays/import", response_model=HolidayImportOut,
    dependencies=[Depends(require_admin)],
)
def import_holidays(calendar_id: int, payload: HolidayImportIn, session: Session = Depends(get_db)) -> dict:
    """Importa los feriados oficiales (Guatemala por defecto) de los años indicados."""
    calendar = _get_or_404(session, BusinessCalendar, calendar_id, "Calendario")
    try:
        result = import_official_holidays(session, calendar, payload.years, country=payload.country)
    except ValueError as exc:
        session.rollback()
        raise _unprocessable(str(exc)) from exc
    _refresh_targets(session, calendar.id)
    _commit(session, "Conflicto al importar feriados")
    return result


def _ensure_free_date(session: Session, calendar_id: int, day: date, exclude_id: int | None = None) -> None:
    stmt = select(CalendarHoliday.id).where(CalendarHoliday.calendar_id == calendar_id, CalendarHoliday.date == day)
    if exclude_id is not None:
        stmt = stmt.where(CalendarHoliday.id != exclude_id)
    if session.scalar(stmt) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Ya existe un feriado en esa fecha")


@router.patch("/sla/holidays/{holiday_id}", response_model=HolidayOut, dependencies=[Depends(require_admin)])
def update_holiday(holiday_id: int, payload: HolidayPatch, session: Session = Depends(get_db)) -> dict:
    holiday = _get_or_404(session, CalendarHoliday, holiday_id, "Feriado")
    if payload.date is not None and payload.date != holiday.date:
        _ensure_free_date(session, holiday.calendar_id, payload.date, exclude_id=holiday.id)
        holiday.date = payload.date
    if payload.name is not None:
        holiday.name = payload.name.strip()
    if payload.recurring is not None:
        holiday.recurring = payload.recurring
    session.flush()
    _refresh_targets(session, holiday.calendar_id)
    _commit(session, "Ya existe un feriado en esa fecha")
    return holiday_out(holiday)


@router.delete(
    "/sla/holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)]
)
def delete_holiday(holiday_id: int, session: Session = Depends(get_db)) -> Response:
    holiday = _get_or_404(session, CalendarHoliday, holiday_id, "Feriado")
    calendar_id = holiday.calendar_id
    session.delete(holiday)
    session.flush()
    _refresh_targets(session, calendar_id)
    _commit(session, "No se pudo eliminar el feriado")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- políticas


def _validate_policy_refs(session: Session, values: dict[str, Any]) -> None:
    if values.get("calendar_id") is not None and session.get(BusinessCalendar, values["calendar_id"]) is None:
        raise _unprocessable("El calendario no existe")
    checks = (
        ("priority_keys", Priority.key, "Prioridades desconocidas"),
        ("type_keys", TicketType.key, "Tipos de ticket desconocidos"),
        ("department_ids", Department.id, "Departamentos inexistentes"),
        ("pause_states", WorkflowState.key, "Estados desconocidos"),
    )
    for field, column, message in checks:
        requested = values.get(field)
        if not requested:
            continue
        found = set(session.scalars(select(column).where(column.in_(list(requested)))).all())
        missing = [str(item) for item in requested if item not in found]
        if missing:
            raise _unprocessable(f"{message}: {', '.join(missing)}")
    if values.get("pause_states"):
        done_states = set(
            session.scalars(
                select(WorkflowState.key).where(
                    WorkflowState.key.in_(list(values["pause_states"])), WorkflowState.category == "done"
                )
            ).all()
        )
        if done_states:
            raise _unprocessable(
                f"Los estados finalizados no pueden pausar el SLA (detienen la resolución): {', '.join(sorted(done_states))}"
            )


def _dedupe(values: list[Any] | None) -> list[Any]:
    return list(dict.fromkeys(values or []))


@router.get("/sla/policies", response_model=list[PolicyOut], dependencies=[Depends(require_agent)])
def list_policies(session: Session = Depends(get_db)) -> list[dict]:
    policies = session.scalars(select(SlaPolicy).order_by(SlaPolicy.precedence, SlaPolicy.id)).all()
    return [policy_out(session, p) for p in policies]


@router.get("/sla/policies/{policy_id}", response_model=PolicyOut, dependencies=[Depends(require_agent)])
def get_policy(policy_id: int, session: Session = Depends(get_db)) -> dict:
    return policy_out(session, _get_or_404(session, SlaPolicy, policy_id, "Política"))


@router.post(
    "/sla/policies", response_model=PolicyOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_policy(payload: PolicyIn, session: Session = Depends(get_db)) -> dict:
    values = payload.model_dump()
    values["name"] = values["name"].strip()
    if _name_taken(session, SlaPolicy, values["name"]):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Ya existe una política con ese nombre")
    if not values.get("first_response_minutes") and not values.get("resolution_minutes"):
        raise _unprocessable("Defina al menos un objetivo: primera respuesta o resolución")
    _validate_policy_refs(session, values)
    now = utcnow()
    policy = SlaPolicy(
        name=values["name"],
        description=(values.get("description") or "").strip(),
        calendar_id=values["calendar_id"],
        priority_keys=_dedupe(values.get("priority_keys")),
        type_keys=_dedupe(values.get("type_keys")),
        department_ids=_dedupe(values.get("department_ids")),
        precedence=values["precedence"],
        first_response_minutes=values.get("first_response_minutes"),
        resolution_minutes=values.get("resolution_minutes"),
        warning_threshold_pct=values["warning_threshold_pct"],
        pause_categories=_dedupe(values.get("pause_categories")),
        pause_states=_dedupe(values.get("pause_states")),
        active=values["active"],
        created_at=now,
        updated_at=now,
    )
    session.add(policy)
    _commit(session, "Ya existe una política con ese nombre")
    return policy_out(session, policy)


@router.patch("/sla/policies/{policy_id}", response_model=PolicyOut, dependencies=[Depends(require_admin)])
def update_policy(policy_id: int, payload: PolicyPatch, session: Session = Depends(get_db)) -> dict:
    policy = _get_or_404(session, SlaPolicy, policy_id, "Política")
    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        if values["name"] is None:
            raise _unprocessable("El nombre no puede quedar vacío")
        values["name"] = values["name"].strip()
        if _name_taken(session, SlaPolicy, values["name"], exclude_id=policy.id):
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Ya existe una política con ese nombre")
    for required in ("calendar_id", "precedence", "warning_threshold_pct", "active"):
        if required in values and values[required] is None:
            raise _unprocessable(f"El campo {required} no puede ser nulo")
    _validate_policy_refs(session, values)
    first = values.get("first_response_minutes", policy.first_response_minutes)
    resolution = values.get("resolution_minutes", policy.resolution_minutes)
    if not first and not resolution:
        raise _unprocessable("Defina al menos un objetivo: primera respuesta o resolución")
    for field, value in values.items():
        if field in ("priority_keys", "type_keys", "department_ids", "pause_categories", "pause_states"):
            value = _dedupe(value)
        elif field == "description":
            value = (value or "").strip()
        setattr(policy, field, value)
    policy.updated_at = utcnow()
    _commit(session, "Ya existe una política con ese nombre")
    return policy_out(session, policy)


@router.delete(
    "/sla/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)]
)
def delete_policy(policy_id: int, session: Session = Depends(get_db)) -> Response:
    policy = _get_or_404(session, SlaPolicy, policy_id, "Política")
    used = session.scalar(select(func.count()).select_from(SlaInstance).where(SlaInstance.policy_id == policy.id))
    if used:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="La política ya se aplicó a tickets; desactívela en lugar de eliminarla",
        )
    session.delete(policy)
    _commit(session, "No se pudo eliminar la política")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sla/recalculate", response_model=RecalculateOut, dependencies=[Depends(require_admin)])
def recalculate_open_tickets(session: Session = Depends(get_db)) -> dict:
    """Re-evalúa la política de todos los tickets abiertos (tras cambiar políticas o calendarios).

    Cada ticket se procesa en su propia transacción con la fila del ticket bloqueada (mismo
    orden de bloqueo que las transiciones y la tarea de vencimientos), así no se retienen
    bloqueos de todos los tickets hasta el final. Las instancias vencidas no se tocan.
    """
    ticket_ids = session.scalars(
        select(Ticket.id)
        .join(WorkflowState, WorkflowState.id == Ticket.status_id)
        .where(WorkflowState.category != "done")
        .order_by(Ticket.id)
    ).all()
    session.commit()
    totals = {"tickets": 0, "created": 0, "cancelled": 0, "breached": 0}
    now = utcnow()
    for ticket_id in ticket_ids:
        try:
            ticket = session.scalar(
                select(Ticket)
                .where(Ticket.id == ticket_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if ticket is None:
                session.rollback()
                continue
            result = ensure_ticket_sla(session, ticket, now)
            session.commit()
        except (CalendarError, LookupError):
            session.rollback()
            logger.exception("No se pudo recalcular el SLA del ticket %s", ticket_id)
            continue
        totals["tickets"] += 1
        totals["created"] += result.created
        totals["cancelled"] += result.cancelled
        totals["breached"] += result.breached
    return totals


# --------------------------------------------------------------------------- ticket y reportes


@router.get("/tickets/{ticket_id}/sla")
def get_ticket_sla(
    ticket_id: int,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    """Instancias (incluidas las canceladas por recálculo) y bitácora SLA del ticket."""
    ticket = ticket_svc.get_visible_ticket(session, principal, ticket_id)
    return ticket_sla_detail(session, ticket)


def _date_range(date_from: Optional[date], date_to: Optional[date]) -> tuple[date, date, datetime, datetime]:
    zone = get_zone(settings.tz_business)
    today = datetime.now(zone).date()
    end_day = date_to or today
    start_day = date_from or (end_day - timedelta(days=29))
    if start_day > end_day:
        raise _unprocessable("La fecha inicial debe ser anterior o igual a la final")
    if (end_day - start_day).days > MAX_SUMMARY_DAYS:
        raise _unprocessable(f"El rango máximo es de {MAX_SUMMARY_DAYS} días")
    start = datetime.combine(start_day, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    return start_day, end_day, start, end


@router.get("/sla/summary")
def sla_summary(
    date_from: Optional[date] = Query(default=None, description="Fecha inicial (creación del ticket), por defecto hace 30 días"),
    date_to: Optional[date] = Query(default=None, description="Fecha final inclusive, por defecto hoy"),
    department_id: Optional[int] = None,
    policy_id: Optional[int] = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    """Cumplimiento por política y departamento de los tickets visibles creados en el rango.

    ``compliance_pct`` = cumplidas / (cumplidas + vencidas); las instancias en curso no cuentan.
    """
    start_day, end_day, start, end = _date_range(date_from, date_to)
    conditions = [
        visible_tickets_filter(principal),
        Ticket.created_at >= start,
        Ticket.created_at < end,
        SlaInstance.status != STATUS_CANCELLED,
    ]
    if department_id is not None:
        conditions.append(Ticket.department_id == department_id)
    if policy_id is not None:
        conditions.append(SlaInstance.policy_id == policy_id)
    rows = session.execute(
        select(
            SlaInstance.policy_id,
            Ticket.department_id,
            SlaInstance.metric,
            SlaInstance.status,
            func.count(SlaInstance.id),
        )
        .join(Ticket, Ticket.id == SlaInstance.ticket_id)
        .where(*conditions)
        .group_by(SlaInstance.policy_id, Ticket.department_id, SlaInstance.metric, SlaInstance.status)
    ).all()

    totals = {metric: empty_counts() for metric in METRICS}
    by_policy: dict[int, dict[str, Any]] = {}
    by_department: dict[int, dict[str, Any]] = {}
    for policy_key, department_key, metric, status_value, count in rows:
        if metric not in totals:
            continue
        add_count(totals[metric], status_value, count)
        bucket = by_policy.setdefault(policy_key, {metric_name: empty_counts() for metric_name in METRICS})
        add_count(bucket[metric], status_value, count)
        bucket = by_department.setdefault(department_key, {metric_name: empty_counts() for metric_name in METRICS})
        add_count(bucket[metric], status_value, count)

    policy_names = dict(session.execute(select(SlaPolicy.id, SlaPolicy.name)).all())
    department_names = dict(session.execute(select(Department.id, Department.name)).all())
    return {
        "date_from": start_day,
        "date_to": end_day,
        "totals": {metric: finalize_counts(bucket) for metric, bucket in totals.items()},
        "by_policy": sorted(
            (
                {
                    "policy_id": key,
                    "policy_name": policy_names.get(key, f"Política {key}"),
                    **{metric: finalize_counts(bucket[metric]) for metric in METRICS},
                }
                for key, bucket in by_policy.items()
            ),
            key=lambda item: item["policy_name"],
        ),
        "by_department": sorted(
            (
                {
                    "department_id": key,
                    "department_name": department_names.get(key, f"Departamento {key}"),
                    **{metric: finalize_counts(bucket[metric]) for metric in METRICS},
                }
                for key, bucket in by_department.items()
            ),
            key=lambda item: item["department_name"],
        ),
    }


@router.get("/sla/at-risk")
def tickets_at_risk(
    department_id: Optional[int] = None,
    mine: bool = Query(default=False, description="Solo tickets asignados a mí"),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_agent),
    session: Session = Depends(get_db),
) -> dict:
    """Métricas abiertas vencidas o que superaron el umbral de advertencia (tickets visibles)."""
    now = utcnow()
    conditions = [
        visible_tickets_filter(principal),
        SlaInstance.stopped_at.is_(None),
        SlaInstance.status.in_((STATUS_RUNNING, STATUS_PAUSED, STATUS_BREACHED)),
        or_(
            SlaInstance.status == STATUS_BREACHED,
            SlaInstance.warned_at.is_not(None),
            and_(SlaInstance.status == STATUS_RUNNING, SlaInstance.warning_at <= now),
        ),
    ]
    if department_id is not None:
        conditions.append(Ticket.department_id == department_id)
    if mine:
        conditions.append(Ticket.assignee_id == principal.user_id)
    instances = session.scalars(
        select(SlaInstance)
        .join(Ticket, Ticket.id == SlaInstance.ticket_id)
        .where(*conditions)
        .order_by(SlaInstance.target_at)
        .limit(1000)
    ).all()

    cache = SpecCache(session)
    entries: list[tuple[SlaInstance, int | None]] = []
    for instance in instances:
        try:
            remaining = remaining_business_seconds(cache.get(instance.calendar_id), instance, now)
        except (CalendarError, LookupError):
            remaining = None
        entries.append((instance, remaining))
    # Primero las vencidas (las más atrasadas arriba) y luego por tiempo restante.
    entries.sort(
        key=lambda e: (
            0 if e[0].status == STATUS_BREACHED else 1,
            e[1] if e[1] is not None else 0,
        )
    )
    breached_total = sum(1 for instance, _ in entries if instance.status == STATUS_BREACHED)
    selected = entries[:limit]

    ticket_ids = list(dict.fromkeys(instance.ticket_id for instance, _ in selected))
    tickets = session.scalars(
        select(Ticket).options(*ticket_svc.TICKET_LOAD_OPTIONS).where(Ticket.id.in_(ticket_ids))
    ).unique().all() if ticket_ids else []
    serialized = {item["id"]: item for item in ticket_svc.serialize_tickets(session, tickets)}
    policy_names = dict(session.execute(select(SlaPolicy.id, SlaPolicy.name)).all())

    items = []
    for instance, remaining in selected:
        ticket = serialized.get(instance.ticket_id)
        if ticket is None:
            continue
        items.append(
            {
                "ticket": ticket,
                "instance_id": instance.id,
                "metric": instance.metric,
                "label": METRIC_LABELS.get(instance.metric, instance.metric),
                "status": instance.status,
                "paused": instance.paused_at is not None,
                "target_at": instance.target_at,
                "breached_at": instance.breached_at,
                "warned_at": instance.warned_at,
                "remaining_business_seconds": remaining,
                "target_minutes": instance.target_minutes,
                "policy_id": instance.policy_id,
                "policy_name": policy_names.get(instance.policy_id),
            }
        )
    return {
        "computed_at": now,
        "counts": {"breached": breached_total, "at_risk": len(entries) - breached_total, "total": len(entries)},
        "items": items,
    }
