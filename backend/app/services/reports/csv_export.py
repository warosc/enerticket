"""Exportación CSV segura contra inyección de fórmulas (CSV/Formula injection, OWASP).

Cualquier texto que empiece por ``=``, ``+``, ``-``, ``@``, tabulador o retorno de carro
(también tras espacios iniciales, o con sus variantes de ancho completo) se prefija con
un apóstrofo para que Excel/LibreOffice lo traten como texto. Los números y fechas que
genera el sistema se escriben tal cual. Se emite BOM UTF-8 para que Excel detecte la
codificación (tildes y eñes).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator, Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")
UTF8_BOM = "﻿"


def sanitize_cell(value: Any, tz: ZoneInfo | None = None) -> Any:
    """Convierte un valor a celda CSV segura."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, datetime):
        moment = value.astimezone(tz) if tz is not None and value.tzinfo is not None else value
        return moment.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    if text.lstrip(" ").startswith(FORMULA_PREFIXES) or text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def iter_csv(
    header: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    tz: ZoneInfo | None = None,
    bom: bool = True,
) -> Iterator[str]:
    """Genera el CSV línea a línea (para ``StreamingResponse``)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")

    def flush() -> str:
        data = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return data

    writer.writerow([sanitize_cell(h) for h in header])
    yield (UTF8_BOM if bom else "") + flush()
    for row in rows:
        writer.writerow([sanitize_cell(cell, tz) for cell in row])
        yield flush()
