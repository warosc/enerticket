"""Cálculo de tiempo hábil: ventanas, noches, fines de semana, feriados, cruce de año y zona horaria."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.sla.calendar import (
    CalendarError,
    CalendarSpec,
    add_business_minutes,
    add_business_seconds,
    business_seconds_between,
    is_24x7_windows,
    normalize_windows,
    weekly_minutes,
)
from app.services.sla.holidays_gt import easter_sunday, fallback_guatemala_holidays, official_holidays

GT = ZoneInfo("America/Guatemala")
BUSINESS = [{"weekday": d, "start": "08:00", "end": "17:00"} for d in range(5)]
FULL = [{"weekday": d, "start": "00:00", "end": "24:00"} for d in range(7)]


def gt(*args: int) -> datetime:
    return datetime(*args, tzinfo=GT)


@pytest.fixture
def business() -> CalendarSpec:
    # 2026-12-25 (viernes) feriado fijo; 1 de enero recurrente (cualquier año).
    return CalendarSpec.build(
        "America/Guatemala", BUSINESS, holidays=[date(2026, 12, 25)], recurring=[date(2000, 1, 1)]
    )


@pytest.fixture
def full() -> CalendarSpec:
    return CalendarSpec.build("America/Guatemala", FULL)


def test_add_within_same_day(business):
    # 2026-11-23 es lunes.
    assert add_business_minutes(business, gt(2026, 11, 23, 9, 0), 120) == gt(2026, 11, 23, 11, 0)


def test_add_spills_to_next_day(business):
    assert add_business_minutes(business, gt(2026, 11, 23, 16, 0), 120) == gt(2026, 11, 24, 9, 0)


def test_add_exactly_to_end_of_window(business):
    assert add_business_minutes(business, gt(2026, 11, 23, 8, 0), 9 * 60) == gt(2026, 11, 23, 17, 0)


def test_add_over_weekend(business):
    # Viernes 27/11 16:30 + 60 min → lunes 30/11 08:30.
    assert add_business_minutes(business, gt(2026, 11, 27, 16, 30), 60) == gt(2026, 11, 30, 8, 30)


def test_start_at_night_and_before_opening(business):
    assert add_business_minutes(business, gt(2026, 11, 23, 20, 0), 30) == gt(2026, 11, 24, 8, 30)
    assert add_business_minutes(business, gt(2026, 11, 24, 6, 0), 30) == gt(2026, 11, 24, 8, 30)
    # Sábado por la mañana → lunes.
    assert add_business_minutes(business, gt(2026, 11, 28, 10, 0), 30) == gt(2026, 11, 30, 8, 30)


def test_holiday_is_skipped(business):
    # Jueves 24/12 16:00 + 2 h: 1 h el jueves, viernes 25 feriado, fin de semana → lunes 28 09:00.
    assert add_business_minutes(business, gt(2026, 12, 24, 16, 0), 120) == gt(2026, 12, 28, 9, 0)


def test_year_crossing_with_recurring_holiday(business):
    # Jueves 31/12/2026 16:00 + 2 h: viernes 1/1/2027 feriado recurrente → lunes 4/1/2027 09:00.
    assert add_business_minutes(business, gt(2026, 12, 31, 16, 0), 120) == gt(2027, 1, 4, 9, 0)
    assert business.is_holiday(date(2031, 1, 1))


def test_multi_day_target(business):
    # 40 h hábiles desde el lunes 08:00 = 4 días de 9 h (36 h) + 4 h → viernes 12:00.
    assert add_business_minutes(business, gt(2026, 11, 23, 8, 0), 40 * 60) == gt(2026, 11, 27, 12, 0)


def test_business_seconds_between(business):
    assert business_seconds_between(business, gt(2026, 11, 27, 16, 0), gt(2026, 11, 30, 9, 0)) == 2 * 3600
    assert business_seconds_between(business, gt(2026, 11, 28, 0, 0), gt(2026, 11, 29, 23, 59)) == 0
    assert business_seconds_between(business, gt(2026, 11, 23, 18, 0), gt(2026, 11, 23, 10, 0)) == 0
    assert business_seconds_between(business, gt(2026, 12, 24, 8, 0), gt(2026, 12, 26, 0, 0)) == 9 * 3600


def test_add_and_between_are_inverse(business):
    rng = random.Random(20261123)
    base = gt(2026, 11, 1, 0, 0)
    for _ in range(200):
        start = base + timedelta(minutes=rng.randint(0, 60 * 24 * 90))
        seconds = rng.randint(1, 60 * 60 * 120)
        target = add_business_seconds(business, start, seconds)
        assert target >= start
        assert business_seconds_between(business, start, target) == seconds


def test_24x7_is_wall_clock(full):
    start = gt(2026, 11, 28, 23, 50)  # sábado por la noche
    assert add_business_minutes(full, start, 15) == start + timedelta(minutes=15)
    assert business_seconds_between(full, start, start + timedelta(days=3)) == 3 * 86400
    assert full.is_24x7


def test_timezone_conversion_is_fixed_utc_minus_6(business):
    # 14:00 UTC = 08:00 en Guatemala (UTC-6 todo el año, sin horario de verano).
    start = datetime(2026, 11, 23, 14, 0, tzinfo=timezone.utc)
    target = add_business_minutes(business, start, 60)
    assert target == datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)
    # 13:00 UTC = 07:00 local → empieza a contar a las 08:00 local.
    assert add_business_minutes(business, datetime(2026, 11, 23, 13, 0, tzinfo=timezone.utc), 30) == datetime(
        2026, 11, 23, 14, 30, tzinfo=timezone.utc
    )
    # Mismo instante en junio: Guatemala no aplica horario de verano.
    june = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)
    assert add_business_minutes(business, june, 60) == datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc)


def test_naive_datetimes_are_utc(business):
    naive = datetime(2026, 11, 23, 14, 0)
    assert add_business_minutes(business, naive, 60) == datetime(2026, 11, 23, 15, 0, tzinfo=timezone.utc)


def test_arithmetic_uses_real_elapsed_time_across_dst():
    # Zona con horario de verano: el 8/3/2026 en Nueva York dura 23 h reales.
    spec = CalendarSpec.build("America/New_York", FULL)
    ny = ZoneInfo("America/New_York")
    start = datetime(2026, 3, 8, 0, 0, tzinfo=ny)
    end = datetime(2026, 3, 9, 0, 0, tzinfo=ny)
    assert business_seconds_between(spec, start, end) == 23 * 3600


def test_split_windows_skip_lunch():
    spec = CalendarSpec.build(
        "America/Guatemala",
        [{"weekday": 0, "start": "08:00", "end": "12:00"}, {"weekday": 0, "start": "13:00", "end": "17:00"}],
    )
    assert add_business_minutes(spec, gt(2026, 11, 23, 11, 30), 60) == gt(2026, 11, 23, 13, 30)
    assert spec.weekly_minutes == 8 * 60


def test_zero_seconds_returns_start(business):
    start = gt(2026, 11, 28, 10, 0)
    assert add_business_seconds(business, start, 0) == start


def test_calendar_without_windows_raises():
    spec = CalendarSpec.build("America/Guatemala", [])
    with pytest.raises(CalendarError):
        add_business_minutes(spec, gt(2026, 11, 23, 8, 0), 10)


@pytest.mark.parametrize(
    "windows",
    [
        [{"weekday": 0, "start": "09:00", "end": "08:00"}],
        [{"weekday": 0, "start": "08:00", "end": "12:00"}, {"weekday": 0, "start": "11:00", "end": "13:00"}],
        [{"weekday": 7, "start": "08:00", "end": "12:00"}],
        [{"weekday": 0, "start": "24:00", "end": "24:00"}],
        [{"weekday": 0, "start": "8am", "end": "12:00"}],
    ],
)
def test_invalid_windows(windows):
    with pytest.raises(CalendarError):
        normalize_windows(windows)


def test_invalid_timezone():
    with pytest.raises(CalendarError):
        CalendarSpec.build("Marte/Olympus", BUSINESS)


def test_window_helpers():
    assert weekly_minutes(BUSINESS) == 45 * 60
    assert is_24x7_windows(FULL)
    assert not is_24x7_windows(BUSINESS)


def test_easter_computus():
    assert easter_sunday(2025) == date(2025, 4, 20)
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)


def test_guatemala_holidays_include_holy_week():
    days = dict(official_holidays(2026))
    for expected in (date(2026, 1, 1), date(2026, 4, 2), date(2026, 4, 3), date(2026, 9, 15), date(2026, 12, 25)):
        assert expected in days
    fallback = dict(fallback_guatemala_holidays(2027))
    assert date(2027, 3, 25) in fallback and fallback[date(2027, 3, 26)] == "Viernes Santo"
    assert date(2027, 10, 20) in fallback
