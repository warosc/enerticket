"""Expresiones SQL portables (PostgreSQL en producción, SQLite en pruebas)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Float, String
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import ColumnElement, FunctionElement

_TZ_NAME_RE = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")


def safe_tz_name(name: str) -> str:
    """Valida un nombre IANA (se inserta como literal en el SQL de PostgreSQL)."""
    if not _TZ_NAME_RE.match(name):
        raise ValueError(f"Zona horaria inválida: {name!r}")
    ZoneInfo(name)  # lanza si no existe
    return name


class seconds_between(FunctionElement):  # noqa: N801 - estilo de funciones SQL
    """Segundos transcurridos entre dos columnas de fecha/hora (``end - start``)."""

    type = Float()
    inherit_cache = True
    name = "seconds_between"


@compiles(seconds_between)
def _seconds_between_default(element: seconds_between, compiler: Any, **kw: Any) -> str:
    start, end = list(element.clauses)
    return (
        f"EXTRACT(EPOCH FROM ({compiler.process(end, **kw)} - {compiler.process(start, **kw)}))"
    )


@compiles(seconds_between, "sqlite")
def _seconds_between_sqlite(element: seconds_between, compiler: Any, **kw: Any) -> str:
    start, end = list(element.clauses)
    return (
        f"((julianday({compiler.process(end, **kw)}) - "
        f"julianday({compiler.process(start, **kw)})) * 86400.0)"
    )


class local_date(FunctionElement):  # noqa: N801
    """Fecha local ``YYYY-MM-DD`` de una columna UTC en la zona de negocio.

    PostgreSQL usa la zona IANA (con horario de verano correcto). SQLite no tiene zonas:
    se aplica el desfase fijo de la zona en la fecha de referencia (America/Guatemala no
    tiene horario de verano, por lo que el resultado es exacto en ese caso).
    Ambos parámetros se insertan como literales validados para que la expresión sea
    idéntica en SELECT y GROUP BY.
    """

    type = String()
    inherit_cache = False
    name = "local_date"

    def __init__(self, column: ColumnElement[Any], tz_name: str, reference: datetime | None = None):
        self.tz_name = safe_tz_name(tz_name)
        ref = reference or datetime.now(timezone.utc)
        offset = ref.astimezone(ZoneInfo(self.tz_name)).utcoffset()
        self.offset_minutes = int(offset.total_seconds() // 60) if offset is not None else 0
        super().__init__(column)


@compiles(local_date)
def _local_date_default(element: local_date, compiler: Any, **kw: Any) -> str:
    (column,) = list(element.clauses)
    return f"to_char(timezone('{element.tz_name}', {compiler.process(column, **kw)}), 'YYYY-MM-DD')"


@compiles(local_date, "sqlite")
def _local_date_sqlite(element: local_date, compiler: Any, **kw: Any) -> str:
    (column,) = list(element.clauses)
    minutes = int(element.offset_minutes)
    return f"strftime('%Y-%m-%d', {compiler.process(column, **kw)}, '{minutes:+d} minutes')"
