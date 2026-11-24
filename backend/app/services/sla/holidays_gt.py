"""Feriados oficiales (por defecto Guatemala) para poblar calendarios hábiles.

Se usa la librería ``holidays`` (dependencia declarada en la fundación), que calcula los
feriados móviles (Semana Santa: Jueves, Viernes y Sábado Santo) y los traslados de ley
(p.ej. Día del Ejército, Decreto 19-2018). Si la librería no está disponible o no conoce
el país, se usa una lista fija documentada para Guatemala más Semana Santa calculada con
el algoritmo de Pascua gregoriano (Meeus/Jones/Butcher).

Lista fija de respaldo (Código de Trabajo de Guatemala, art. 127):
1 de enero, Jueves/Viernes/Sábado Santo, 1 de mayo, 30 de junio, 15 de septiembre,
20 de octubre, 1 de noviembre y 25 de diciembre. El 15 de agosto (Asunción) solo es
feriado en el departamento de Guatemala y se incluye para la sede central.

Nota: el 24 y el 31 de diciembre son medios días de asueto (desde las 12:00). El modelo de
feriados es de día completo; si se requiere, un administrador puede ajustar las ventanas
de servicio o registrar esos días manualmente.
"""

from __future__ import annotations

import logging
import warnings
from datetime import date, timedelta

logger = logging.getLogger(__name__)

DEFAULT_COUNTRY = "GT"

_FIXED_GT: tuple[tuple[int, int, str], ...] = (
    (1, 1, "Año Nuevo"),
    (5, 1, "Día del Trabajo"),
    (6, 30, "Día del Ejército"),
    (8, 15, "Día de la Asunción"),
    (9, 15, "Día de la Independencia"),
    (10, 20, "Día de la Revolución"),
    (11, 1, "Día de Todos los Santos"),
    (12, 25, "Navidad"),
)

# Nombres normalizados (la librería devuelve algunos sin tilde según la versión).
_NAME_FIXES = {
    "Sabado Santo": "Sábado Santo",
    "Día de Navidad": "Navidad",
}


def easter_sunday(year: int) -> date:
    """Domingo de Pascua (calendario gregoriano, algoritmo de Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - nombre del algoritmo
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def fallback_guatemala_holidays(year: int) -> list[tuple[date, str]]:
    """Lista fija de Guatemala + Semana Santa (sin traslados de ley)."""
    easter = easter_sunday(year)
    result = [(date(year, month, day), name) for month, day, name in _FIXED_GT]
    result += [
        (easter - timedelta(days=3), "Jueves Santo"),
        (easter - timedelta(days=2), "Viernes Santo"),
        (easter - timedelta(days=1), "Sábado Santo"),
    ]
    return sorted(result)


def official_holidays(year: int, country: str = DEFAULT_COUNTRY) -> list[tuple[date, str]]:
    """Feriados oficiales ``[(fecha, nombre)]`` del país para el año, ordenados por fecha."""
    country = (country or DEFAULT_COUNTRY).upper()
    try:
        with warnings.catch_warnings():
            # holidays 0.x avisa en cada import sobre su futura política de versiones.
            warnings.simplefilter("ignore")
            import holidays as holidays_lib

        try:
            calendar = holidays_lib.country_holidays(country, years=year, language="es")
        except NotImplementedError:
            calendar = holidays_lib.country_holidays(country, years=year)
        items = sorted(calendar.items())
        result = [(day, _NAME_FIXES.get(str(name), str(name))) for day, name in items]
        if result:
            return result
    except Exception as exc:  # librería ausente, país no soportado...
        if country != DEFAULT_COUNTRY:
            raise ValueError(f"No se pueden calcular los feriados del país '{country}'") from exc
        logger.warning("No se pudo usar la librería 'holidays' (%s); se usa la lista fija de Guatemala", exc)
    if country == DEFAULT_COUNTRY:
        return fallback_guatemala_holidays(year)
    raise ValueError(f"No se pueden calcular los feriados del país '{country}'")
