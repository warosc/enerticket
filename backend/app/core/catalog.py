"""Catálogo de servicio: normalización de valores del AD y resolución de departamentos.

El atributo ``department`` del AD es texto libre y poco uniforme (``"07 COMERCIAL"``,
``"Comercial"``, ``"0903 Gerencia Financiera"``...). Cada valor distinto se normaliza y se
guarda en ``department_aliases``; el administrador asigna los pendientes a un departamento.

- ``normalize_ad_value(raw)``: recorta, pasa a mayúsculas, quita tildes, quita el prefijo
  numérico (``"07 "``, ``"0903 "``) y colapsa espacios.
- ``resolve_department_alias(session, raw)``: devuelve el departamento asignado al alias o
  ``None``. Si el alias no existe lo crea: asignado al departamento cuyo nombre normalizado
  coincide (si hay exactamente uno) o pendiente (``department_id`` nulo), emitiendo
  ``catalog.alias_pending`` (solo lo reciben administradores por WebSocket).
- ``import_catalog_csv(session, raw)``: importación idempotente de categorías, subcategorías
  y ANS desde CSV (``POST /admin/catalog/import``).
"""

from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models.catalog import DepartmentAlias
from app.models.core import Department

logger = logging.getLogger(__name__)

EVENT_ALIAS_PENDING = "catalog.alias_pending"
MAX_ALIAS_LENGTH = 200

_NUMERIC_PREFIX_RE = re.compile(r"^\d+[\s._-]+(?=\S)")
_SPACES_RE = re.compile(r"\s+")


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(raw: str | None) -> str:
    """Mayúsculas, sin tildes y con espacios colapsados (sin quitar prefijos numéricos)."""
    text = strip_accents(str(raw or "")).upper()
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    return _SPACES_RE.sub(" ", text).strip()


def normalize_ad_value(raw: str | None) -> str:
    """Valor del AD normalizado: ``"07  Comercial "`` y ``"COMERCIAL"`` → ``"COMERCIAL"``."""
    text = normalize_name(raw)
    text = _NUMERIC_PREFIX_RE.sub("", text).strip()
    return text[:MAX_ALIAS_LENGTH]


def _queue_pending_event(session: Session, alias: DepartmentAlias) -> None:
    """Encola ``catalog.alias_pending`` para publicarse tras el commit (sin ticket asociado)."""
    from app.core.events import PENDING_KEY

    session.info.setdefault(PENDING_KEY, []).append(
        {
            "type": EVENT_ALIAS_PENDING,
            "ticket_id": 0,
            "ticket_key": None,
            "actor_id": None,
            "data": {
                "alias_id": alias.id,
                "raw_value": alias.raw_value,
                "normalized_value": alias.normalized_value,
            },
            "ts": utcnow().isoformat(),
            "department_id": None,
            "team_id": None,
            "requester_id": None,
            "assignee_id": None,
        }
    )


def _matching_department(session: Session, normalized: str) -> Department | None:
    candidates = [
        department
        for department in session.scalars(select(Department)).all()
        if normalize_ad_value(department.name) == normalized
    ]
    return candidates[0] if len(candidates) == 1 else None


def get_or_create_alias(session: Session, raw: str | None) -> tuple[DepartmentAlias | None, bool]:
    """Devuelve (alias, creado). ``None`` si el valor queda vacío al normalizar."""
    normalized = normalize_ad_value(raw)
    if not normalized:
        return None, False
    alias = session.scalar(
        select(DepartmentAlias).where(DepartmentAlias.normalized_value == normalized)
    )
    if alias is not None:
        return alias, False
    department = _matching_department(session, normalized)
    alias = DepartmentAlias(
        normalized_value=normalized,
        raw_value=str(raw).strip()[:MAX_ALIAS_LENGTH],
        department_id=department.id if department else None,
        created_at=utcnow(),
    )
    session.add(alias)
    session.flush()
    if department is None:
        logger.info("Valor de departamento del AD pendiente de asignar: %r", alias.raw_value)
        _queue_pending_event(session, alias)
    return alias, True


def resolve_department_alias(session: Session, raw: str | None) -> Department | None:
    """Departamento asignado al valor del AD (creando el alias si no existe)."""
    alias, _ = get_or_create_alias(session, raw)
    if alias is None or alias.department_id is None:
        return None
    return session.get(Department, alias.department_id)


