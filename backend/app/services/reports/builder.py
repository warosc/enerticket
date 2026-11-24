"""Generador de reportes genérico: filas × columnas × métricas, por secciones.

Reglas de seguridad y de cálculo:

- Todo reporte parte de ``visible_tickets_filter(principal)``: los filtros del usuario
  (definición y sección) se combinan con AND y solo pueden **reducir** ese alcance.
- Dimensiones y métricas son una lista blanca (``DIMENSIONS`` / ``METRICS``); una clave
  desconocida se rechaza al validar la definición (422).
- Las dimensiones de fecha (``day``, ``week``, ``month``, ``year``) se calculan sobre
  ``date_field`` en la zona de negocio (``TZ_BUSINESS``, America/Guatemala) con
  ``local_date`` de ``services/reports/sql.py`` (portable PostgreSQL / SQLite).
- SQL devuelve una fila por ticket con las claves de las dimensiones y los insumos de las
  métricas; Python agrega por celda, por fila, por columna y en total. Así los totales,
  promedios, medianas y porcentajes se recalculan sobre los tickets (nunca promedio de
  promedios).
- Horas de resolución = ``tickets.business_resolution_minutes`` (horas hábiles, ya
  descuenta pausas); objetivo y cumplimiento = instancia SLA ``resolution`` vigente
  (no cancelada) del ticket: cumplimiento = cumplidas / (cumplidas + vencidas).
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.core.db import utcnow
from app.core.permissions import visible_tickets_filter
from app.core.security import Principal
from app.models.catalog import Gerencia, Provider, TicketCategory, TicketSubcategory
from app.models.core import (
    TICKET_CHANNELS,
    Department,
    Priority,
    Team,
    Ticket,
    TicketType,
    User,
    WorkflowState,
)
from app.models.sla import SlaInstance
from app.services.reports.sql import local_date

# --------------------------------------------------------------------------- lista blanca

DimensionKey = Literal[
    "type", "department", "team", "gerencia", "requester_department", "category", "subcategory",
    "provider", "assignee", "resolved_by", "priority", "vip", "channel", "status",
    "day", "week", "month", "year",
]
MetricKey = Literal[
    "count", "resolution_hours_avg", "resolution_hours_sum", "resolution_hours_median",
    "resolution_hours_max", "first_response_hours_avg", "sla_target_hours", "sla_compliance_pct",
    "sla_breached_count", "open_count",
]
DateField = Literal["created_at", "resolved_at"]
Period = Literal["custom", "this_month", "last_month", "last_30_days", "this_year", "last_year"]
ChannelKey = Literal["portal", "email", "phone", "other"]

# clave → (etiqueta, tipo)
DIMENSIONS: dict[str, tuple[str, str]] = {
    "type": ("Tipo", "category"),
    "department": ("Departamento de atención", "category"),
    "team": ("Equipo", "category"),
    "gerencia": ("Gerencia solicitante", "category"),
    "requester_department": ("Departamento solicitante", "category"),
    "category": ("Categoría", "category"),
    "subcategory": ("Subcategoría", "category"),
    "provider": ("Proveedor", "category"),
    "assignee": ("Técnico asignado", "category"),
    "resolved_by": ("Resuelto por", "category"),
    "priority": ("Prioridad", "category"),
    "vip": ("VIP", "category"),
    "channel": ("Canal", "category"),
    "status": ("Estado", "category"),
    "day": ("Día", "date"),
    "week": ("Semana", "date"),
    "month": ("Mes", "date"),
    "year": ("Año", "date"),
}
DATE_DIMENSIONS = frozenset(key for key, (_label, kind) in DIMENSIONS.items() if kind == "date")

# clave → (etiqueta, unidad)
METRICS: dict[str, tuple[str, str]] = {
    "count": ("Cantidad de tickets", "count"),
    "resolution_hours_avg": ("Horas hábiles de resolución (promedio)", "hours"),
    "resolution_hours_sum": ("Horas hábiles de resolución (suma)", "hours"),
    "resolution_hours_median": ("Horas hábiles de resolución (mediana)", "hours"),
    "resolution_hours_max": ("Horas hábiles de resolución (máximo)", "hours"),
    "first_response_hours_avg": ("Horas a primera respuesta (promedio)", "hours"),
    "sla_target_hours": ("Objetivo SLA de resolución (horas)", "hours"),
    "sla_compliance_pct": ("Cumplimiento SLA (%)", "pct"),
    "sla_breached_count": ("SLA vencidos", "count"),
    "open_count": ("Abiertos", "count"),
}

DATE_FIELDS: dict[str, str] = {"created_at": "Fecha de solicitud", "resolved_at": "Fecha de solución"}

PERIODS: dict[str, str] = {
    "custom": "Personalizado",
    "this_month": "Este mes",
    "last_month": "Mes anterior",
    "last_30_days": "Últimos 30 días",
    "this_year": "Este año",
    "last_year": "Año anterior",
}

CHANNEL_LABELS = {"portal": "Portal", "email": "Correo", "phone": "Teléfono", "other": "Otro"}
NONE_LABELS = {
    "team": "Sin equipo",
    "gerencia": "Sin gerencia",
    "requester_department": "Sin departamento",
    "category": "Sin categoría",
    "subcategory": "Sin subcategoría",
    "provider": "Sin proveedor",
    "assignee": "Sin asignar",
    "resolved_by": "Sin resolver",
}
MONTH_ABBR = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")

NONE_KEY = "_none"
ALL_COLUMN_KEY = "_all"

MAX_RANGE_DAYS = 366
MAX_SECTIONS = 10
MAX_ROWS = 1000  # filas por sección (se marca ``truncated``)
MAX_COLUMNS = 100  # columnas por sección (se marca ``truncated``)
MAX_SECTION_TICKETS = 200_000
MAX_DETAIL_ROWS = 50_000
MAX_FILTER_VALUES = 200

assert set(DIMENSIONS) == set(DimensionKey.__args__)  # type: ignore[attr-defined]
assert set(METRICS) == set(MetricKey.__args__)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- definición


class ReportFilterSet(BaseModel):
    """Filtros (todos opcionales; lista vacía = sin filtrar). Solo reducen el alcance."""

    model_config = ConfigDict(extra="forbid")

    type_keys: list[str] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    department_ids: list[int] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    team_ids: list[int] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    category_ids: list[int] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    subcategory_ids: list[int] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    provider_ids: list[int] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    gerencia_ids: list[int] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    requester_department_ids: list[int] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    assignee_ids: list[int] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    priority_keys: list[str] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    channels: list[ChannelKey] = Field(default_factory=list, max_length=len(TICKET_CHANNELS))
    vip: Optional[bool] = None

    @field_validator("type_keys", "priority_keys")
    @classmethod
    def _clean_keys(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            item = item.strip()
            if not item or len(item) > 50:
                raise ValueError("Clave inválida")
            if item not in cleaned:
                cleaned.append(item)
        return cleaned

    @field_validator(
        "department_ids", "team_ids", "category_ids", "subcategory_ids", "provider_ids",
        "gerencia_ids", "requester_department_ids", "assignee_ids",
    )
    @classmethod
    def _unique_ids(cls, value: list[int]) -> list[int]:
        if any(item < 1 for item in value):
            raise ValueError("Los identificadores deben ser positivos")
        return list(dict.fromkeys(value))


class ReportSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=120)
    rows: list[DimensionKey] = Field(min_length=1, max_length=2)
    columns: list[DimensionKey] = Field(default_factory=list, max_length=1)
    metrics: list[MetricKey] = Field(min_length=1, max_length=8)
    filters: ReportFilterSet = Field(default_factory=ReportFilterSet)
    totals: bool = True

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _check_dimensions(self) -> "ReportSection":
        if len(set(self.rows)) != len(self.rows):
            raise ValueError("Las dimensiones de filas no se pueden repetir")
        if self.columns and self.columns[0] in self.rows:
            raise ValueError("La dimensión de columnas no puede repetirse en las filas")
        if len(set(self.metrics)) != len(self.metrics):
            raise ValueError("Las métricas no se pueden repetir")
        return self

    @property
    def column_dim(self) -> str | None:
        return self.columns[0] if self.columns else None

    def display_title(self) -> str:
        if self.title:
            return self.title
        labels = [DIMENSIONS[d][0].lower() for d in self.rows]
        text = "Por " + " y ".join(labels)
        if self.column_dim:
            text += f" según {DIMENSIONS[self.column_dim][0].lower()}"
        return text[:1].upper() + text[1:]


class ReportDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="Reporte", max_length=200)
    date_field: DateField = "created_at"
    period: Period = "last_30_days"
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    filters: ReportFilterSet = Field(default_factory=ReportFilterSet)
    sections: list[ReportSection] = Field(min_length=1, max_length=MAX_SECTIONS)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        return value.strip() or "Reporte"

    @model_validator(mode="after")
    def _check_custom_range(self) -> "ReportDefinition":
        if self.period == "custom":
            if self.date_from is None or self.date_to is None:
                raise ValueError("Indique la fecha inicial y final del periodo personalizado")
            check_range(self.date_from, self.date_to, as_http=False)
        return self


DETAIL_COLUMNS: dict[str, str] = {
    "number": "Número",
    "created_at": "Fecha de solicitud",
    "resolved_at": "Fecha de solución",
    "type": "Tipo",
    "department": "Departamento de atención",
    "category": "Categoría",
    "subcategory": "Subcategoría",
    "provider": "Proveedor",
    "channel": "Canal",
    "priority": "Prioridad",
    "vip": "VIP",
    "requester": "Solicitante",
    "requester_department": "Departamento del solicitante",
    "requester_gerencia": "Gerencia del solicitante",
    "assignee": "Técnico asignado",
    "resolved_by": "Resuelto por",
    "business_hours": "Horas hábiles de resolución",
    "sla_target_hours": "Objetivo SLA (horas)",
    "sla_met": "Cumplió SLA",
    "status": "Estado",
    "title": "Título",
    "resolution_note": "Solución",
    # Columnas opcionales (no van por defecto).
    "team": "Equipo",
    "closed_at": "Fecha de cierre",
    "first_response_hours": "Horas a primera respuesta",
}
OPTIONAL_DETAIL_COLUMNS = ("team", "closed_at", "first_response_hours")
DEFAULT_DETAIL_COLUMNS: tuple[str, ...] = tuple(
    key for key in DETAIL_COLUMNS if key not in OPTIONAL_DETAIL_COLUMNS
)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: ReportDefinition
    include_detail: bool = True
    detail_columns: Optional[list[str]] = Field(default=None, max_length=len(DETAIL_COLUMNS))

    @field_validator("detail_columns")
    @classmethod
    def _known_columns(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = [item for item in value if item not in DETAIL_COLUMNS]
        if unknown:
            raise ValueError(f"Columnas de detalle desconocidas: {', '.join(unknown[:5])}")
        return list(dict.fromkeys(value)) or None


def parse_definition(data: Any) -> ReportDefinition:
    """Valida una definición guardada (plantillas). 422 con el detalle si no es válida."""
    try:
        return ReportDefinition.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors(include_url=False, include_context=False, include_input=False),
        ) from exc


# --------------------------------------------------------------------------- periodo


def business_tz() -> ZoneInfo:
    return ZoneInfo(settings.tz_business)


def today_local(now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(business_tz()).date()


def check_range(date_from: date, date_to: date, *, as_http: bool = True) -> None:
    message = None
    if date_from > date_to:
        message = "La fecha inicial no puede ser posterior a la fecha final"
    elif (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
        message = f"El rango máximo de un reporte es de {MAX_RANGE_DAYS} días"
    if message is None:
        return
    if as_http:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message)
    raise ValueError(message)


def resolve_period(definition: ReportDefinition, now: datetime | None = None) -> tuple[date, date]:
    """Fechas (incluidas, hora de Guatemala) del periodo de la definición."""
    today = today_local(now)
    period = definition.period
    if period == "custom":
        assert definition.date_from is not None and definition.date_to is not None
        start, end = definition.date_from, definition.date_to
    elif period == "this_month":
        start, end = today.replace(day=1), today
    elif period == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    elif period == "last_30_days":
        start, end = today - timedelta(days=29), today
    elif period == "this_year":
        start, end = date(today.year, 1, 1), today
    else:  # last_year
        start, end = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    check_range(start, end)
    return start, end


def utc_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """Instantes UTC [inicio, fin) de un rango de fechas locales (ambas incluidas)."""
    tz = business_tz()
    start = datetime.combine(date_from, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return start, end


# --------------------------------------------------------------------------- alcance


def _join_names(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " y " + names[-1]


def scope_info(session: Session, principal: Principal) -> dict[str, str]:
    """Nivel y descripción del alcance de los reportes de ``principal``."""
    if principal.is_admin:
        return {"level": "all", "label": "Todos los tickets"}
    parts: list[str] = []
    if principal.is_agent and principal.department_ids:
        names = sorted(
            session.scalars(
                select(Department.name).where(Department.id.in_(sorted(principal.department_ids)))
            ).all(),
            key=str.casefold,
        )
        if names:
            parts.append(f"tickets de {_join_names(names)}")
    if principal.viewer_gerencia_ids:
        names = sorted(
            session.scalars(
                select(Gerencia.name).where(Gerencia.id.in_(sorted(principal.viewer_gerencia_ids)))
            ).all(),
            key=str.casefold,
        )
        if names:
            parts.append(f"tickets solicitados por {_join_names(names)}")
    if parts:
        label = _join_names(parts) + ", además de los tuyos"
        return {"level": "areas", "label": label[:1].upper() + label[1:]}
    if principal.is_approver:
        return {"level": "own", "label": "Tus tickets y los que te corresponde aprobar"}
    return {"level": "own", "label": "Tus tickets (solicitados o asignados a ti)"}


def meta(session: Session, principal: Principal) -> dict[str, Any]:
    return {
        "dimensions": [
            {"key": key, "label": label, "kind": kind} for key, (label, kind) in DIMENSIONS.items()
        ],
        "metrics": [
            {"key": key, "label": label, "unit": unit} for key, (label, unit) in METRICS.items()
        ],
        "date_fields": [{"key": key, "label": label} for key, label in DATE_FIELDS.items()],
        "periods": [{"key": key, "label": label} for key, label in PERIODS.items()],
        "detail_columns": [
            {"key": key, "label": label, "default": key in DEFAULT_DETAIL_COLUMNS}
            for key, label in DETAIL_COLUMNS.items()
        ],
        "limits": {
            "max_range_days": MAX_RANGE_DAYS,
            "max_sections": MAX_SECTIONS,
            "max_row_dimensions": 2,
            "max_column_dimensions": 1,
            "max_metrics": 8,
            "max_rows": MAX_ROWS,
            "max_columns": MAX_COLUMNS,
            "max_detail_rows": MAX_DETAIL_ROWS,
        },
        "scope": scope_info(session, principal),
        # Programar envíos de plantillas: administradores y líderes de equipo.
        "can_schedule": principal.is_admin or bool(principal.lead_team_ids),
    }


# --------------------------------------------------------------------------- filtros SQL


def filter_conditions(filters: ReportFilterSet) -> list[ColumnElement[bool]]:
    """Condiciones WHERE (AND) de un conjunto de filtros. Nunca amplían el alcance."""
    conditions: list[ColumnElement[bool]] = []
    if filters.type_keys:
        conditions.append(
            Ticket.type_id.in_(select(TicketType.id).where(TicketType.key.in_(filters.type_keys)))
        )
    if filters.priority_keys:
        conditions.append(
            Ticket.priority_id.in_(select(Priority.id).where(Priority.key.in_(filters.priority_keys)))
        )
    simple = (
        (filters.department_ids, Ticket.department_id),
        (filters.team_ids, Ticket.team_id),
        (filters.category_ids, Ticket.category_id),
        (filters.subcategory_ids, Ticket.subcategory_id),
        (filters.provider_ids, Ticket.provider_id),
        (filters.requester_department_ids, Ticket.requester_department_id),
        (filters.assignee_ids, Ticket.assignee_id),
    )
    for values, column in simple:
        if values:
            conditions.append(column.in_(values))
    if filters.gerencia_ids:
        conditions.append(
            Ticket.requester_department_id.in_(
                select(Department.id).where(Department.gerencia_id.in_(filters.gerencia_ids))
            )
        )
    if filters.channels:
        conditions.append(Ticket.channel.in_(list(filters.channels)))
    if filters.vip is not None:
        conditions.append(Ticket.vip == filters.vip)
    return conditions


@dataclass(frozen=True)
class ReportContext:
    """Definición ya resuelta: periodo, instantes UTC y condiciones base (alcance + filtros)."""

    principal: Principal
    definition: ReportDefinition
    date_from: date
    date_to: date
    start: datetime
    end: datetime

    @property
    def date_column(self) -> Any:
        return Ticket.created_at if self.definition.date_field == "created_at" else Ticket.resolved_at

    def base_conditions(self, section: ReportSection | None = None) -> list[ColumnElement[bool]]:
        column = self.date_column
        conditions: list[ColumnElement[bool]] = [
            visible_tickets_filter(self.principal),
            column >= self.start,
            column < self.end,
            *filter_conditions(self.definition.filters),
        ]
        if section is not None:
            conditions.extend(filter_conditions(section.filters))
        return conditions


def build_context(
    principal: Principal, definition: ReportDefinition, now: datetime | None = None
) -> ReportContext:
    date_from, date_to = resolve_period(definition, now)
    start, end = utc_bounds(date_from, date_to)
    return ReportContext(principal, definition, date_from, date_to, start, end)


def _resolution_instance_subquery() -> Any:
    """Instancia SLA de resolución vigente por ticket (la última no cancelada)."""
    return (
        select(SlaInstance.ticket_id.label("ticket_id"), func.max(SlaInstance.id).label("instance_id"))
        .where(SlaInstance.metric == "resolution", SlaInstance.status != "cancelled")
        .group_by(SlaInstance.ticket_id)
        .subquery("current_resolution")
    )


# --------------------------------------------------------------------------- agregación


class _Accumulator:
    __slots__ = ("count", "resolution", "fr_sum", "fr_n", "target_sum", "target_n", "met", "breached", "open")

    def __init__(self) -> None:
        self.count = 0
        self.resolution: list[float] = []
        self.fr_sum = 0.0
        self.fr_n = 0
        self.target_sum = 0.0
        self.target_n = 0
        self.met = 0
        self.breached = 0
        self.open = 0

    def add(self, values: "_TicketValues") -> None:
        self.count += 1
        if values.resolution_hours is not None:
            self.resolution.append(values.resolution_hours)
        if values.first_response_seconds is not None:
            self.fr_sum += values.first_response_seconds
            self.fr_n += 1
        if values.target_minutes is not None:
            self.target_sum += values.target_minutes
            self.target_n += 1
        if values.sla_status == "met":
            self.met += 1
        elif values.sla_status == "breached":
            self.breached += 1
        if values.is_open:
            self.open += 1

    def result(self, metrics: Iterable[str]) -> dict[str, Any]:
        return {metric: self.value(metric) for metric in metrics}

    def value(self, metric: str) -> Any:
        if self.count == 0:
            return None
        res = self.resolution
        if metric == "count":
            return self.count
        if metric == "resolution_hours_avg":
            return round(sum(res) / len(res), 2) if res else None
        if metric == "resolution_hours_sum":
            return round(sum(res), 2) if res else None
        if metric == "resolution_hours_median":
            return round(statistics.median(res), 2) if res else None
        if metric == "resolution_hours_max":
            return round(max(res), 2) if res else None
        if metric == "first_response_hours_avg":
            return round(self.fr_sum / self.fr_n / 3600.0, 2) if self.fr_n else None
        if metric == "sla_target_hours":
            return round(self.target_sum / self.target_n / 60.0, 2) if self.target_n else None
        if metric == "sla_compliance_pct":
            evaluated = self.met + self.breached
            return round(self.met * 100.0 / evaluated, 1) if evaluated else None
        if metric == "sla_breached_count":
            return self.breached
        if metric == "open_count":
            return self.open
        raise KeyError(metric)


def _empty_values(metrics: Iterable[str]) -> dict[str, Any]:
    return {metric: None for metric in metrics}


@dataclass
class _TicketValues:
    resolution_hours: float | None
    first_response_seconds: float | None
    target_minutes: int | None
    sla_status: str | None
    is_open: bool


def _date_key(dim: str, day: str) -> str:
    """Clave del bucket de fecha a partir del día local ``YYYY-MM-DD``."""
    if dim == "day":
        return day
    if dim == "month":
        return day[:7]
    if dim == "year":
        return day[:4]
    value = date.fromisoformat(day)
    return (value - timedelta(days=value.weekday())).isoformat()


def _date_label(dim: str, key: str) -> str:
    if dim == "day":
        value = date.fromisoformat(key)
        return value.strftime("%d/%m/%Y")
    if dim == "week":
        value = date.fromisoformat(key)
        return f"Semana del {value.strftime('%d/%m/%Y')}"
    if dim == "month":
        year, month = key.split("-")
        return f"{MONTH_ABBR[int(month) - 1]} {year}"
    return key


def _date_buckets(dim: str, date_from: date, date_to: date) -> list[str]:
    keys: list[str] = []
    current = date_from
    while current <= date_to:
        key = _date_key(dim, current.isoformat())
        if not keys or keys[-1] != key:
            keys.append(key)
        current += timedelta(days=1)
    return keys


def _raw_key(dim: str, value: Any) -> str:
    if value is None:
        return NONE_KEY
    if dim in DATE_DIMENSIONS:
        return _date_key(dim, str(value)[:10])
    if dim == "vip":
        return "true" if value else "false"
    return str(value)


class _Labeler:
    """Resuelve etiqueta y orden de las claves de cada dimensión (consultas por lote)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _named(self, model: Any, label_attr: str, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        column = getattr(model, label_attr)
        return dict(self.session.execute(select(model.id, column).where(model.id.in_(ids))).all())

    def resolve(self, dim: str, keys: Iterable[str]) -> dict[str, tuple[str, Any]]:
        """``{clave: (etiqueta, orden)}``; la clave nula va siempre al final."""
        keys = set(keys)
        result: dict[str, tuple[str, Any]] = {}
        if NONE_KEY in keys:
            result[NONE_KEY] = (NONE_LABELS.get(dim, "Sin dato"), (1, ""))
            keys.discard(NONE_KEY)
        if dim in DATE_DIMENSIONS:
            for key in keys:
                result[key] = (_date_label(dim, key), (0, key))
            return result
        if dim == "vip":
            for key in keys:
                result[key] = ("Sí" if key == "true" else "No", (0, 0 if key == "true" else 1))
            return result
        if dim == "channel":
            order = list(TICKET_CHANNELS)
            for key in keys:
                result[key] = (
                    CHANNEL_LABELS.get(key, key),
                    (0, order.index(key) if key in order else len(order)),
                )
            return result
        ids = sorted(int(key) for key in keys if key.lstrip("-").isdigit())
        if dim == "priority":
            rows = self.session.execute(
                select(Priority.id, Priority.name, Priority.rank).where(Priority.id.in_(ids))
            ).all() if ids else []
            info = {row[0]: (row[1], (0, -int(row[2]))) for row in rows}
        elif dim == "status":
            rows = self.session.execute(
                select(WorkflowState.id, WorkflowState.name, WorkflowState.order).where(
                    WorkflowState.id.in_(ids)
                )
            ).all() if ids else []
            info = {row[0]: (row[1], (0, int(row[2] or 0), row[0])) for row in rows}
        elif dim == "type":
            names = self._named(TicketType, "name", ids)
            info = {tid: (name, (0, tid)) for tid, name in names.items()}
        else:
            model, attr = {
                "department": (Department, "name"),
                "requester_department": (Department, "name"),
                "team": (Team, "name"),
                "gerencia": (Gerencia, "name"),
                "category": (TicketCategory, "name"),
                "subcategory": (TicketSubcategory, "name"),
                "provider": (Provider, "name"),
                "assignee": (User, "display_name"),
                "resolved_by": (User, "display_name"),
            }[dim]
            names = self._named(model, attr, ids)
            info = {item_id: (name, (0, (name or "").casefold(), item_id)) for item_id, name in names.items()}
        for key in keys:
            item_id = int(key) if key.lstrip("-").isdigit() else None
            label, order = info.get(item_id, (f"#{key}", (2, key)))  # type: ignore[arg-type]
            result[key] = (label, order)
        return result


@dataclass
class _SectionData:
    row_keys: dict[tuple[str, ...], dict[str, _Accumulator]] = field(default_factory=dict)
    row_totals: dict[tuple[str, ...], _Accumulator] = field(default_factory=dict)
    column_totals: dict[str, _Accumulator] = field(default_factory=dict)
    grand: _Accumulator = field(default_factory=_Accumulator)


def _section_rows(session: Session, ctx: ReportContext, section: ReportSection) -> Iterator[tuple[Any, ...]]:
    """Una fila por ticket: (claves de dimensiones..., insumos de métricas)."""
    dims = [*section.rows, *section.columns]
    needs_gerencia = "gerencia" in dims
    requester_dept = aliased(Department, name="requester_dept")
    date_expr = None
    if any(dim in DATE_DIMENSIONS for dim in dims):
        date_expr = local_date(ctx.date_column, settings.tz_business, reference=ctx.end)

    dim_columns: list[Any] = []
    for dim in dims:
        if dim in DATE_DIMENSIONS:
            dim_columns.append(date_expr)
        elif dim == "gerencia":
            dim_columns.append(requester_dept.gerencia_id)
        else:
            dim_columns.append(
                {
                    "type": Ticket.type_id,
                    "department": Ticket.department_id,
                    "team": Ticket.team_id,
                    "requester_department": Ticket.requester_department_id,
                    "category": Ticket.category_id,
                    "subcategory": Ticket.subcategory_id,
                    "provider": Ticket.provider_id,
                    "assignee": Ticket.assignee_id,
                    "resolved_by": Ticket.resolved_by_id,
                    "priority": Ticket.priority_id,
                    "vip": Ticket.vip,
                    "channel": Ticket.channel,
                    "status": Ticket.status_id,
                }[dim]
            )

    current = _resolution_instance_subquery()
    instance = aliased(SlaInstance, name="resolution_instance")
    stmt = (
        select(
            *dim_columns,
            Ticket.business_resolution_minutes,
            Ticket.created_at,
            Ticket.first_response_at,
            instance.target_minutes,
            instance.status,
            WorkflowState.category,
        )
        .select_from(Ticket)
        .join(WorkflowState, WorkflowState.id == Ticket.status_id)
        .outerjoin(current, current.c.ticket_id == Ticket.id)
        .outerjoin(instance, instance.id == current.c.instance_id)
    )
    if needs_gerencia:
        stmt = stmt.outerjoin(requester_dept, requester_dept.id == Ticket.requester_department_id)
    stmt = stmt.where(*ctx.base_conditions(section)).limit(MAX_SECTION_TICKETS + 1)
    yield from session.execute(stmt.execution_options(yield_per=2000))


def _aggregate(session: Session, ctx: ReportContext, section: ReportSection) -> tuple[_SectionData, int]:
    data = _SectionData()
    row_dims = section.rows
    column_dim = section.column_dim
    n_dims = len(row_dims) + (1 if column_dim else 0)
    total = 0
    for row in _section_rows(session, ctx, section):
        total += 1
        if total > MAX_SECTION_TICKETS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"La sección «{section.display_title()}» abarca más de {MAX_SECTION_TICKETS:,} "
                    "tickets; reduzca el periodo o agregue filtros"
                ).replace(",", " "),
            )
        keys = row[:n_dims]
        minutes, created_at, first_response_at, target, sla_status, state_category = row[n_dims:]
        values = _TicketValues(
            resolution_hours=(float(minutes) / 60.0) if minutes is not None else None,
            first_response_seconds=(
                max(0.0, (first_response_at - created_at).total_seconds())
                if first_response_at is not None and created_at is not None
                else None
            ),
            target_minutes=int(target) if target is not None else None,
            sla_status=sla_status,
            is_open=state_category != "done",
        )
        row_key = tuple(_raw_key(dim, keys[i]) for i, dim in enumerate(row_dims))
        column_key = _raw_key(column_dim, keys[-1]) if column_dim else ALL_COLUMN_KEY
        cells = data.row_keys.setdefault(row_key, {})
        cells.setdefault(column_key, _Accumulator()).add(values)
        data.row_totals.setdefault(row_key, _Accumulator()).add(values)
        data.column_totals.setdefault(column_key, _Accumulator()).add(values)
        data.grand.add(values)
    return data, total


