"""Consultas de reportes. Todas aplican ``visible_tickets_filter`` del usuario.

Las agregaciones se hacen en SQL (conteos, promedios, agrupaciones) con expresiones
portables entre PostgreSQL y SQLite; Python solo completa series (días sin datos)
y combina resultados pequeños (filas por departamento/agente/bucket).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models.core import Department, Priority, Team, Ticket, TicketType, User, WorkflowState
from app.services.reports.filters import ReportFilters
from app.services.reports.sql import local_date, seconds_between

Granularity = Literal["day", "week", "auto"]

OPEN_CONDITION = WorkflowState.category != "done"

# Buckets de antigüedad del backlog: (clave, etiqueta, desde_horas, hasta_horas)
AGING_BUCKETS: tuple[tuple[str, str, int, int | None], ...] = (
    ("lt_1d", "< 1 día", 0, 24),
    ("1_3d", "1–3 días", 24, 72),
    ("3_7d", "3–7 días", 72, 168),
    ("7_14d", "7–14 días", 168, 336),
    ("14_30d", "14–30 días", 336, 720),
    ("gt_30d", "> 30 días", 720, None),
)

MAX_ASSIGNEE_ROWS = 100


def _hours(seconds: float | None) -> float | None:
    if seconds is None:
        return None
    return round(float(seconds) / 3600.0, 2)


def _in_range(column: Any, filters: ReportFilters) -> Any:
    return and_(column >= filters.start, column < filters.end)


def _ticket_state_join(stmt: Any) -> Any:
    return stmt.join(WorkflowState, WorkflowState.id == Ticket.status_id)


# --------------------------------------------------------------------------- overview


def _period_metrics(session: Session, filters: ReportFilters) -> dict[str, Any]:
    scope = filters.scope_conditions()
    created_in_range = _in_range(Ticket.created_at, filters)

    created_row = session.execute(
        _ticket_state_join(
            select(
                func.count(Ticket.id),
                func.coalesce(func.sum(case((OPEN_CONDITION, 1), else_=0)), 0),
                func.avg(
                    case(
                        (
                            Ticket.first_response_at.is_not(None),
                            seconds_between(Ticket.created_at, Ticket.first_response_at),
                        ),
                        else_=None,
                    )
                ),
                func.count(Ticket.first_response_at),
            )
        ).where(*scope, created_in_range)
    ).one()

    resolved_row = session.execute(
        select(
            func.count(Ticket.id),
            func.avg(seconds_between(Ticket.created_at, Ticket.resolved_at)),
        ).where(*scope, _in_range(Ticket.resolved_at, filters))
    ).one()

    closed = session.scalar(
        select(func.count(Ticket.id)).where(*scope, _in_range(Ticket.closed_at, filters))
    )

    created = int(created_row[0] or 0)
    responded = int(created_row[3] or 0)
    return {
        "created": created,
        "created_still_open": int(created_row[1] or 0),
        "resolved": int(resolved_row[0] or 0),
        "closed": int(closed or 0),
        "mttr_hours": _hours(resolved_row[1]),
        "mean_first_response_hours": _hours(created_row[2]),
        "first_response_rate_pct": round(responded * 100.0 / created, 1) if created else None,
    }


def overview(session: Session, filters: ReportFilters) -> dict[str, Any]:
    """KPIs del periodo, backlog actual y comparativa con el periodo anterior."""
    current = _period_metrics(session, filters)
    previous = _period_metrics(session, filters.previous_period())

    backlog_row = session.execute(
        _ticket_state_join(
            select(
                func.count(Ticket.id),
                func.coalesce(func.sum(case((Ticket.assignee_id.is_(None), 1), else_=0)), 0),
                func.coalesce(func.sum(case((WorkflowState.category == "waiting", 1), else_=0)), 0),
            )
        ).where(*filters.scope_conditions(), OPEN_CONDITION)
    ).one()

    return {
        "filters": filters.as_dict(),
        "generated_at": utcnow(),
        # "open" = tickets creados en el periodo que siguen abiertos hoy.
        "open": current["created_still_open"],
        "created": current["created"],
        "resolved": current["resolved"],
        "closed": current["closed"],
        # Backlog = todos los tickets abiertos ahora (independiente del rango de fechas).
        "backlog": int(backlog_row[0] or 0),
        "backlog_unassigned": int(backlog_row[1] or 0),
        "backlog_waiting": int(backlog_row[2] or 0),
        "mttr_hours": current["mttr_hours"],
        "mean_first_response_hours": current["mean_first_response_hours"],
        "first_response_rate_pct": current["first_response_rate_pct"],
        "previous": {
            "from": filters.previous_period().date_from.isoformat(),
            "to": filters.previous_period().date_to.isoformat(),
            "created": previous["created"],
            "resolved": previous["resolved"],
            "closed": previous["closed"],
            "mttr_hours": previous["mttr_hours"],
            "mean_first_response_hours": previous["mean_first_response_hours"],
        },
    }


# --------------------------------------------------------------------------- tendencia


def _daily_counts(session: Session, filters: ReportFilters, column: Any) -> dict[str, int]:
    day = local_date(column, filters.tz_name, reference=filters.end).label("day")
    rows = session.execute(
        select(day, func.count(Ticket.id))
        .where(*filters.scope_conditions(), _in_range(column, filters))
        .group_by(day)
    ).all()
    return {str(row[0]): int(row[1]) for row in rows if row[0] is not None}


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def trend(session: Session, filters: ReportFilters, granularity: Granularity = "auto") -> dict[str, Any]:
    """Creados vs resueltos por día o semana (semanas ISO, inician en lunes)."""
    if granularity == "auto":
        granularity = "week" if filters.days > 62 else "day"
    created = _daily_counts(session, filters, Ticket.created_at)
    resolved = _daily_counts(session, filters, Ticket.resolved_at)

    buckets: dict[date, dict[str, int]] = {}
    current = filters.date_from
    while current <= filters.date_to:
        key = current if granularity == "day" else _week_start(current)
        bucket = buckets.setdefault(key, {"created": 0, "resolved": 0})
        iso = current.isoformat()
        bucket["created"] += created.get(iso, 0)
        bucket["resolved"] += resolved.get(iso, 0)
        current += timedelta(days=1)

    points = []
    cumulative = 0
    for key in sorted(buckets):
        values = buckets[key]
        cumulative += values["created"] - values["resolved"]
        points.append(
            {
                "period": key.isoformat(),
                "created": values["created"],
                "resolved": values["resolved"],
                "net": cumulative,
            }
        )
    return {
        "filters": filters.as_dict(),
        "granularity": granularity,
        "points": points,
        "totals": {
            "created": sum(p["created"] for p in points),
            "resolved": sum(p["resolved"] for p in points),
        },
    }


# --------------------------------------------------------------------------- por departamento


def by_department(session: Session, filters: ReportFilters) -> dict[str, Any]:
    scope = filters.scope_conditions()
    rows: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"created": 0, "resolved": 0, "backlog": 0, "mttr_hours": None}
    )

    for dept_id, count in session.execute(
        select(Ticket.department_id, func.count(Ticket.id))
        .where(*scope, _in_range(Ticket.created_at, filters))
        .group_by(Ticket.department_id)
    ):
        rows[dept_id]["created"] = int(count)

    for dept_id, count, avg_seconds in session.execute(
        select(
            Ticket.department_id,
            func.count(Ticket.id),
            func.avg(seconds_between(Ticket.created_at, Ticket.resolved_at)),
        )
        .where(*scope, _in_range(Ticket.resolved_at, filters))
        .group_by(Ticket.department_id)
    ):
        rows[dept_id]["resolved"] = int(count)
        rows[dept_id]["mttr_hours"] = _hours(avg_seconds)

    for dept_id, count in session.execute(
        _ticket_state_join(select(Ticket.department_id, func.count(Ticket.id)))
        .where(*scope, OPEN_CONDITION)
        .group_by(Ticket.department_id)
    ):
        rows[dept_id]["backlog"] = int(count)

    names = (
        dict(session.execute(select(Department.id, Department.name).where(Department.id.in_(list(rows)))).all())
        if rows
        else {}
    )
    items = [
        {"department_id": dept_id, "department": names.get(dept_id, f"#{dept_id}"), **values}
        for dept_id, values in rows.items()
    ]
    items.sort(key=lambda item: (-item["created"], -item["backlog"], item["department"]))
    return {"filters": filters.as_dict(), "items": items}


# --------------------------------------------------------------------------- por agente


def by_assignee(session: Session, filters: ReportFilters) -> dict[str, Any]:
    """Carga actual (backlog asignado) y resueltos del periodo por agente asignado."""
    scope = filters.scope_conditions()
    rows: dict[int | None, dict[str, Any]] = defaultdict(
        lambda: {"open": 0, "open_critical": 0, "resolved": 0, "mttr_hours": None}
    )

    for assignee_id, count, critical in session.execute(
        _ticket_state_join(
            select(
                Ticket.assignee_id,
                func.count(Ticket.id),
                func.coalesce(func.sum(case((Priority.key == "critical", 1), else_=0)), 0),
            ).join(Priority, Priority.id == Ticket.priority_id)
        )
        .where(*scope, OPEN_CONDITION)
        .group_by(Ticket.assignee_id)
    ):
        rows[assignee_id]["open"] = int(count)
        rows[assignee_id]["open_critical"] = int(critical or 0)

    for assignee_id, count, avg_seconds in session.execute(
        select(
            Ticket.assignee_id,
            func.count(Ticket.id),
            func.avg(seconds_between(Ticket.created_at, Ticket.resolved_at)),
        )
        .where(*scope, _in_range(Ticket.resolved_at, filters))
        .group_by(Ticket.assignee_id)
    ):
        rows[assignee_id]["resolved"] = int(count)
        rows[assignee_id]["mttr_hours"] = _hours(avg_seconds)

    user_ids = [uid for uid in rows if uid is not None]
    names = (
        dict(session.execute(select(User.id, User.display_name).where(User.id.in_(user_ids))).all())
        if user_ids
        else {}
    )
    items = [
        {
            "assignee_id": uid,
            "assignee": names.get(uid, f"#{uid}") if uid is not None else "Sin asignar",
            **values,
        }
        for uid, values in rows.items()
    ]
    items.sort(key=lambda item: (item["assignee_id"] is None, -item["open"], -item["resolved"], item["assignee"]))
    truncated = len(items) > MAX_ASSIGNEE_ROWS
    return {"filters": filters.as_dict(), "items": items[:MAX_ASSIGNEE_ROWS], "truncated": truncated}


# --------------------------------------------------------------------------- antigüedad


def aging(session: Session, filters: ReportFilters, now: datetime | None = None) -> dict[str, Any]:
    """Backlog actual (abiertos) por antigüedad y prioridad. No depende del rango de fechas."""
    now = (now or utcnow()).astimezone(timezone.utc)
    whens = []
    for key, _label, _min_h, max_h in AGING_BUCKETS:
        if max_h is None:
            continue
        whens.append((Ticket.created_at > now - timedelta(hours=max_h), key))
    # Subconsulta: el CASE (con parámetros) se agrupa por su columna, no repitiendo la expresión.
    open_tickets = (
        _ticket_state_join(
            select(
                case(*whens, else_=AGING_BUCKETS[-1][0]).label("bucket"),
                Priority.key.label("priority_key"),
            )
        )
        .join(Priority, Priority.id == Ticket.priority_id)
        .where(*filters.scope_conditions(), OPEN_CONDITION)
        .subquery()
    )
    rows = session.execute(
        select(open_tickets.c.bucket, open_tickets.c.priority_key, func.count())
        .group_by(open_tickets.c.bucket, open_tickets.c.priority_key)
    ).all()

    priorities = session.execute(select(Priority.key, Priority.name).order_by(Priority.rank)).all()
    counts: dict[str, dict[str, int]] = defaultdict(dict)
    for bucket_key, priority_key, count in rows:
        counts[bucket_key][priority_key] = int(count)

    oldest = session.execute(
        _ticket_state_join(select(func.min(Ticket.created_at))).where(
            *filters.scope_conditions(), OPEN_CONDITION
        )
    ).scalar()

    items = []
    for key, label, min_h, max_h in AGING_BUCKETS:
        by_priority = {p_key: counts[key].get(p_key, 0) for p_key, _name in priorities}
        items.append(
            {
                "bucket": key,
                "label": label,
                "min_hours": min_h,
                "max_hours": max_h,
                "total": sum(by_priority.values()),
                "by_priority": by_priority,
            }
        )
    oldest_age_hours = None
    if oldest is not None:
        oldest_aware = oldest if oldest.tzinfo else oldest.replace(tzinfo=timezone.utc)
        oldest_age_hours = round((now - oldest_aware).total_seconds() / 3600.0, 1)
    return {
        "filters": filters.as_dict(),
        "priorities": [{"key": k, "name": n} for k, n in priorities],
        "items": items,
        "total": sum(item["total"] for item in items),
        "oldest_age_hours": oldest_age_hours,
    }


# --------------------------------------------------------------------------- exportación


EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("key", "Clave"),
    ("title", "Título"),
    ("status", "Estado"),
    ("category", "Categoría"),
    ("priority", "Prioridad"),
    ("type", "Tipo"),
    ("department", "Departamento"),
    ("team", "Equipo"),
    ("requester", "Solicitante"),
    ("assignee", "Asignado"),
    ("created_at", "Creado"),
    ("first_response_at", "Primera respuesta"),
    ("resolved_at", "Resuelto"),
    ("closed_at", "Cerrado"),
    ("first_response_hours", "Horas a primera respuesta"),
    ("resolution_hours", "Horas a resolución"),
)

MAX_EXPORT_ROWS = 50_000


def export_rows(session: Session, filters: ReportFilters, *, batch_size: int = 1000):
    """Itera (en lotes) los tickets creados en el periodo como diccionarios planos."""
    requester = User.__table__.alias("requester_user")
    assignee = User.__table__.alias("assignee_user")
    stmt = (
        select(
            Ticket.id,
            Ticket.public_key,
            Ticket.title,
            WorkflowState.name,
            WorkflowState.category,
            Priority.name,
            TicketType.name,
            Department.name,
            Team.name,
            requester.c.display_name,
            assignee.c.display_name,
            Ticket.created_at,
            Ticket.first_response_at,
            Ticket.resolved_at,
            Ticket.closed_at,
        )
        .join(WorkflowState, WorkflowState.id == Ticket.status_id)
        .join(Priority, Priority.id == Ticket.priority_id)
        .join(TicketType, TicketType.id == Ticket.type_id)
        .join(Department, Department.id == Ticket.department_id)
        .outerjoin(Team, Team.id == Ticket.team_id)
        .join(requester, requester.c.id == Ticket.requester_id)
        .outerjoin(assignee, assignee.c.id == Ticket.assignee_id)
        .where(*filters.scope_conditions(), _in_range(Ticket.created_at, filters))
        .order_by(Ticket.created_at, Ticket.id)
        .limit(MAX_EXPORT_ROWS)
        .execution_options(yield_per=batch_size)
    )
    for row in session.execute(stmt):
        (
            ticket_id, public_key, title, status_name, category, priority_name, type_name,
            dept_name, team_name, requester_name, assignee_name,
            created_at, first_response_at, resolved_at, closed_at,
        ) = row
        yield {
            "key": public_key or f"ESD-{ticket_id}",
            "title": title,
            "status": status_name,
            "category": category,
            "priority": priority_name,
            "type": type_name,
            "department": dept_name,
            "team": team_name,
            "requester": requester_name,
            "assignee": assignee_name,
            "created_at": created_at,
            "first_response_at": first_response_at,
            "resolved_at": resolved_at,
            "closed_at": closed_at,
            "first_response_hours": _elapsed_hours(created_at, first_response_at),
            "resolution_hours": _elapsed_hours(created_at, resolved_at),
        }


def _elapsed_hours(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 3600.0, 2)
