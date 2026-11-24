"""Operaciones de catálogo SLA compartidas por el router de administración y los seeds."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.sla import BusinessCalendar, CalendarHoliday, SlaInstance, SlaPolicy
from app.services.sla.calendar import is_24x7_windows, weekly_minutes
from app.services.sla.holidays_gt import DEFAULT_COUNTRY, official_holidays


def import_official_holidays(
    session: Session,
    calendar: BusinessCalendar,
    years: Iterable[int],
    *,
    country: str = DEFAULT_COUNTRY,
    only_empty_years: bool = False,
) -> dict[str, int]:
    """Agrega los feriados oficiales de ``years`` al calendario (sin duplicar fechas).

    ``only_empty_years=True`` (seeds) omite los años que ya tienen algún feriado de fecha
    fija, para respetar los feriados que un administrador haya eliminado a propósito.
    """
    created = skipped = 0
    existing = set(
        session.scalars(select(CalendarHoliday.date).where(CalendarHoliday.calendar_id == calendar.id)).all()
    )
    years_with_data = {
        d.year
        for d in session.scalars(
            select(CalendarHoliday.date).where(
                CalendarHoliday.calendar_id == calendar.id, CalendarHoliday.recurring.is_(False)
            )
        ).all()
    }
    for year in sorted(set(years)):
        if only_empty_years and year in years_with_data:
            continue
        for day, name in official_holidays(year, country):
            if day in existing:
                skipped += 1
                continue
            session.add(CalendarHoliday(calendar_id=calendar.id, date=day, name=name[:150], recurring=False))
            existing.add(day)
            created += 1
    session.flush()
    return {"created": created, "skipped": skipped}


def calendar_out(session: Session, calendar: BusinessCalendar) -> dict[str, Any]:
    windows = calendar.windows or []
    holiday_count = session.scalar(
        select(func.count()).select_from(CalendarHoliday).where(CalendarHoliday.calendar_id == calendar.id)
    )
    policy_count = session.scalar(
        select(func.count()).select_from(SlaPolicy).where(SlaPolicy.calendar_id == calendar.id)
    )
    return {
        "id": calendar.id,
        "name": calendar.name,
        "description": calendar.description or "",
        "timezone": calendar.timezone,
        "windows": windows,
        "is_24x7": is_24x7_windows(windows) if windows else False,
        "weekly_business_minutes": weekly_minutes(windows) if windows else 0,
        "holiday_count": int(holiday_count or 0),
        "policy_count": int(policy_count or 0),
        "created_at": calendar.created_at,
        "updated_at": calendar.updated_at,
    }


def holiday_out(holiday: CalendarHoliday) -> dict[str, Any]:
    return {
        "id": holiday.id,
        "calendar_id": holiday.calendar_id,
        "date": holiday.date,
        "name": holiday.name,
        "recurring": holiday.recurring,
    }


def policy_out(session: Session, policy: SlaPolicy) -> dict[str, Any]:
    calendar = session.get(BusinessCalendar, policy.calendar_id)
    instance_count = session.scalar(
        select(func.count()).select_from(SlaInstance).where(SlaInstance.policy_id == policy.id)
    )
    return {
        "id": policy.id,
        "name": policy.name,
        "description": policy.description or "",
        "calendar_id": policy.calendar_id,
        "calendar_name": calendar.name if calendar else None,
        "priority_keys": list(policy.priority_keys or []),
        "type_keys": list(policy.type_keys or []),
        "department_ids": list(policy.department_ids or []),
        "category_ids": list(policy.category_ids or []),
        "subcategory_ids": list(policy.subcategory_ids or []),
        "vip_only": bool(policy.vip_only),
        "precedence": policy.precedence,
        "first_response_minutes": policy.first_response_minutes,
        "resolution_minutes": policy.resolution_minutes,
        "warning_threshold_pct": policy.warning_threshold_pct,
        "pause_categories": list(policy.pause_categories or []),
        "pause_states": list(policy.pause_states or []),
        "active": policy.active,
        "instance_count": int(instance_count or 0),
        "created_at": policy.created_at,
        "updated_at": policy.updated_at,
    }


def years_around(today: date, back: int = 1, ahead: int = 3) -> list[int]:
    return list(range(today.year - back, today.year + ahead + 1))