def run_section(session: Session, ctx: ReportContext, section: ReportSection) -> dict[str, Any]:
    data, ticket_count = _aggregate(session, ctx, section)
    labeler = _Labeler(session)
    metrics = list(section.metrics)
    row_dims = list(section.rows)
    column_dim = section.column_dim
    truncated = False

    # Columnas: completas para fechas (series continuas), si no, las que tienen datos.
    if column_dim:
        column_keys = set(data.column_totals)
        if column_dim in DATE_DIMENSIONS:
            column_keys.update(_date_buckets(column_dim, ctx.date_from, ctx.date_to)[: MAX_COLUMNS + 1])
        column_info = labeler.resolve(column_dim, column_keys)
        ordered_columns = sorted(column_keys, key=lambda key: column_info[key][1])
        if len(ordered_columns) > MAX_COLUMNS:
            ordered_columns = ordered_columns[:MAX_COLUMNS]
            truncated = True
        columns = [{"key": key, "label": column_info[key][0]} for key in ordered_columns]
    else:
        ordered_columns = [ALL_COLUMN_KEY]
        columns = [{"key": ALL_COLUMN_KEY, "label": "Total"}]

    # Filas: una dimensión de fecha sola se completa con los periodos sin datos.
    row_keys = set(data.row_keys)
    if len(row_dims) == 1 and row_dims[0] in DATE_DIMENSIONS:
        row_keys.update((key,) for key in _date_buckets(row_dims[0], ctx.date_from, ctx.date_to)[: MAX_ROWS + 1])
    infos = [labeler.resolve(dim, {key[i] for key in row_keys}) for i, dim in enumerate(row_dims)]
    ordered_rows = sorted(row_keys, key=lambda key: tuple(infos[i][part][1] for i, part in enumerate(key)))
    if len(ordered_rows) > MAX_ROWS:
        ordered_rows = ordered_rows[:MAX_ROWS]
        truncated = True

    rows_out = []
    for key in ordered_rows:
        cells = data.row_keys.get(key, {})
        total_acc = data.row_totals.get(key)
        rows_out.append(
            {
                "keys": [{"key": part, "label": infos[i][part][0]} for i, part in enumerate(key)],
                "cells": {
                    column: (cells[column].result(metrics) if column in cells else _empty_values(metrics))
                    for column in ordered_columns
                },
                "total": total_acc.result(metrics) if total_acc is not None else _empty_values(metrics),
            }
        )
    totals = {
        column: (
            data.column_totals[column].result(metrics)
            if column in data.column_totals
            else _empty_values(metrics)
        )
        for column in ordered_columns
    }
    grand_total = data.grand.result(metrics) if data.grand.count else _empty_values(metrics)
    if data.grand.count == 0 and "count" in grand_total:
        grand_total["count"] = 0
    return {
        "title": section.display_title(),
        "row_dims": row_dims,
        "column_dim": column_dim,
        "metrics": metrics,
        "columns": columns,
        "rows": rows_out,
        "totals": totals,
        "grand_total": grand_total,
        "show_totals": section.totals,
        "ticket_count": ticket_count,
        "truncated": truncated,
    }


