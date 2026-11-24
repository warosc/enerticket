"""Parámetros comunes de los reportes: rango de fechas (zona de negocio) y alcance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement

from app.core.config import settings
from app.core.permissions import visible_tickets_filter
from app.core.security import Principal
from app.models.core import Ticket

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 731


@dataclass(frozen=True)
class ReportFilters:
    """Filtros ya validados. ``start``/``end`` son instantes UTC (``end`` exclusivo)."""

    principal: Principal
    date_from: date
    date_to: date
    tz_name: str
    department_id: int | None = None
    team_id: int | None = None

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @property
    def start(self) -> datetime:
        return datetime.combine(self.date_from, time.min, tzinfo=self.tz).astimezone(timezone.utc)

    @property
    def end(self) -> datetime:
        next_day = self.date_to + timedelta(days=1)
        return datetime.combine(next_day, time.min, tzinfo=self.tz).astimezone(timezone.utc)

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1

    def previous_period(self) -> "ReportFilters":
        """Periodo inmediatamente anterior con la misma duración (para comparativas)."""
        span = timedelta(days=self.days)
        return ReportFilters(
            principal=self.principal,
            date_from=self.date_from - span,
            date_to=self.date_to - span,
            tz_name=self.tz_name,
            department_id=self.department_id,
            team_id=self.team_id,
        )

    def scope_conditions(self) -> list[ColumnElement[bool]]:
        """Visibilidad del usuario + filtros de departamento/equipo (sin fechas)."""
        conditions: list[ColumnElement[bool]] = [visible_tickets_filter(self.principal)]
        if self.department_id is not None:
            conditions.append(Ticket.department_id == self.department_id)
        if self.team_id is not None:
            conditions.append(Ticket.team_id == self.team_id)
        return conditions

    def as_dict(self) -> dict:
        return {
            "from": self.date_from.isoformat(),
            "to": self.date_to.isoformat(),
            "timezone": self.tz_name,
            "department_id": self.department_id,
            "team_id": self.team_id,
        }


def today_local(tz_name: str | None = None) -> date:
    return datetime.now(ZoneInfo(tz_name or settings.tz_business)).date()


def build_filters(
    principal: Principal,
    date_from: date | None,
    date_to: date | None,
    department_id: int | None = None,
    team_id: int | None = None,
) -> ReportFilters:
    """Valida y completa los filtros (por defecto, los últimos 30 días)."""
    tz_name = settings.tz_business
    end = date_to or today_local(tz_name)
    start = date_from or (end - timedelta(days=DEFAULT_RANGE_DAYS - 1))
    if start > end:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="La fecha inicial no puede ser posterior a la fecha final",
        )
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"El rango máximo de un reporte es de {MAX_RANGE_DAYS} días",
        )
    return ReportFilters(
        principal=principal,
        date_from=start,
        date_to=end,
        tz_name=tz_name,
        department_id=department_id,
        team_id=team_id,
    )
