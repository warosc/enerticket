"""Plantillas del generador de reportes: permisos, validación, programación y envío por correo.

Reglas:
- Ver: administradores ven todas; el resto ve las de fábrica (``is_system``), las propias y
  las compartidas con alguno de sus roles (``shared_roles``) o departamentos
  (``shared_department_ids``: departamentos de sus equipos o su departamento del AD).
- Editar/eliminar: dueño o administrador; las de fábrica solo se pueden **duplicar**.
- Programar envíos (``schedule.enabled``): solo administradores o líderes de equipo; hasta
  ``MAX_RECIPIENTS`` correos y, si ``REPORT_RECIPIENT_DOMAINS`` (coma) no está vacío, solo
  de esos dominios.
- Quien ejecuta una plantilla (compartida o de fábrica) siempre ve los datos de SU alcance.
  El envío programado usa el alcance del dueño (Principal reconstruido desde la BD).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any, Literal, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.accounts import is_stale
from app.core.config import settings
from app.core.db import SessionLocal, utcnow
from app.core.security import KNOWN_ROLES, Principal, build_principal
from app.models.core import Department, User
from app.models.reports import ReportTemplate
from app.services.reports.builder import (
    DEFAULT_DETAIL_COLUMNS,
    ReportDefinition,
    MAX_DETAIL_ROWS,
    build_context,
    business_tz,
    count_detail_rows,
    iter_detail_rows,
    parse_definition,
    run_report,
)
from app.services.reports.xlsx_export import XLSX_MEDIA_TYPE, build_workbook, export_filename

logger = logging.getLogger(__name__)

MAX_RECIPIENTS = 20
MAX_TEMPLATES_PER_USER = 200
SCHEDULED_BATCH = 50
_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")

# Estados de la última ejecución programada (``last_status``).
STATUS_SENT = "sent"
STATUS_SMTP_DISABLED = "smtp_disabled"
STATUS_OWNER_INACTIVE = "owner_inactive"
# El dueño no inicia sesión hace más de ``ACCOUNT_STALE_DAYS``: sus roles/equipos son la última
# foto conocida y no deben seguir produciendo envíos (ver ``app.core.accounts``).
STATUS_OWNER_STALE = "owner_stale"
STATUS_NOT_ALLOWED = "not_allowed"
STATUS_NO_RECIPIENTS = "no_recipients"
STATUS_INVALID = "invalid_definition"
STATUS_ERROR = "error"
# Error transitorio (SMTP caído, timeout…): se reintenta antes del siguiente periodo.
STATUS_RETRYING = "retrying"
# Esperas (minutos) de los reintentos consecutivos tras un error; agotados, queda «error» hasta
# el siguiente periodo. Un reintento nunca se programa después de la siguiente ejecución normal.
RETRY_DELAYS_MINUTES = (15, 30, 60)


def recipient_domains() -> list[str]:
    """Dominios permitidos para destinatarios (``REPORT_RECIPIENT_DOMAINS``; vacío = cualquiera)."""
    raw = getattr(settings, "report_recipient_domains", None) or ""
    if isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = str(raw).split(",")
    return sorted({item.strip().lower().lstrip("@") for item in items if item and item.strip()})


# --------------------------------------------------------------------------- esquemas


class ScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    frequency: Literal["monthly", "weekly"] = "monthly"
    # monthly: día del mes 1-28; weekly: día de la semana 0 = lunes … 6 = domingo.
    day: int = Field(default=1, ge=0, le=28)
    hour: int = Field(default=7, ge=0, le=23)
    recipients: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)

    @field_validator("recipients")
    @classmethod
    def _valid_emails(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            email = item.strip().lower()
            if len(email) > 254 or not _EMAIL_RE.match(email):
                raise ValueError(f"Correo inválido: {item[:80]}")
            if email not in cleaned:
                cleaned.append(email)
        domains = recipient_domains()
        if domains:
            invalid = [email for email in cleaned if email.rsplit("@", 1)[1] not in domains]
            if invalid:
                raise ValueError(
                    "Solo se permiten destinatarios de los dominios: " + ", ".join(domains)
                )
        return cleaned

    @model_validator(mode="after")
    def _check_day(self) -> "ScheduleIn":
        if self.frequency == "monthly" and not 1 <= self.day <= 28:
            raise ValueError("Para envíos mensuales el día debe estar entre 1 y 28")
        if self.frequency == "weekly" and not 0 <= self.day <= 6:
            raise ValueError("Para envíos semanales el día debe estar entre 0 (lunes) y 6 (domingo)")
        if self.enabled and not self.recipients:
            raise ValueError("Indique al menos un destinatario para el envío programado")
        return self


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=2000)
    definition: ReportDefinition
    shared_roles: list[str] = Field(default_factory=list, max_length=len(KNOWN_ROLES))
    shared_department_ids: list[int] = Field(default_factory=list, max_length=200)
    schedule: Optional[ScheduleIn] = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = _CONTROL_RE.sub(" ", value).strip()
        if not value:
            raise ValueError("El nombre es obligatorio")
        return value

    @field_validator("shared_roles")
    @classmethod
    def _known_roles(cls, value: list[str]) -> list[str]:
        unknown = [role for role in value if role not in KNOWN_ROLES]
        if unknown:
            raise ValueError(f"Roles desconocidos: {', '.join(unknown)}")
        return sorted(set(value))

    @field_validator("shared_department_ids")
    @classmethod
    def _unique_departments(cls, value: list[int]) -> list[int]:
        return sorted(set(value))


class TemplatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    description: Optional[str] = Field(default=None, max_length=2000)
    definition: Optional[ReportDefinition] = None
    shared_roles: Optional[list[str]] = Field(default=None, max_length=len(KNOWN_ROLES))
    shared_department_ids: Optional[list[int]] = Field(default=None, max_length=200)
    schedule: Optional[ScheduleIn] = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = _CONTROL_RE.sub(" ", value).strip()
        if not value:
            raise ValueError("El nombre es obligatorio")
        return value

    @field_validator("shared_roles")
    @classmethod
    def _known_roles(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = [role for role in value if role not in KNOWN_ROLES]
        if unknown:
            raise ValueError(f"Roles desconocidos: {', '.join(unknown)}")
        return sorted(set(value))

    @field_validator("shared_department_ids")
    @classmethod
    def _unique_departments(cls, value: list[int] | None) -> list[int] | None:
        return sorted(set(value)) if value is not None else None


# --------------------------------------------------------------------------- permisos


def can_schedule(principal: Principal) -> bool:
    return principal.is_admin or bool(principal.lead_team_ids)


def principal_department_ids(principal: Principal) -> set[int]:
    ids = set(principal.department_ids)
    if principal.home_department_id is not None:
        ids.add(principal.home_department_id)
    return ids


def can_view_template(principal: Principal, template: ReportTemplate) -> bool:
    if principal.is_admin or template.is_system or template.owner_id == principal.user_id:
        return True
    if set(template.shared_roles or ()) & set(principal.roles):
        return True
    return bool(set(template.shared_department_ids or ()) & principal_department_ids(principal))


def can_edit_template(principal: Principal, template: ReportTemplate) -> bool:
    if template.is_system:
        return False
    return principal.is_admin or template.owner_id == principal.user_id


def list_templates(session: Session, principal: Principal) -> list[ReportTemplate]:
    templates = session.scalars(
        select(ReportTemplate).order_by(ReportTemplate.is_system.desc(), ReportTemplate.name, ReportTemplate.id)
    ).all()
    return [template for template in templates if can_view_template(principal, template)]


def get_template(session: Session, principal: Principal, template_id: int, *, edit: bool = False) -> ReportTemplate:
    template = session.get(ReportTemplate, template_id)
    if template is None or not can_view_template(principal, template):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Plantilla no encontrada")
    if edit and not can_edit_template(principal, template):
        if template.is_system:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="Las plantillas de fábrica no se pueden modificar; duplíquela para personalizarla",
            )
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Solo el dueño o un administrador puede modificarla")
    return template


def template_out(session: Session, principal: Principal, template: ReportTemplate) -> dict[str, Any]:
    editable = can_edit_template(principal, template)
    owner = None
    if template.owner_id is not None:
        user = session.get(User, template.owner_id)
        if user is not None:
            owner = {"id": user.id, "display_name": user.display_name}
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description or "",
        "owner": owner,
        "is_system": bool(template.is_system),
        "definition": template.definition,
        "shared_roles": list(template.shared_roles or []),
        "shared_department_ids": list(template.shared_department_ids or []),
        # Los destinatarios solo los ve quien puede editar la plantilla.
        "schedule": template.schedule if editable else None,
        "next_run_at": template.next_run_at if editable else None,
        "last_run_at": template.last_run_at if editable else None,
        "last_status": template.last_status if editable else None,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
        "can_edit": editable,
        "can_delete": editable,
        "can_schedule": editable and can_schedule(principal),
    }


# --------------------------------------------------------------------------- programación


def next_run_at(schedule: dict[str, Any], after: datetime) -> datetime:
    """Próxima ejecución (UTC) estrictamente posterior a ``after``, en hora de Guatemala."""
    tz = business_tz()
    local = after.astimezone(tz)
    hour = int(schedule.get("hour", 7))
    day = int(schedule.get("day", 1))
    if schedule.get("frequency") == "weekly":
        candidate = (local + timedelta(days=(day - local.weekday()) % 7)).replace(
            hour=hour, minute=0, second=0, microsecond=0
        )
        if candidate <= local:
            candidate += timedelta(days=7)
    else:
        candidate = local.replace(day=day, hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= local:
            year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
            candidate = candidate.replace(year=year, month=month)
    return candidate.astimezone(timezone.utc)


def _validate_departments(session: Session, department_ids: list[int]) -> None:
    if not department_ids:
        return
    found = set(session.scalars(select(Department.id).where(Department.id.in_(department_ids))).all())
    missing = sorted(set(department_ids) - found)
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Departamentos inexistentes: {', '.join(str(i) for i in missing[:10])}",
        )


def _apply_schedule(principal: Principal, template: ReportTemplate, schedule: ScheduleIn | None) -> None:
    if schedule is not None and schedule.enabled and not can_schedule(principal):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Solo administradores o líderes de equipo pueden programar envíos",
        )
    if schedule is None:
        template.schedule = None
        template.next_run_at = None
        template.retry_count = 0
        return
    template.schedule = schedule.model_dump()
    template.next_run_at = next_run_at(template.schedule, utcnow()) if schedule.enabled else None
    template.retry_count = 0


def create_template(session: Session, principal: Principal, payload: TemplateIn) -> ReportTemplate:
    owned = len(session.scalars(select(ReportTemplate.id).where(ReportTemplate.owner_id == principal.user_id)).all())
    if owned >= MAX_TEMPLATES_PER_USER:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Alcanzó el máximo de {MAX_TEMPLATES_PER_USER} plantillas; elimine alguna",
        )
    _validate_departments(session, payload.shared_department_ids)
    template = ReportTemplate(
        name=payload.name,
        description=payload.description,
        owner_id=principal.user_id,
        is_system=False,
        definition=payload.definition.model_dump(mode="json"),
        shared_roles=payload.shared_roles,
        shared_department_ids=payload.shared_department_ids,
    )
    _apply_schedule(principal, template, payload.schedule)
    session.add(template)
    session.flush()
    return template


def update_template(
    session: Session, principal: Principal, template: ReportTemplate, payload: TemplatePatch
) -> ReportTemplate:
    fields = payload.model_fields_set
    for name in ("name", "shared_roles", "shared_department_ids"):
        if name in fields and getattr(payload, name) is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"«{name}» no puede ser nulo")
    if "name" in fields:
        template.name = payload.name  # type: ignore[assignment]
    if "description" in fields:
        template.description = payload.description or ""
    if "definition" in fields:
        if payload.definition is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="La definición es obligatoria")
        template.definition = payload.definition.model_dump(mode="json")
    if "shared_roles" in fields:
        template.shared_roles = payload.shared_roles  # type: ignore[assignment]
    if "shared_department_ids" in fields:
        _validate_departments(session, payload.shared_department_ids or [])
        template.shared_department_ids = payload.shared_department_ids  # type: ignore[assignment]
    if "schedule" in fields:
        _apply_schedule(principal, template, payload.schedule)
    template.updated_at = utcnow()
    session.flush()
    return template


def duplicate_template(session: Session, principal: Principal, template: ReportTemplate) -> ReportTemplate:
    copy = TemplateIn(
        name=(template.name[:140] + " (copia)"),
        description=template.description or "",
        definition=parse_definition(template.definition),
    )
    return create_template(session, principal, copy)


def definition_with_period(
    definition: ReportDefinition,
    period: str | None = None,
    date_from: Any = None,
    date_to: Any = None,
) -> ReportDefinition:
    """Copia la definición con otro periodo (ejecutar una plantilla para otro mes)."""
    if period is None and date_from is None and date_to is None:
        return definition
    data = definition.model_dump(mode="json")
    if period is not None:
        data["period"] = period
    if date_from is not None:
        data["date_from"] = str(date_from)
    if date_to is not None:
        data["date_to"] = str(date_to)
    if (date_from is not None or date_to is not None) and period is None:
        data["period"] = "custom"
    return parse_definition(data)


def render_xlsx(
    session: Session,
    principal: Principal,
    definition: ReportDefinition,
    *,
    include_detail: bool = True,
    detail_columns: list[str] | None = None,
    now: datetime | None = None,
) -> tuple[bytes, str, dict[str, Any]]:
    """Ejecuta la definición y genera el .xlsx. Devuelve ``(contenido, nombre, resultado)``.

    Con detalle, ``resultado["detail"] = {"total", "rows", "truncated"}`` indica si la hoja
    «Detalle» quedó recortada a ``MAX_DETAIL_ROWS`` (el libro también lo avisa).
    """
    result = run_report(session, principal, definition, now)
    columns = list(detail_columns or DEFAULT_DETAIL_COLUMNS)
    rows = None
    total = None
    if include_detail:
        ctx = build_context(principal, definition, now)
        total = count_detail_rows(session, ctx)
        rows = iter_detail_rows(session, ctx, columns, limit=MAX_DETAIL_ROWS)
        result["detail"] = {
            "total": total,
            "rows": min(total, MAX_DETAIL_ROWS),
            "truncated": total > MAX_DETAIL_ROWS,
        }
    content = build_workbook(result, detail_columns=columns, detail_rows=rows, detail_total=total)
    return content, export_filename(result), result


# --------------------------------------------------------------------------- envío programado


def _clean_header(text: str, limit: int = 150) -> str:
    return _CONTROL_RE.sub(" ", text).strip()[:limit]


def build_report_email(
    template: ReportTemplate, recipients: list[str], content: bytes, filename: str, result: dict[str, Any]
) -> EmailMessage:
    name = _clean_header(template.name)
    message = EmailMessage()
    message["Subject"] = f"Reporte programado: {name}"
    message["From"] = formataddr(("Energuate Service Desk", settings.smtp_from))
    message["To"] = ", ".join(recipients)
    message["Message-ID"] = make_msgid(domain=settings.smtp_from.rsplit("@", 1)[-1] or None)
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(
        "Buen día:\n\n"
        f"Se adjunta el reporte «{name}» del {result['date_from']} al {result['date_to']}.\n"
        f"Alcance: {result['scope']['label']}.\n"
        + (
            f"Aviso: la hoja «Detalle» incluye solo los primeros {MAX_DETAIL_ROWS} de "
            f"{result['detail']['total']} tickets.\n"
            if (result.get("detail") or {}).get("truncated")
            else ""
        )
        + "\n"
        "Este correo se generó automáticamente desde Energuate Service Desk; no responda a este mensaje.\n"
    )
    message.add_attachment(
        content,
        maintype="application",
        subtype=XLSX_MEDIA_TYPE.split("/", 1)[1],
        filename=filename,
    )
    return message


def _send(message: EmailMessage) -> None:
    # Import diferido: reutiliza el cliente SMTP de notificaciones (y su sustituto en pruebas).
    from app.services.notifications.email import send_message

    send_message(message)


def _smtp_enabled() -> bool:
    return bool((settings.smtp_host or "").strip())


def execute_scheduled(session: Session, template: ReportTemplate, now: datetime) -> str:
    """Genera y envía una plantilla programada. Devuelve el ``last_status``."""
    if not _smtp_enabled():
        return STATUS_SMTP_DISABLED
    owner = session.get(User, template.owner_id) if template.owner_id is not None else None
    if owner is None or not owner.active:
        return STATUS_OWNER_INACTIVE
    if is_stale(owner):
        return STATUS_OWNER_STALE
    principal = build_principal(session, owner, set(owner.roles or []), None)
    if not can_schedule(principal):
        return STATUS_NOT_ALLOWED
    schedule = template.schedule or {}
    try:
        recipients = ScheduleIn.model_validate({**schedule, "enabled": False}).recipients
    except ValueError:
        return STATUS_NO_RECIPIENTS
    if not recipients:
        return STATUS_NO_RECIPIENTS
    try:
        definition = parse_definition(template.definition)
    except HTTPException:
        return STATUS_INVALID
    try:
        content, filename, result = render_xlsx(session, principal, definition, now=now)
        _send(build_report_email(template, recipients, content, filename, result))
    except Exception:
        logger.exception("Falló el envío programado de la plantilla %s", template.id)
        return STATUS_ERROR
    logger.info("Reporte programado %s enviado a %d destinatarios", template.id, len(recipients))
    return STATUS_SENT


def run_due_templates(
    now: datetime | None = None, session_factory: Callable[[], Session] = SessionLocal
) -> dict[str, int]:
    """Ejecuta las plantillas con ``schedule.enabled`` y ``next_run_at <= now``.

    Cada plantilla se procesa en su transacción: primero se reserva (avanza ``next_run_at`` y
    confirma, así otro beat/worker no la repite) y luego se genera y envía. Si el envío falla
    (``error``) se reprograma un reintento acotado (``RETRY_DELAYS_MINUTES``) antes del siguiente
    periodo, con ``last_status = "retrying"``.
    """
    now = now or utcnow()
    counts: dict[str, int] = {}
    with session_factory() as session:
        due_ids = session.scalars(
            select(ReportTemplate.id)
            .where(ReportTemplate.next_run_at.is_not(None), ReportTemplate.next_run_at <= now)
            .order_by(ReportTemplate.next_run_at, ReportTemplate.id)
            .limit(SCHEDULED_BATCH)
        ).all()
    for template_id in due_ids:
        with session_factory() as session:
            template = session.scalars(
                select(ReportTemplate)
                .where(
                    ReportTemplate.id == template_id,
                    ReportTemplate.next_run_at.is_not(None),
                    ReportTemplate.next_run_at <= now,
                )
                .with_for_update(skip_locked=True)
            ).first()
            if template is None:
                continue
            schedule = template.schedule or {}
            if template.is_system or not schedule.get("enabled"):
                template.next_run_at = None
                session.commit()
                continue
            template.next_run_at = next_run_at(schedule, now)
            template.last_run_at = now
            session.commit()
            try:
                result_status = execute_scheduled(session, template, now)
            except Exception:  # pragma: no cover - defensa: nunca romper el beat
                logger.exception("Error inesperado en la plantilla programada %s", template_id)
                session.rollback()
                result_status = STATUS_ERROR
            template = session.get(ReportTemplate, template_id)
            if template is not None:
                result_status = _schedule_retry(template, result_status, now)
                template.last_status = result_status
                session.commit()
            counts[result_status] = counts.get(result_status, 0) + 1
    return counts


def _schedule_retry(template: ReportTemplate, result_status: str, now: datetime) -> str:
    """Tras un ``error`` programa un reintento (si quedan y llega antes del siguiente periodo)."""
    if result_status != STATUS_ERROR:
        template.retry_count = 0
        return result_status
    attempt = int(template.retry_count or 0)
    if attempt < len(RETRY_DELAYS_MINUTES):
        retry_at = now + timedelta(minutes=RETRY_DELAYS_MINUTES[attempt])
        if template.next_run_at is not None and retry_at < template.next_run_at:
            template.next_run_at = retry_at
            template.retry_count = attempt + 1
            logger.warning(
                "Reintento %d de la plantilla programada %s a las %s",
                attempt + 1, template.id, retry_at.isoformat(),
            )
            return STATUS_RETRYING
    template.retry_count = 0
    return STATUS_ERROR
