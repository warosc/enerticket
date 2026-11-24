"""Plantillas de reportes de fábrica (``is_system``), idempotentes por nombre.

Se crean si no existen; si ya existen como plantilla de sistema se actualiza su definición
(no son editables por los usuarios, así una corrección de fábrica llega en el siguiente
arranque). Una plantilla de usuario con el mismo nombre no se toca.

1. «Informe mensual de proveedor»: mes anterior por fecha de solución; secciones Incidencias,
   Peticiones y Órdenes de trabajo; filas categoría, columnas mes.
2. «Desempeño por técnico»: categoría y técnico asignado, cantidad y horas hábiles.
3. «Tickets por día»: este mes, días × tipo.
4. «Tickets por gerencia solicitante»: gerencia y departamento solicitante × tipo.
5. «Cumplimiento de SLA por categoría»: departamento de atención y categoría.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.reports import ReportTemplate
from app.services.reports.builder import ReportDefinition

logger = logging.getLogger(__name__)

PROVIDER_METRICS = [
    "resolution_hours_avg",
    "count",
    "resolution_hours_sum",
    "sla_target_hours",
    "sla_compliance_pct",
]


def _provider_section(title: str, type_key: str) -> dict[str, Any]:
    return {
        "title": title,
        "rows": ["category"],
        "columns": ["month"],
        "metrics": PROVIDER_METRICS,
        "filters": {"type_keys": [type_key]},
        "totals": True,
    }


TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "name": "Informe mensual de proveedor",
        "description": (
            "Tickets resueltos el mes anterior por tipo (incidencias, peticiones y órdenes de "
            "trabajo) y categoría: tiempo hábil promedio y total, objetivo y cumplimiento de SLA. "
            "Filtre por proveedor o departamento para el informe de cada proveedor."
        ),
        "definition": {
            "title": "Informe mensual de proveedor",
            "date_field": "resolved_at",
            "period": "last_month",
            "sections": [
                _provider_section("Incidencias", "incident"),
                _provider_section("Peticiones", "service_request"),
                _provider_section("Órdenes de trabajo", "work_order"),
            ],
        },
    },
    {
        "name": "Desempeño por técnico",
        "description": "Tickets resueltos el mes anterior por categoría y técnico asignado.",
        "definition": {
            "title": "Desempeño por técnico",
            "date_field": "resolved_at",
            "period": "last_month",
            "sections": [
                {
                    "title": "Desempeño por técnico",
                    "rows": ["category", "assignee"],
                    "metrics": ["count", "resolution_hours_avg", "resolution_hours_sum"],
                    "totals": True,
                }
            ],
        },
    },
    {
        "name": "Tickets por día",
        "description": "Tickets creados cada día del mes en curso, por tipo.",
        "definition": {
            "title": "Tickets por día",
            "date_field": "created_at",
            "period": "this_month",
            "sections": [
                {
                    "title": "Tickets por día",
                    "rows": ["day"],
                    "columns": ["type"],
                    "metrics": ["count"],
                    "totals": True,
                }
            ],
        },
    },
    {
        "name": "Tickets por gerencia solicitante",
        "description": "Tickets creados el mes anterior por gerencia y departamento solicitante, por tipo.",
        "definition": {
            "title": "Tickets por gerencia solicitante",
            "date_field": "created_at",
            "period": "last_month",
            "sections": [
                {
                    "title": "Por gerencia solicitante",
                    "rows": ["gerencia", "requester_department"],
                    "columns": ["type"],
                    "metrics": ["count"],
                    "totals": True,
                }
            ],
        },
    },
    {
        "name": "Cumplimiento de SLA por categoría",
        "description": "Tickets resueltos el mes anterior: cumplimiento, vencidos y objetivo de SLA por categoría.",
        "definition": {
            "title": "Cumplimiento de SLA por categoría",
            "date_field": "resolved_at",
            "period": "last_month",
            "sections": [
                {
                    "title": "Cumplimiento de SLA",
                    "rows": ["department", "category"],
                    "metrics": ["count", "sla_compliance_pct", "sla_breached_count", "sla_target_hours"],
                    "totals": True,
                }
            ],
        },
    },
)


def run(session: Session) -> None:
    for spec in TEMPLATES:
        definition = ReportDefinition.model_validate(spec["definition"]).model_dump(mode="json")
        existing = session.scalars(
            select(ReportTemplate).where(ReportTemplate.name == spec["name"]).order_by(ReportTemplate.id)
        ).all()
        system = next((t for t in existing if t.is_system), None)
        if system is not None:
            if system.definition != definition or system.description != spec["description"]:
                system.definition = definition
                system.description = spec["description"]
            continue
        if existing:
            # Un usuario ya usa ese nombre: se crea igual la de fábrica (se distinguen por is_system).
            logger.info("Ya existe una plantilla de usuario «%s»; se agrega la de fábrica", spec["name"])
        session.add(
            ReportTemplate(
                name=spec["name"],
                description=spec["description"],
                owner_id=None,
                is_system=True,
                definition=definition,
                shared_roles=[],
                shared_department_ids=[],
                schedule=None,
            )
        )
    session.flush()