# --------------------------------------------------------------------------- importación CSV

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 5000
MAX_NAME_LENGTH = 150
IMPORT_TYPE_KEYS = ("incident", "service_request", "work_order")
IMPORT_POLICY_PRECEDENCE = 50
IMPORT_COLUMNS = ("departamento", "categoria", "subcategoria", "tipos", "ans_horas")
_FORMULA_PREFIXES = ("=", "+", "-", "@")


class CatalogImportError(ValueError):
    """Error que invalida todo el archivo (formato, tamaño, encabezado)."""


class _RowError(ValueError):
    pass


def sanitize_import_cell(value: object) -> str:
    """Texto limpio de una celda: sin caracteres de control, espacios colapsados y sin prefijos
    de fórmula (``= + - @``) para que el valor no se ejecute al exportarlo a una hoja de cálculo."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    text = _SPACES_RE.sub(" ", text).strip()
    while text and text[0] in _FORMULA_PREFIXES:
        text = text[1:].lstrip()
    return text


def _header_key(value: str) -> str:
    return strip_accents(value or "").strip().lower().replace(" ", "_")


def _parse_hours(raw: str) -> int | None:
    from app.services.sla.schemas import MAX_TARGET_MINUTES

    # Sin quitar prefijos de fórmula: «-3» debe rechazarse, no convertirse en 3.
    text = _SPACES_RE.sub("", str(raw or "")).replace(",", ".")
    if not text:
        return None
    try:
        hours = float(text)
    except ValueError as exc:
        raise _RowError(f"ans_horas inválido: «{text[:30]}» (use un número de horas)") from exc
    minutes = round(hours * 60)
    if hours <= 0 or minutes < 1 or minutes > MAX_TARGET_MINUTES:
        raise _RowError("ans_horas debe ser un número de horas mayor que 0 (máximo un año)")
    return minutes


def _parse_types(raw: str) -> list[str]:
    keys = [part.strip().lower() for part in sanitize_import_cell(raw).split("|") if part.strip()]
    unknown = [key for key in keys if key not in IMPORT_TYPE_KEYS]
    if unknown:
        raise _RowError(
            f"Tipos desconocidos: {', '.join(unknown[:5])} (use {'|'.join(IMPORT_TYPE_KEYS)})"
        )
    return list(dict.fromkeys(keys))


def _check_name(value: str, label: str, required: bool) -> str:
    if not value:
        if required:
            raise _RowError(f"Falta {label}")
        return ""
    if len(value) > MAX_NAME_LENGTH:
        raise _RowError(f"{label.capitalize()} supera {MAX_NAME_LENGTH} caracteres")
    return value


def _read_rows(raw: bytes) -> tuple[list[str], list[tuple[int, list[str]]]]:
    if len(raw) > MAX_IMPORT_BYTES:
        raise CatalogImportError("El archivo supera el máximo de 2 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CatalogImportError("El archivo debe estar codificado en UTF-8") from exc
    first_line = text.split("\n", 1)[0]
    # Excel en español guarda CSV con «;»: se detecta por el encabezado.
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        header = next(reader, None)
        if header is None:
            raise CatalogImportError("El archivo está vacío")
        rows: list[tuple[int, list[str]]] = []
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            if len(rows) >= MAX_IMPORT_ROWS:
                raise CatalogImportError(f"El archivo supera el máximo de {MAX_IMPORT_ROWS} filas")
            rows.append((reader.line_num, row))
    except csv.Error as exc:
        raise CatalogImportError(f"CSV inválido: {exc}") from exc
    return [_header_key(h) for h in header], rows


def import_catalog_csv(session: Session, raw: bytes) -> dict:
    """Importa categorías/subcategorías (y ANS opcionales) desde CSV. No hace commit.

    Encabezado ``departamento,categoria,subcategoria,tipos,ans_horas`` (UTF-8; también se
    acepta ``;``). Idempotente: crea o reactiva lo que falta; con ``ans_horas`` crea o actualiza
    la política ``"ANS <depto> / <categoría>[ / <subcategoría>]"``. Las filas con error se
    omiten y se informan con su número de línea. Para una vista previa (``dry_run``) el
    llamador revierte la transacción.
    """
    from app.models.catalog import TicketCategory, TicketSubcategory
    from app.models.sla import SlaPolicy
    from app.services.sla.engine import default_calendar

    header, rows = _read_rows(raw)
    missing = [name for name in ("departamento", "categoria") if name not in header]
    if missing:
        raise CatalogImportError(
            "Faltan columnas en el encabezado: " + ", ".join(missing)
            + f" (esperado: {','.join(IMPORT_COLUMNS)})"
        )
    index = {name: header.index(name) for name in IMPORT_COLUMNS if name in header}

    def cell(row: list[str], name: str) -> str:
        position = index.get(name)
        return row[position] if position is not None and position < len(row) else ""

    result = {
        "created": {"categories": 0, "subcategories": 0, "policies": 0},
        "updated": {"policies": 0},
        "errors": [],
    }
    departments = {normalize_name(d.name): d for d in session.scalars(select(Department)).all()}
    categories: dict[tuple[int, str], TicketCategory] = {
        (c.department_id, normalize_name(c.name)): c
        for c in session.scalars(select(TicketCategory)).all()
    }
    subcategories: dict[tuple[int, str], TicketSubcategory] = {
        (s.category_id, normalize_name(s.name)): s
        for s in session.scalars(select(TicketSubcategory)).all()
    }
    policies = {p.name.strip().lower(): p for p in session.scalars(select(SlaPolicy)).all()}
    updated_policy_ids: set[int] = set()
    calendar = None
    now = utcnow()

    for line, row in rows:
        try:
            department_name = _check_name(sanitize_import_cell(cell(row, "departamento")), "el departamento", True)
            department = departments.get(normalize_name(department_name))
            if department is None:
                raise _RowError(f"Departamento desconocido: «{department_name}»")
            category_name = _check_name(sanitize_import_cell(cell(row, "categoria")), "la categoría", True)
            subcategory_name = _check_name(
                sanitize_import_cell(cell(row, "subcategoria")), "la subcategoría", False
            )
            type_keys = _parse_types(cell(row, "tipos"))
            minutes = _parse_hours(cell(row, "ans_horas"))
            if minutes is not None and calendar is None:
                calendar = default_calendar(session)
                if calendar is None:
                    raise _RowError(
                        "No existe el calendario hábil por defecto para el ANS (configure el módulo SLA)"
                    )
        except _RowError as exc:
            result["errors"].append({"line": line, "message": str(exc)})
            continue

        category_key = (department.id, normalize_name(category_name))
        category = categories.get(category_key)
        if category is None:
            order = sum(1 for key in categories if key[0] == department.id)
            category = TicketCategory(
                department_id=department.id, name=category_name, type_keys=type_keys,
                active=True, sort_order=order,
            )
            session.add(category)
            session.flush()
            categories[category_key] = category
            result["created"]["categories"] += 1
        else:
            category.active = True
            if type_keys and category.type_keys:
                merged = list(dict.fromkeys([*category.type_keys, *type_keys]))
                if merged != list(category.type_keys):
                    category.type_keys = merged
        subcategory = None
        if subcategory_name:
            sub_key = (category.id, normalize_name(subcategory_name))
            subcategory = subcategories.get(sub_key)
            if subcategory is None:
                order = sum(1 for key in subcategories if key[0] == category.id)
                subcategory = TicketSubcategory(
                    category_id=category.id, name=subcategory_name, active=True, sort_order=order
                )
                session.add(subcategory)
                session.flush()
                subcategories[sub_key] = subcategory
                result["created"]["subcategories"] += 1
            else:
                subcategory.active = True
        if minutes is None:
            continue
        name = f"ANS {department.name} / {category.name}"
        if subcategory is not None:
            name += f" / {subcategory.name}"
        name = name[:MAX_NAME_LENGTH]
        wanted = {
            "department_ids": [department.id],
            "category_ids": [] if subcategory is not None else [category.id],
            "subcategory_ids": [subcategory.id] if subcategory is not None else [],
            "resolution_minutes": minutes,
        }
        policy = policies.get(name.lower())
        if policy is None:
            policy = SlaPolicy(
                name=name,
                description="Importada desde el catálogo (CSV)",
                calendar_id=calendar.id,
                priority_keys=[],
                type_keys=[],
                precedence=IMPORT_POLICY_PRECEDENCE,
                first_response_minutes=None,
                warning_threshold_pct=75,
                pause_categories=["waiting"],
                pause_states=[],
                vip_only=False,
                active=True,
                created_at=now,
                updated_at=now,
                **wanted,
            )
            session.add(policy)
            session.flush()
            policies[name.lower()] = policy
            result["created"]["policies"] += 1
            continue
        changed = False
        for field_name, value in wanted.items():
            current = getattr(policy, field_name)
            if (list(current or []) if isinstance(value, list) else current) != value:
                setattr(policy, field_name, value)
                changed = True
        if changed:
            policy.updated_at = now
            if policy.id not in updated_policy_ids:
                updated_policy_ids.add(policy.id)
                result["updated"]["policies"] += 1
    session.flush()
    return result