def run_report(
    session: Session, principal: Principal, definition: ReportDefinition, now: datetime | None = None
) -> dict[str, Any]:
    """Ejecuta la definición con el alcance de ``principal`` → ``ReportResult``."""
    ctx = build_context(principal, definition, now)
    return {
        "title": definition.title,
        "generated_at": (now or utcnow()).isoformat(),
        "date_from": ctx.date_from.isoformat(),
        "date_to": ctx.date_to.isoformat(),
        "date_field": definition.date_field,
        "period": definition.period,
        "timezone": settings.tz_business,
        "scope": scope_info(session, principal),
        "sections": [run_section(session, ctx, section) for section in definition.sections],
    }


# --------------------------------------------------------------------------- detalle


def _catalog_names(session: Session, model: Any, attr: str = "name") -> dict[int, str]:
    return dict(session.execute(select(model.id, getattr(model, attr))).all())


def count_detail_rows(session: Session, ctx: ReportContext) -> int:
    """Tickets que entrarían en la hoja «Detalle» sin el límite ``MAX_DETAIL_ROWS``."""
    return int(
        session.scalar(select(func.count(Ticket.id)).select_from(Ticket).where(*ctx.base_conditions()))
        or 0
    )


def iter_detail_rows(
    session: Session,
    ctx: ReportContext,
    columns: Iterable[str] = DEFAULT_DETAIL_COLUMNS,
    *,
    limit: int = MAX_DETAIL_ROWS,
    batch_size: int = 1000,
) -> Iterator[dict[str, Any]]:
    """Una fila por ticket (filtros globales de la definición, sin los de sección).

    Los valores son crudos (``datetime`` UTC, números, textos); el exportador los formatea.
    """
    columns = list(columns)
    types = _catalog_names(session, TicketType)
    departments = dict(session.execute(select(Department.id, Department.name)).all())
    department_gerencia = dict(session.execute(select(Department.id, Department.gerencia_id)).all())
    gerencias = _catalog_names(session, Gerencia)
    teams = _catalog_names(session, Team)
    categories = _catalog_names(session, TicketCategory)
    subcategories = _catalog_names(session, TicketSubcategory)
    providers = _catalog_names(session, Provider)
    priorities = _catalog_names(session, Priority)
    states = _catalog_names(session, WorkflowState)

    requester = aliased(User, name="requester_user")
    assignee = aliased(User, name="assignee_user")
    resolver = aliased(User, name="resolver_user")
    current = _resolution_instance_subquery()
    instance = aliased(SlaInstance, name="resolution_instance")
    stmt = (
        select(
            Ticket.id,
            Ticket.public_key,
            Ticket.title,
            Ticket.created_at,
            Ticket.resolved_at,
            Ticket.closed_at,
            Ticket.first_response_at,
            Ticket.type_id,
            Ticket.department_id,
            Ticket.team_id,
            Ticket.category_id,
            Ticket.subcategory_id,
            Ticket.provider_id,
            Ticket.channel,
            Ticket.priority_id,
            Ticket.vip,
            Ticket.requester_department_id,
            Ticket.status_id,
            Ticket.business_resolution_minutes,
            Ticket.resolution_note,
            requester.display_name,
            assignee.display_name,
            resolver.display_name,
            instance.target_minutes,
            instance.status,
        )
        .select_from(Ticket)
        .join(requester, requester.id == Ticket.requester_id)
        .outerjoin(assignee, assignee.id == Ticket.assignee_id)
        .outerjoin(resolver, resolver.id == Ticket.resolved_by_id)
        .outerjoin(current, current.c.ticket_id == Ticket.id)
        .outerjoin(instance, instance.id == current.c.instance_id)
        .where(*ctx.base_conditions())
        .order_by(ctx.date_column, Ticket.id)
        .limit(limit)
        .execution_options(yield_per=batch_size)
    )
    for row in session.execute(stmt):
        (
            ticket_id, public_key, title, created_at, resolved_at, closed_at, first_response_at,
            type_id, department_id, team_id, category_id, subcategory_id, provider_id, channel,
            priority_id, vip, requester_department_id, status_id, minutes, resolution_note,
            requester_name, assignee_name, resolver_name, target, sla_status,
        ) = row
        gerencia_id = department_gerencia.get(requester_department_id)
        values = {
            "number": public_key or f"ESD-{ticket_id}",
            "created_at": created_at,
            "resolved_at": resolved_at,
            "closed_at": closed_at,
            "type": types.get(type_id),
            "department": departments.get(department_id),
            "team": teams.get(team_id),
            "category": categories.get(category_id),
            "subcategory": subcategories.get(subcategory_id),
            "provider": providers.get(provider_id),
            "channel": CHANNEL_LABELS.get(channel, channel),
            "priority": priorities.get(priority_id),
            "vip": "Sí" if vip else "No",
            "requester": requester_name,
            "requester_department": departments.get(requester_department_id),
            "requester_gerencia": gerencias.get(gerencia_id) if gerencia_id else None,
            "assignee": assignee_name,
            "resolved_by": resolver_name,
            "business_hours": round(minutes / 60.0, 2) if minutes is not None else None,
            "sla_target_hours": round(target / 60.0, 2) if target is not None else None,
            "sla_met": {"met": "Sí", "breached": "No"}.get(sla_status or ""),
            "status": states.get(status_id),
            "title": title,
            "resolution_note": resolution_note,
            "first_response_hours": (
                round(max(0.0, (first_response_at - created_at).total_seconds()) / 3600.0, 2)
                if first_response_at is not None and created_at is not None
                else None
            ),
        }
        yield {key: values[key] for key in columns}
