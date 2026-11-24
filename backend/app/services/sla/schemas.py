"""Esquemas Pydantic de la API SLA."""

from __future__ import annotations

import datetime as dt
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.sla.calendar import CalendarError, get_zone, normalize_windows

PauseCategory = Literal["todo", "in_progress", "waiting"]
MAX_TARGET_MINUTES = 60 * 24 * 365  # un año de minutos hábiles


class WindowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6, description="0 = lunes ... 6 = domingo")
    start: str = Field(pattern=r"^\d{2}:\d{2}$", examples=["08:00"])
    end: str = Field(pattern=r"^\d{2}:\d{2}$", examples=["17:00"])


def _check_windows(value: list[WindowIn] | None) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    try:
        return normalize_windows([w.model_dump() for w in value])
    except CalendarError as exc:
        raise ValueError(str(exc)) from exc


def _check_timezone(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        get_zone(value)
    except CalendarError as exc:
        raise ValueError(str(exc)) from exc
    return value.strip()


class CalendarIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=150)
    description: str = Field(default="", max_length=2000)
    timezone: str = Field(default="America/Guatemala", max_length=64)
    windows: list[WindowIn] = Field(min_length=1, max_length=70)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        return _check_timezone(value) or value

    @field_validator("windows")
    @classmethod
    def _valid_windows(cls, value):
        _check_windows(value)
        return value

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("El nombre debe tener al menos 2 caracteres")
        return value

    def normalized_windows(self) -> list[dict[str, Any]]:
        return _check_windows(self.windows) or []


class CalendarPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    description: Optional[str] = Field(default=None, max_length=2000)
    timezone: Optional[str] = Field(default=None, max_length=64)
    windows: Optional[list[WindowIn]] = Field(default=None, min_length=1, max_length=70)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: Optional[str]) -> Optional[str]:
        return _check_timezone(value)

    @field_validator("windows")
    @classmethod
    def _valid_windows(cls, value):
        _check_windows(value)
        return value

    def normalized_windows(self) -> list[dict[str, Any]] | None:
        return _check_windows(self.windows)


class CalendarOut(BaseModel):
    id: int
    name: str
    description: str
    timezone: str
    windows: list[dict[str, Any]]
    is_24x7: bool
    weekly_business_minutes: int
    holiday_count: int
    policy_count: int
    created_at: datetime
    updated_at: datetime


class HolidayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date
    name: str = Field(min_length=2, max_length=150)
    recurring: bool = False


class HolidayPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: Optional[dt.date] = None
    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    recurring: Optional[bool] = None


class HolidayOut(BaseModel):
    id: int
    calendar_id: int
    date: dt.date
    name: str
    recurring: bool


class HolidayImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    years: list[int] = Field(min_length=1, max_length=10)
    country: str = Field(default="GT", min_length=2, max_length=3)

    @field_validator("years")
    @classmethod
    def _valid_years(cls, value: list[int]) -> list[int]:
        for year in value:
            if not 2000 <= year <= 2100:
                raise ValueError("Los años deben estar entre 2000 y 2100")
        return sorted(set(value))


class HolidayImportOut(BaseModel):
    created: int
    skipped: int


class PolicyBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = Field(default=None, max_length=2000)
    calendar_id: Optional[int] = None
    priority_keys: Optional[list[str]] = Field(default=None, max_length=20)
    type_keys: Optional[list[str]] = Field(default=None, max_length=20)
    department_ids: Optional[list[int]] = Field(default=None, max_length=200)
    category_ids: Optional[list[int]] = Field(default=None, max_length=500)
    subcategory_ids: Optional[list[int]] = Field(default=None, max_length=1000)
    vip_only: Optional[bool] = None
    precedence: Optional[int] = Field(default=None, ge=0, le=100000)
    first_response_minutes: Optional[int] = Field(default=None, ge=1, le=MAX_TARGET_MINUTES)
    resolution_minutes: Optional[int] = Field(default=None, ge=1, le=MAX_TARGET_MINUTES)
    warning_threshold_pct: Optional[int] = Field(default=None, ge=1, le=99)
    pause_categories: Optional[list[PauseCategory]] = None
    pause_states: Optional[list[str]] = Field(default=None, max_length=50)
    active: Optional[bool] = None


class PolicyIn(PolicyBase):
    name: str = Field(min_length=2, max_length=150)
    description: str = Field(default="", max_length=2000)
    calendar_id: int
    precedence: int = Field(default=100, ge=0, le=100000)
    warning_threshold_pct: int = Field(default=75, ge=1, le=99)
    pause_categories: list[PauseCategory] = Field(default_factory=lambda: ["waiting"])
    vip_only: bool = False
    active: bool = True


class PolicyPatch(PolicyBase):
    name: Optional[str] = Field(default=None, min_length=2, max_length=150)


class PolicyOut(BaseModel):
    id: int
    name: str
    description: str
    calendar_id: int
    calendar_name: Optional[str]
    priority_keys: list[str]
    type_keys: list[str]
    department_ids: list[int]
    category_ids: list[int]
    subcategory_ids: list[int]
    vip_only: bool
    precedence: int
    first_response_minutes: Optional[int]
    resolution_minutes: Optional[int]
    warning_threshold_pct: int
    pause_categories: list[str]
    pause_states: list[str]
    active: bool
    instance_count: int
    created_at: datetime
    updated_at: datetime


class PreviewOut(BaseModel):
    calendar_id: int
    start: datetime
    minutes: int
    target_at: datetime
    business_seconds: int


class RecalculateOut(BaseModel):
    tickets: int
    created: int
    cancelled: int
    breached: int
