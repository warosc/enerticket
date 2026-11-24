"""Cumplimiento de SLA para reportes.

Se basa en ``sla_instances`` del módulo SLA (``app.models.sla``), importado de forma
tolerante: si el módulo no está instalado el reporte responde ``available: false``.
El módulo SLA (u otro) puede sustituir el cálculo registrando un proveedor con
``register_sla_report_provider(fn)``, donde ``fn(session, filters) -> dict`` devuelve
la misma forma que ``sla_compliance``.

Semántica: se consideran las instancias de tickets visibles cuyo reloj inició en el
periodo (``started_at``). Cumplimiento = met / (met + breached); las canceladas no cuentan.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models.core import Priority, Ticket
from app.services.reports.filters import ReportFilters

logger = logging.getLogger(__name__)

SlaReportProvider = Callable[[Session, ReportFilters], dict[str, Any]]
_provider: SlaReportProvider | None = None

METRIC_LABELS = {"first_response": "Primera respuesta", "resolution": "Resolución"}


def register_sla_report_provider(fn: SlaReportProvider | None) -> SlaReportProvider | None:
    """Reemplaza el cálculo por defecto (``None`` restaura el comportamiento interno)."""
    global _provider
    _provider = fn
    return fn


def _sla_instance_model() -> Any | None:
    try:
        module = importlib.import_module("app.models.sla")
    except ImportError:
        return None
    model = getattr(module, "SlaInstance", None)
    required = ("ticket_id", "metric", "status", "started_at")
    if model is None or not all(hasattr(model, attr) for attr in required):
        return None
    return model


def _pct(met: int, breached: int) -> float | None:
    decided = met + breached
    return round(met * 100.0 / decided, 1) if decided else None


def _empty(filters: ReportFilters, reason: str) -> dict[str, Any]:
    return {
        "filters": filters.as_dict(),
        "available": False,
        "reason": reason,
        "metrics": [],
        "by_priority": [],
    }


def sla_compliance(session: Session, filters: ReportFilters) -> dict[str, Any]:
    if _provider is not None:
        try:
            return _provider(session, filters)
        except Exception:
            logger.exception("El proveedor de reportes SLA falló; se usa el cálculo interno")

    model = _sla_instance_model()
    if model is None:
        return _empty(filters, "El módulo SLA no está instalado")

    now = utcnow()
    status_col = model.status
    started_in_range = and_(model.started_at >= filters.start, model.started_at < filters.end)
    at_risk_condition = (
        and_(status_col == "running", model.warning_at.is_not(None), model.warning_at <= now)
        if hasattr(model, "warning_at")
        else None
    )

    def counts(status_value: str) -> Any:
        return func.coalesce(func.sum(case((status_col == status_value, 1), else_=0)), 0)

    columns = [
        model.metric,
        func.count(model.id),
        counts("met"),
        counts("breached"),
        counts("running"),
        counts("paused"),
        counts("cancelled"),
    ]
    if at_risk_condition is not None:
        columns.append(func.coalesce(func.sum(case((at_risk_condition, 1), else_=0)), 0))

    base = (
        select(*columns)
        .join(Ticket, Ticket.id == model.ticket_id)
        .where(*filters.scope_conditions(), started_in_range)
        .group_by(model.metric)
    )
    metrics = []
    for row in session.execute(base):
        metric, total, met, breached, running, paused, cancelled = row[:7]
        at_risk = row[7] if len(row) > 7 else 0
        metrics.append(
            {
                "metric": metric,
                "label": METRIC_LABELS.get(metric, metric),
                "total": int(total),
                "met": int(met),
                "breached": int(breached),
                "running": int(running),
                "paused": int(paused),
                "cancelled": int(cancelled),
                "at_risk": int(at_risk or 0),
                "compliance_pct": _pct(int(met), int(breached)),
            }
        )
    metrics.sort(key=lambda m: list(METRIC_LABELS).index(m["metric"]) if m["metric"] in METRIC_LABELS else 99)

    by_priority_rows = session.execute(
        select(
            Priority.key,
            Priority.name,
            Priority.rank,
            model.metric,
            counts("met"),
            counts("breached"),
        )
        .join(Ticket, Ticket.id == model.ticket_id)
        .join(Priority, Priority.id == Ticket.priority_id)
        .where(*filters.scope_conditions(), started_in_range)
        .group_by(Priority.key, Priority.name, Priority.rank, model.metric)
    ).all()
    grouped: dict[str, dict[str, Any]] = {}
    for key, name, rank, metric, met, breached in by_priority_rows:
        item = grouped.setdefault(key, {"priority": key, "name": name, "rank": rank, "metrics": {}})
        item["metrics"][metric] = {
            "met": int(met),
            "breached": int(breached),
            "compliance_pct": _pct(int(met), int(breached)),
        }
    by_priority = sorted(grouped.values(), key=lambda item: -int(item["rank"]))

    return {
        "filters": filters.as_dict(),
        "available": True,
        "reason": None,
        "metrics": metrics,
        "by_priority": by_priority,
    }
