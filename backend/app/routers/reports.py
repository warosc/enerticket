"""Reportes: tablero (resumen), generador de reportes y plantillas.

Cualquier usuario autenticado puede usarlos: todos los datos se limitan a los tickets que
puede ver (``visible_tickets_filter``: admin todo, agente su área, solicitante los suyos,
aprobador/gerencia por extensión) y los filtros solo reducen ese alcance.

Tablero — parámetros comunes: ``from`` y ``to`` (fechas ``YYYY-MM-DD`` en la zona
``TZ_BUSINESS``, ambas incluidas; por defecto los últimos 30 días), ``department_id`` y ``team_id``.

Generador — ``GET /reports/builder/meta``, ``POST /reports/builder/run`` y
``POST /reports/builder/export.xlsx``. Plantillas — ``/reports/templates`` (CRUD, duplicar,
ejecutar y exportar). Ver ``app.services.reports.builder`` y ``app.services.reports.templates``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, get_db
from app.core.security import Principal, get_principal
from app.services import reports as svc
from app.services.reports import builder
from app.services.reports import templates as tpl
from app.services.reports.csv_export import iter_csv
from app.services.reports.xlsx_export import XLSX_MEDIA_TYPE
from app.services.storage.validation import content_disposition

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])

# Cualquier usuario autenticado: el alcance lo da ``visible_tickets_filter``.
ReportUser = Depends(get_principal)


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


# --------------------------------------------------------------------------- generador


def _xlsx_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": content_disposition(filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/builder/meta")
def builder_meta(principal: Principal = ReportUser, session: Session = Depends(get_db)) -> dict[str, Any]:
    """Dimensiones, métricas, campos de fecha, periodos, límites y alcance del usuario."""
    return builder.meta(session, principal)


@router.post("/builder/run")
def builder_run(
    definition: builder.ReportDefinition,
    principal: Principal = ReportUser,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Ejecuta una definición (no se guarda) sobre los tickets visibles del usuario."""
    return builder.run_report(session, principal, definition)


@router.post(
    "/builder/export.xlsx",
    response_class=Response,
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}}},
)
def builder_export(
    payload: builder.ExportRequest,
    principal: Principal = ReportUser,
    session: Session = Depends(get_db),
) -> Response:
    """Excel: una hoja por sección y, si ``include_detail``, la hoja «Detalle» (máx. 50 000 filas)."""
    content, filename, _result = tpl.render_xlsx(
        session, principal, payload.definition,
        include_detail=payload.include_detail, detail_columns=payload.detail_columns,
    )
    logger.info("Exportación .xlsx del generador por el usuario %s", principal.user_id)
    return _xlsx_response(content, filename)


# --------------------------------------------------------------------------- plantillas


def _period_override(
    period: Optional[builder.Period] = Query(default=None, description="Otro periodo para esta ejecución"),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
) -> dict[str, Any]:
    return {"period": period, "date_from": date_from, "date_to": date_to}


@router.get("/templates")
def list_templates(principal: Principal = ReportUser, session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """Propias, compartidas conmigo (rol o departamento) y de fábrica (admin: todas)."""
    return [tpl.template_out(session, principal, t) for t in tpl.list_templates(session, principal)]


@router.post("/templates", status_code=status.HTTP_201_CREATED)
def create_template(
    payload: tpl.TemplateIn, principal: Principal = ReportUser, session: Session = Depends(get_db)
) -> dict[str, Any]:
    template = tpl.create_template(session, principal, payload)
    session.commit()
    return tpl.template_out(session, principal, template)


@router.get("/templates/{template_id}")
def get_template(
    template_id: int, principal: Principal = ReportUser, session: Session = Depends(get_db)
) -> dict[str, Any]:
    return tpl.template_out(session, principal, tpl.get_template(session, principal, template_id))


@router.patch("/templates/{template_id}")
def update_template(
    template_id: int,
    payload: tpl.TemplatePatch,
    principal: Principal = ReportUser,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    template = tpl.get_template(session, principal, template_id, edit=True)
    tpl.update_template(session, principal, template, payload)
    session.commit()
    return tpl.template_out(session, principal, template)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: int, principal: Principal = ReportUser, session: Session = Depends(get_db)
) -> Response:
    template = tpl.get_template(session, principal, template_id, edit=True)
    session.delete(template)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/templates/{template_id}/duplicate", status_code=status.HTTP_201_CREATED)
def duplicate_template(
    template_id: int, principal: Principal = ReportUser, session: Session = Depends(get_db)
) -> dict[str, Any]:
    source = tpl.get_template(session, principal, template_id)
    template = tpl.duplicate_template(session, principal, source)
    session.commit()
    return tpl.template_out(session, principal, template)


@router.post("/templates/{template_id}/run")
def run_template(
    template_id: int,
    override: dict[str, Any] = Depends(_period_override),
    principal: Principal = ReportUser,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Ejecuta la plantilla con el alcance de quien la ejecuta (opcional: otro periodo)."""
    template = tpl.get_template(session, principal, template_id)
    definition = tpl.definition_with_period(builder.parse_definition(template.definition), **override)
    return builder.run_report(session, principal, definition)


@router.get(
    "/templates/{template_id}/export.xlsx",
    response_class=Response,
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}}},
)
def export_template(
    template_id: int,
    include_detail: bool = Query(default=True),
    override: dict[str, Any] = Depends(_period_override),
    principal: Principal = ReportUser,
    session: Session = Depends(get_db),
) -> Response:
    """Excel de la plantilla con el alcance de quien la exporta (opcional: otro periodo)."""
    template = tpl.get_template(session, principal, template_id)
    definition = tpl.definition_with_period(builder.parse_definition(template.definition), **override)
    content, filename, _result = tpl.render_xlsx(
        session, principal, definition, include_detail=include_detail
    )
    logger.info("Exportación .xlsx de la plantilla %s por el usuario %s", template_id, principal.user_id)
    return _xlsx_response(content, filename)
