"""Reportes avanzados (agentes y administradores).

Parámetros comunes: ``from`` y ``to`` (fechas ``YYYY-MM-DD`` en la zona ``TZ_BUSINESS``,
ambas incluidas; por defecto los últimos 30 días), ``department_id`` y ``team_id``.
Todos los datos se limitan a los tickets visibles para el usuario (``visible_tickets_filter``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, get_db
from app.core.security import ROLE_AGENT, Principal, require_roles
from app.services import reports as svc
from app.services.reports.csv_export import iter_csv
from app.services.storage.validation import content_disposition

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])

ReportUser = Depends(require_roles(ROLE_AGENT))


def report_filters(
    date_from: Optional[date] = Query(default=None, alias="from", description="Fecha inicial (incluida)"),
    date_to: Optional[date] = Query(default=None, alias="to", description="Fecha final (incluida)"),
    department_id: Optional[int] = Query(default=None, ge=1),
    team_id: Optional[int] = Query(default=None, ge=1),
    principal: Principal = ReportUser,
) -> svc.ReportFilters:
    return svc.build_filters(principal, date_from, date_to, department_id, team_id)


@router.get("/overview")
def overview(
    filters: svc.ReportFilters = Depends(report_filters), session: Session = Depends(get_db)
) -> dict[str, Any]:
    """KPIs: creados, resueltos, cerrados, abiertos, backlog, MTTR y primera respuesta."""
    return svc.overview(session, filters)


@router.get("/trend")
def trend(
    granularity: Literal["day", "week", "auto"] = "auto",
    filters: svc.ReportFilters = Depends(report_filters),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Creados vs resueltos por día o semana (``auto``: semanas si el rango supera 62 días)."""
    return svc.trend(session, filters, granularity)


@router.get("/by-department")
def by_department(
    filters: svc.ReportFilters = Depends(report_filters), session: Session = Depends(get_db)
) -> dict[str, Any]:
    return svc.by_department(session, filters)


@router.get("/by-assignee")
def by_assignee(
    filters: svc.ReportFilters = Depends(report_filters), session: Session = Depends(get_db)
) -> dict[str, Any]:
    return svc.by_assignee(session, filters)


@router.get("/aging")
def aging(
    filters: svc.ReportFilters = Depends(report_filters), session: Session = Depends(get_db)
) -> dict[str, Any]:
    """Antigüedad del backlog actual por buckets y prioridad (no usa el rango de fechas)."""
    return svc.aging(session, filters)


@router.get("/sla")
def sla(
    filters: svc.ReportFilters = Depends(report_filters), session: Session = Depends(get_db)
) -> dict[str, Any]:
    """Cumplimiento de SLA por métrica y prioridad (``available: false`` sin módulo SLA)."""
    return svc.sla_compliance(session, filters)


def _export_lines(filters: svc.ReportFilters) -> Iterator[str]:
    # Sesión propia: las dependencias con ``yield`` se cierran antes de enviar el streaming.
    session = SessionLocal()
    try:
        keys = [key for key, _label in svc.EXPORT_COLUMNS]
        rows = ([row[key] for key in keys] for row in svc.export_rows(session, filters))
        yield from iter_csv([label for _key, label in svc.EXPORT_COLUMNS], rows, tz=filters.tz)
    except Exception:
        logger.exception("Error generando la exportación CSV de reportes")
        raise
    finally:
        session.close()


@router.get(
    "/export.csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}}},
)
def export_csv(filters: svc.ReportFilters = Depends(report_filters)) -> StreamingResponse:
    """Tickets creados en el periodo (máx. 50 000 filas), con celdas protegidas contra fórmulas."""
    filename = f"tickets_{filters.date_from:%Y%m%d}_{filters.date_to:%Y%m%d}.csv"
    logger.info(
        "Exportación CSV por el usuario %s (%s → %s)",
        filters.principal.user_id, filters.date_from, filters.date_to,
    )
    return StreamingResponse(
        _export_lines(filters),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": content_disposition(filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
