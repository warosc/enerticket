"""Reportes avanzados: KPIs, tendencias, carga por área/agente, antigüedad, SLA y CSV.

Todas las consultas parten de ``visible_tickets_filter(principal)``: un usuario solo
agrega datos de los tickets que puede ver.
"""

from app.services.reports.filters import ReportFilters, build_filters
from app.services.reports.queries import (
    AGING_BUCKETS,
    EXPORT_COLUMNS,
    MAX_EXPORT_ROWS,
    aging,
    by_assignee,
    by_department,
    export_rows,
    overview,
    trend,
)
from app.services.reports.sla import register_sla_report_provider, sla_compliance

__all__ = [
    "AGING_BUCKETS",
    "EXPORT_COLUMNS",
    "MAX_EXPORT_ROWS",
    "ReportFilters",
    "aging",
    "build_filters",
    "by_assignee",
    "by_department",
    "export_rows",
    "overview",
    "register_sla_report_provider",
    "sla_compliance",
    "trend",
]
