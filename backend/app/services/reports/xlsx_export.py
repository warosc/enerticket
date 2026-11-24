"""Exportación del generador de reportes a Excel (.xlsx) con openpyxl.

- Una hoja por sección (encabezado con título, periodo, alcance y fecha de generación;
  tabla dinámica con filas × columnas × métricas y fila de totales) y, opcionalmente, la
  hoja «Detalle» con una fila por ticket (máx. ``MAX_DETAIL_ROWS``).
- Modo ``write_only``: el detalle se escribe en streaming sin cargar las celdas en memoria.
- Todo texto pasa por ``sanitize_cell`` (protección contra inyección de fórmulas) y se le
  quitan los caracteres de control que el formato XML no admite. Ninguna celda es fórmula.
- Horas con 2 decimales; porcentajes como fracción con formato ``0.0%``; fechas en hora
  de Guatemala (``TZ_BUSINESS``) como fecha real de Excel.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.services.reports.builder import (
    DATE_FIELDS,
    DETAIL_COLUMNS,
    DIMENSIONS,
    MAX_DETAIL_ROWS,
    METRICS,
    business_tz,
)
from app.services.reports.csv_export import sanitize_cell

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DETAIL_SHEET = "Detalle"
MAX_CELL_TEXT = 32_000
_SHEET_INVALID = re.compile(r"[\[\]:*?/\\]")

TITLE_FONT = Font(bold=True, size=14, color="1F3864")
SUBTITLE_FONT = Font(bold=True, size=12)
BOLD = Font(bold=True)
MUTED = Font(italic=True, color="595959")
WARNING_FONT = Font(bold=True, color="9C0006")
WARNING_FILL = PatternFill("solid", fgColor="FFC7CE")
HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
TOTAL_FILL = PatternFill("solid", fgColor="F2F2F2")
THIN = Side(style="thin", color="A6A6A6")
HEADER_BORDER = Border(bottom=THIN)
TOTAL_BORDER = Border(top=THIN)
WRAP_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
CENTER_CONTINUOUS = Alignment(horizontal="centerContinuous", vertical="center")

NUMBER_FORMATS = {"count": "0", "hours": "0.00", "pct": "0.0%"}
DATETIME_FORMAT = "dd/mm/yyyy hh:mm"

DETAIL_WIDTHS = {
    "number": 12, "created_at": 17, "resolved_at": 17, "closed_at": 17, "type": 16, "department": 24,
    "team": 22, "category": 24, "subcategory": 24, "provider": 20, "channel": 11, "priority": 11,
    "vip": 6, "requester": 24, "requester_department": 24, "requester_gerencia": 24, "assignee": 22,
    "resolved_by": 22, "business_hours": 12, "sla_target_hours": 12, "sla_met": 10, "status": 14,
    "title": 40, "resolution_note": 50, "first_response_hours": 12,
}
DETAIL_NUMERIC = {"business_hours", "sla_target_hours", "first_response_hours"}
DETAIL_DATES = {"created_at", "resolved_at", "closed_at"}


# --------------------------------------------------------------------------- celdas seguras


def safe_text(value: Any) -> str:
    """Texto seguro para una celda: sin caracteres de control inválidos ni fórmulas."""
    text = ILLEGAL_CHARACTERS_RE.sub("", str(value))
    if len(text) > MAX_CELL_TEXT:
        text = text[:MAX_CELL_TEXT] + "…"
    cleaned = sanitize_cell(text)
    return cleaned if isinstance(cleaned, str) else str(cleaned)


def _local_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(business_tz()).replace(tzinfo=None)


def _cell(ws: Any, value: Any, *, font: Font | None = None, number_format: str | None = None,
          fill: PatternFill | None = None, border: Border | None = None,
          alignment: Alignment | None = None) -> WriteOnlyCell:
    if isinstance(value, bool):
        value = "Sí" if value else "No"
    elif isinstance(value, datetime):
        value = _local_naive(value)
        number_format = number_format or DATETIME_FORMAT
    elif isinstance(value, date):
        number_format = number_format or "dd/mm/yyyy"
    elif isinstance(value, str):
        value = safe_text(value)
    elif value is not None and not isinstance(value, (int, float)):
        value = safe_text(value)
    cell = WriteOnlyCell(ws, value=value)
    if font is not None:
        cell.font = font
    if number_format is not None:
        cell.number_format = number_format
    if fill is not None:
        cell.fill = fill
    if border is not None:
        cell.border = border
    if alignment is not None:
        cell.alignment = alignment
    return cell


def _metric_value(metric: str, value: Any) -> Any:
    if value is None:
        return None
    if METRICS[metric][1] == "pct":
        return round(float(value) / 100.0, 4)
    return value


def unique_sheet_title(title: str, used: set[str]) -> str:
    base = _SHEET_INVALID.sub(" ", ILLEGAL_CHARACTERS_RE.sub("", title or "")).strip().strip("'").strip()
    base = re.sub(r"\s+", " ", base)[:31].strip() or "Sección"
    candidate = base
    counter = 2
    while candidate.casefold() in used:
        suffix = f" ({counter})"
        candidate = base[: 31 - len(suffix)].rstrip() + suffix
        counter += 1
    used.add(candidate.casefold())
    return candidate


def _fmt_date(value: str) -> str:
    return date.fromisoformat(value).strftime("%d/%m/%Y")


# --------------------------------------------------------------------------- hojas


def _write_header(
    ws: Any, result: dict[str, Any], subtitle: str | None = None, note: str | None = None
) -> None:
    ws.append([_cell(ws, result["title"], font=TITLE_FONT)])
    field_label = DATE_FIELDS.get(result.get("date_field", ""), "")
    period = f"{_fmt_date(result['date_from'])} – {_fmt_date(result['date_to'])}"
    if field_label:
        period += f" (según {field_label.lower()})"
    generated = result.get("generated_at")
    generated_text = ""
    if generated:
        generated_dt = datetime.fromisoformat(generated)
        generated_text = _local_naive(generated_dt).strftime("%d/%m/%Y %H:%M")
    ws.append([_cell(ws, "Periodo:", font=BOLD), _cell(ws, period)])
    ws.append([_cell(ws, "Alcance:", font=BOLD), _cell(ws, result["scope"]["label"])])
    ws.append([_cell(ws, "Generado:", font=BOLD), _cell(ws, generated_text)])
    if note:
        # Misma fila que la línea en blanco: no cambia la posición de la tabla ni la inmovilización.
        ws.append([_cell(ws, "Aviso:", font=WARNING_FONT), _cell(ws, note, font=WARNING_FONT)])
    else:
        ws.append([])
    if subtitle:
        ws.append([_cell(ws, subtitle, font=SUBTITLE_FONT)])


def _write_section(
    ws: Any, result: dict[str, Any], section: dict[str, Any], *, detail_note: str | None = None
) -> None:
    row_dims: list[str] = section["row_dims"]
    metrics: list[str] = section["metrics"]
    columns: list[dict[str, str]] = section["columns"]
    has_columns = section.get("column_dim") is not None
    show_totals = section.get("show_totals", True)
    subtitle = section["title"]
    if section.get("truncated"):
        subtitle += " (resultado recortado: demasiadas filas o columnas)"

    n_dims = len(row_dims)
    groups = list(columns) if has_columns else [{"key": "_all", "label": ""}]
    if has_columns and show_totals:
        groups.append({"key": "__total__", "label": "Total"})
    metric_count = len(metrics)

    # Anchos de columna (antes de escribir filas: requisito del modo write_only).
    for i, dim in enumerate(row_dims):
        longest = max(
            [len(DIMENSIONS[dim][0])] + [len(str(row["keys"][i]["label"])) for row in section["rows"]]
        )
        ws.column_dimensions[get_column_letter(i + 1)].width = min(max(longest + 2, 12), 50)
    for g in range(len(groups)):
        for m in range(metric_count):
            col = n_dims + g * metric_count + m + 1
            ws.column_dimensions[get_column_letter(col)].width = 16

    # Filas 1-4 encabezado, 5 vacía, 6 subtítulo, 7 (y 8 con columnas) títulos de la tabla.
    # En modo write_only la inmovilización se fija antes de escribir la primera fila.
    first_data_row = 9 if has_columns else 8
    ws.freeze_panes = f"{get_column_letter(n_dims + 1)}{first_data_row}"
    _write_header(ws, result, subtitle, detail_note)

    if has_columns:
        top = [_cell(ws, None, fill=HEADER_FILL) for _ in range(n_dims)]
        for group in groups:
            for m in range(metric_count):
                top.append(
                    _cell(
                        ws, group["label"] if m == 0 else None, font=BOLD, fill=HEADER_FILL,
                        alignment=CENTER_CONTINUOUS,
                    )
                )
        ws.append(top)
    header = [
        _cell(ws, DIMENSIONS[dim][0], font=BOLD, fill=HEADER_FILL, border=HEADER_BORDER, alignment=WRAP_CENTER)
        for dim in row_dims
    ]
    for _group in groups:
        for metric in metrics:
            header.append(
                _cell(ws, METRICS[metric][0], font=BOLD, fill=HEADER_FILL, border=HEADER_BORDER,
                      alignment=WRAP_CENTER)
            )
    ws.append(header)

    def metric_cells(values: dict[str, Any], **style: Any) -> list[WriteOnlyCell]:
        return [
            _cell(ws, _metric_value(metric, values.get(metric)),
                  number_format=NUMBER_FORMATS[METRICS[metric][1]], **style)
            for metric in metrics
        ]

    for row in section["rows"]:
        line = [_cell(ws, part["label"]) for part in row["keys"]]
        for group in groups:
            values = row["total"] if group["key"] == "__total__" else row["cells"].get(group["key"], {})
            line.extend(metric_cells(values, font=BOLD if group["key"] == "__total__" else None))
        ws.append(line)

    if show_totals:
        line = [_cell(ws, "Total", font=BOLD, fill=TOTAL_FILL, border=TOTAL_BORDER)]
        line.extend(_cell(ws, None, fill=TOTAL_FILL, border=TOTAL_BORDER) for _ in range(n_dims - 1))
        for group in groups:
            if group["key"] == "__total__":
                values = section["grand_total"]
            elif has_columns:
                values = section["totals"].get(group["key"], {})
            else:
                values = section["grand_total"]
            line.extend(metric_cells(values, font=BOLD, fill=TOTAL_FILL, border=TOTAL_BORDER))
        ws.append(line)

    if not section["rows"]:
        ws.append([])
        ws.append([_cell(ws, "Sin datos para el periodo y los filtros seleccionados.", font=MUTED)])


def detail_truncated_note(total: int, limit: int | None = None) -> str:
    limit = MAX_DETAIL_ROWS if limit is None else limit
    return (
        f"Detalle recortado: se incluyen los primeros {limit:,} de {total:,} tickets. "
        "Acote el periodo o los filtros para obtener el detalle completo."
    ).replace(",", " ")


def _write_detail(
    ws: Any, columns: list[str], rows: Iterable[dict[str, Any]], *, total: int | None = None
) -> int:
    """Escribe la hoja «Detalle». Si ``total`` supera ``MAX_DETAIL_ROWS`` se avisa en la fila de
    títulos (a la derecha de las columnas, visible al estar inmovilizada) que está recortada."""
    truncated = total is not None and total > MAX_DETAIL_ROWS
    for i, key in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(i)].width = DETAIL_WIDTHS.get(key, 16)
    if truncated:
        ws.column_dimensions[get_column_letter(len(columns) + 1)].width = 80
    ws.freeze_panes = "B2"
    header = [
        _cell(ws, DETAIL_COLUMNS[key], font=BOLD, fill=HEADER_FILL, border=HEADER_BORDER,
              alignment=WRAP_CENTER)
        for key in columns
    ]
    if truncated:
        header.append(_cell(ws, detail_truncated_note(total), font=WARNING_FONT, fill=WARNING_FILL))
    ws.append(header)
    count = 0
    for row in rows:
        line = []
        for key in columns:
            value = row.get(key)
            if key in DETAIL_NUMERIC:
                line.append(_cell(ws, value, number_format="0.00"))
            elif key in DETAIL_DATES:
                line.append(_cell(ws, value, number_format=DATETIME_FORMAT))
            else:
                line.append(_cell(ws, value))
        ws.append(line)
        count += 1
    if columns:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{count + 1}"
    return count


def build_workbook(
    result: dict[str, Any],
    *,
    detail_columns: list[str] | None = None,
    detail_rows: Iterable[dict[str, Any]] | None = None,
    detail_total: int | None = None,
) -> bytes:
    """Genera el .xlsx del ``ReportResult`` (y del detalle, si se pasa ``detail_rows``).

    ``detail_total``: tickets del detalle sin límite; si supera ``MAX_DETAIL_ROWS`` la hoja
    «Detalle» y el encabezado de cada sección avisan que el detalle está recortado.
    """
    workbook = Workbook(write_only=True)
    workbook.properties.creator = "Energuate Service Desk"
    workbook.properties.title = safe_text(result["title"])[:200]
    used: set[str] = {DETAIL_SHEET.casefold()} if detail_rows is not None else set()
    detail_note = None
    if detail_rows is not None and detail_total is not None and detail_total > MAX_DETAIL_ROWS:
        detail_note = detail_truncated_note(detail_total)
    for section in result["sections"]:
        ws = workbook.create_sheet(unique_sheet_title(section["title"], used))
        _write_section(ws, result, section, detail_note=detail_note)
    if detail_rows is not None:
        ws = workbook.create_sheet(DETAIL_SHEET)
        _write_detail(ws, list(detail_columns or []), detail_rows, total=detail_total)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def export_filename(result: dict[str, Any]) -> str:
    slug = re.sub(r"[^A-Za-z0-9áéíóúñÁÉÍÓÚÑ]+", "_", result["title"]).strip("_")[:60] or "reporte"
    start = result["date_from"].replace("-", "")
    end = result["date_to"].replace("-", "")
    return f"{slug}_{start}_{end}.xlsx"
