"""Cálculo de tiempo hábil sobre un calendario de servicio.

Un calendario define, en su zona horaria (por defecto ``America/Guatemala``), ventanas de
servicio por día de la semana (0 = lunes ... 6 = domingo) y feriados de día completo
(fecha fija o recurrente cada año). Ejemplos:

- 24x7: una ventana ``00:00-24:00`` cada día.
- Horario hábil: lunes a viernes ``08:00-17:00`` (se admiten varias ventanas por día,
  p.ej. ``08:00-12:00`` y ``13:00-17:00`` para excluir el almuerzo).

Toda la aritmética se hace en UTC: los límites de cada ventana se construyen en hora
local y se convierten a UTC antes de restar, de modo que el cálculo sería correcto
incluso en zonas con horario de verano (Guatemala no lo aplica, UTC-6 fijo).

Funciones principales (puras, sin base de datos):

- ``business_seconds_between(spec, start, end)``: segundos hábiles en ``[start, end)``.
- ``add_business_seconds(spec, start, seconds)``: instante en que se acumulan ``seconds``
  segundos hábiles a partir de ``start``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MINUTES_PER_DAY = 24 * 60
# Límite de búsqueda al sumar tiempo hábil (evita bucles infinitos con calendarios vacíos).
MAX_SEARCH_DAYS = 3660
WEEKDAY_NAMES = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")

_TIME_RE = re.compile(r"^([01]\d|2[0-4]):([0-5]\d)$")


class CalendarError(ValueError):
    """Calendario inválido o sin horario hábil suficiente para el cálculo."""


# --------------------------------------------------------------------------- parsing


def parse_hhmm(value: str, *, allow_24: bool = False) -> int:
    """``"08:30"`` → 510 minutos. ``"24:00"`` solo si ``allow_24``."""
    match = _TIME_RE.match((value or "").strip())
    if not match:
        raise CalendarError(f"Hora inválida '{value}': use el formato HH:MM")
    hours, minutes = int(match.group(1)), int(match.group(2))
    total = hours * 60 + minutes
    if hours == 24 and (minutes != 0 or not allow_24):
        raise CalendarError(f"Hora inválida '{value}': solo se admite 24:00 como hora de fin")
    return total


def format_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo((name or "").strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CalendarError(f"Zona horaria desconocida: '{name}'") from exc


def normalize_windows(raw: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Valida y normaliza ventanas ``{"weekday", "start", "end"}``.

    - ``weekday`` 0..6 (0 = lunes), ``start`` < ``end``, ``end`` puede ser ``24:00``.
    - Las ventanas de un mismo día no pueden solaparse.
    Devuelve la lista ordenada por día y hora de inicio.
    """
    parsed: list[tuple[int, int, int]] = []
    for item in raw:
        try:
            weekday = int(item["weekday"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CalendarError("Cada ventana necesita 'weekday' (0 = lunes ... 6 = domingo)") from exc
        if not 0 <= weekday <= 6:
            raise CalendarError(f"Día de la semana inválido: {weekday} (use 0 = lunes ... 6 = domingo)")
        start = parse_hhmm(str(item.get("start", "")))
        end = parse_hhmm(str(item.get("end", "")), allow_24=True)
        if end <= start:
            raise CalendarError(
                f"{WEEKDAY_NAMES[weekday]}: la hora de fin ({format_hhmm(end)}) debe ser "
                f"posterior a la de inicio ({format_hhmm(start)})"
            )
        parsed.append((weekday, start, end))
    parsed.sort()
    for previous, current in zip(parsed, parsed[1:]):
        if previous[0] == current[0] and current[1] < previous[2]:
            raise CalendarError(f"{WEEKDAY_NAMES[current[0]]}: las ventanas de servicio se solapan")
    return [
        {"weekday": weekday, "start": format_hhmm(start), "end": format_hhmm(end)}
        for weekday, start, end in parsed
    ]


# --------------------------------------------------------------------------- especificación


@dataclass(frozen=True)
class CalendarSpec:
    """Representación inmutable y precalculada de un calendario para los cálculos."""

    tz: ZoneInfo
    # weekday -> ventanas (inicio, fin) en minutos desde la medianoche local, ordenadas.
    windows: Mapping[int, tuple[tuple[int, int], ...]]
    holidays: frozenset[date] = field(default_factory=frozenset)
    # (mes, día) de feriados recurrentes.
    recurring: frozenset[tuple[int, int]] = field(default_factory=frozenset)

    @classmethod
    def build(
        cls,
        timezone_name: str,
        windows: Iterable[Mapping[str, Any]],
        holidays: Iterable[date] = (),
        recurring: Iterable[date | tuple[int, int]] = (),
    ) -> "CalendarSpec":
        by_day: dict[int, list[tuple[int, int]]] = {}
        for item in normalize_windows(windows):
            by_day.setdefault(item["weekday"], []).append(
                (parse_hhmm(item["start"]), parse_hhmm(item["end"], allow_24=True))
            )
        recurring_md = frozenset(
            (value.month, value.day) if isinstance(value, date) else (int(value[0]), int(value[1]))
            for value in recurring
        )
        return cls(
            tz=get_zone(timezone_name),
            windows={day: tuple(items) for day, items in by_day.items()},
            holidays=frozenset(holidays),
            recurring=recurring_md,
        )

    @property
    def weekly_minutes(self) -> int:
        return sum(end - start for items in self.windows.values() for start, end in items)

    @cached_property
    def is_24x7(self) -> bool:
        """Servicio continuo sin feriados: el tiempo hábil equivale al tiempo real."""
        return (
            not self.holidays
            and not self.recurring
            and all(self.windows.get(day) == ((0, MINUTES_PER_DAY),) for day in range(7))
        )

    def is_holiday(self, day: date) -> bool:
        return day in self.holidays or (day.month, day.day) in self.recurring

    def local_date(self, moment: datetime) -> date:
        return _aware(moment).astimezone(self.tz).date()

    def intervals_for(self, day: date) -> list[tuple[datetime, datetime]]:
        """Intervalos hábiles (UTC) del día local ``day``; vacío si es feriado o no laborable."""
        if self.is_holiday(day):
            return []
        result: list[tuple[datetime, datetime]] = []
        for start, end in self.windows.get(day.weekday(), ()):
            result.append((self._at(day, start), self._at(day, end)))
        return result

    def _at(self, day: date, minutes: int) -> datetime:
        if minutes >= MINUTES_PER_DAY:
            day = day + timedelta(days=minutes // MINUTES_PER_DAY)
            minutes = minutes % MINUTES_PER_DAY
        local = datetime.combine(day, time(minutes // 60, minutes % 60), tzinfo=self.tz)
        return local.astimezone(timezone.utc)


def _aware(moment: datetime) -> datetime:
    """Fechas sin zona se interpretan como UTC (igual que ``TZDateTime``)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _days(start_day: date, end_day: date) -> Iterator[date]:
    day = start_day
    while day <= end_day:
        yield day
        day += timedelta(days=1)


# --------------------------------------------------------------------------- cálculo


def business_seconds_between(spec: CalendarSpec, start: datetime, end: datetime) -> int:
    """Segundos hábiles transcurridos entre ``start`` y ``end`` (0 si ``end <= start``)."""
    start = _aware(start).astimezone(timezone.utc)
    end = _aware(end).astimezone(timezone.utc)
    if end <= start:
        return 0
    if spec.is_24x7:
        return int((end - start).total_seconds())
    total = timedelta(0)
    # Un día antes por seguridad: las ventanas no cruzan la medianoche local, pero así
    # cualquier desfase de zona queda cubierto.
    first_day = spec.local_date(start) - timedelta(days=1)
    last_day = spec.local_date(end)
    for day in _days(first_day, last_day):
        for window_start, window_end in spec.intervals_for(day):
            overlap_start = max(window_start, start)
            overlap_end = min(window_end, end)
            if overlap_end > overlap_start:
                total += overlap_end - overlap_start
    return int(total.total_seconds())


def add_business_seconds(spec: CalendarSpec, start: datetime, seconds: int | float) -> datetime:
    """Instante en el que se completan ``seconds`` segundos hábiles contados desde ``start``.

    Si ``start`` cae fuera de horario, el conteo empieza en la siguiente ventana hábil.
    Con ``seconds <= 0`` devuelve ``start``. Lanza ``CalendarError`` si el calendario no
    tiene suficiente horario hábil en ``MAX_SEARCH_DAYS`` días.
    """
    start = _aware(start).astimezone(timezone.utc)
    remaining = timedelta(seconds=seconds)
    if remaining <= timedelta(0):
        return start
    if not spec.windows:
        raise CalendarError("El calendario no tiene ventanas de servicio")
    if spec.is_24x7:
        return start + remaining
    day = spec.local_date(start) - timedelta(days=1)
    for _ in range(MAX_SEARCH_DAYS):
        for window_start, window_end in spec.intervals_for(day):
            if window_end <= start:
                continue
            segment_start = max(window_start, start)
            available = window_end - segment_start
            if remaining <= available:
                return segment_start + remaining
            remaining -= available
        day += timedelta(days=1)
    raise CalendarError("El calendario no tiene horario hábil suficiente para calcular el objetivo")


def add_business_minutes(spec: CalendarSpec, start: datetime, minutes: int | float) -> datetime:
    return add_business_seconds(spec, start, minutes * 60)


def weekly_minutes(windows: Sequence[Mapping[str, Any]]) -> int:
    """Minutos hábiles por semana de una lista de ventanas ya normalizada."""
    return sum(
        parse_hhmm(w["end"], allow_24=True) - parse_hhmm(w["start"]) for w in normalize_windows(windows)
    )


def is_24x7_windows(windows: Sequence[Mapping[str, Any]]) -> bool:
    normalized = normalize_windows(windows)
    days = {w["weekday"] for w in normalized if w["start"] == "00:00" and w["end"] == "24:00"}
    return days == set(range(7))
