"""Seeds del módulo SLA: calendarios, feriados de Guatemala y políticas por prioridad.

Idempotente y respetuoso con los cambios de administradores:
- Los calendarios y las políticas iniciales se crean UNA sola vez (marca ``sla.defaults.v1``
  en ``seed_state``): si un administrador los renombra, desactiva o borra, no reaparecen
  en el siguiente arranque. Una base que ya tenía calendarios/políticas antes de existir la
  marca se adopta tal cual (no se crea nada).
- Importa los feriados oficiales del año anterior al actual hasta 3 años adelante, solo para
  los años del calendario hábil que aún no tengan feriados (si un administrador borró uno,
  no se vuelve a crear). El calendario se identifica por su id (guardado en la marca), no
  por su nombre editable. Al ejecutarse en cada arranque, los años nuevos se agregan solos.

Políticas por defecto (editables en /sla):

| Política                 | Prioridad | Calendario              | 1.ª respuesta | Resolución |
|--------------------------|-----------|-------------------------|---------------|------------|
| Crítica 24x7             | critical  | 24x7                    | 15 min        | 4 h        |
| Alta - horario hábil     | high      | Horario hábil Guatemala | 30 min        | 8 h        |
| Media - horario hábil    | medium    | Horario hábil Guatemala | 2 h           | 24 h       |
| Baja - horario hábil     | low       | Horario hábil Guatemala | 4 h           | 40 h       |

Los estados de categoría ``waiting`` (p.ej. "En espera") pausan el reloj; advertencia al 75 %.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import utcnow
from app.core.seed_state import get_seed_state, mark_seed_applied
from app.models.sla import BusinessCalendar, SlaPolicy
from app.services.sla.calendar import get_zone, normalize_windows
from app.services.sla.catalog import import_official_holidays, years_around

logger = logging.getLogger(__name__)

CALENDAR_BUSINESS = "Horario hábil Guatemala"
CALENDAR_24X7 = "24x7"

CALENDARS = (
    {
        "name": CALENDAR_BUSINESS,
        "description": "Lunes a viernes de 08:00 a 17:00 (America/Guatemala), excluye feriados oficiales.",
        "windows": [{"weekday": day, "start": "08:00", "end": "17:00"} for day in range(5)],
        "holidays": True,
    },
    {
        "name": CALENDAR_24X7,
        "description": "Servicio continuo: todos los días, las 24 horas, sin feriados.",
        "windows": [{"weekday": day, "start": "00:00", "end": "24:00"} for day in range(7)],
        "holidays": False,
    },
)

POLICIES = (
    {
        "name": "Crítica 24x7",
        "description": "Incidentes críticos: atención continua, también fines de semana y feriados.",
        "calendar": CALENDAR_24X7,
        "priority_keys": ["critical"],
        "precedence": 10,
        "first_response_minutes": 15,
        "resolution_minutes": 4 * 60,
    },
    {
        "name": "Alta - horario hábil",
        "description": "Prioridad alta en horario hábil.",
        "calendar": CALENDAR_BUSINESS,
        "priority_keys": ["high"],
        "precedence": 20,
        "first_response_minutes": 30,
        "resolution_minutes": 8 * 60,
    },
    {
        "name": "Media - horario hábil",
        "description": "Prioridad media en horario hábil.",
        "calendar": CALENDAR_BUSINESS,
        "priority_keys": ["medium"],
        "precedence": 30,
        "first_response_minutes": 2 * 60,
        "resolution_minutes": 24 * 60,
    },
    {
        "name": "Baja - horario hábil",
        "description": "Prioridad baja en horario hábil.",
        "calendar": CALENDAR_BUSINESS,
        "priority_keys": ["low"],
        "precedence": 40,
        "first_response_minutes": 4 * 60,
        "resolution_minutes": 40 * 60,
    },
)


SEED_KEY = "sla.defaults.v1"


def _import_holidays(session: Session, calendar: BusinessCalendar) -> None:
    local_today = datetime.now(get_zone(calendar.timezone)).date()
    try:
        result = import_official_holidays(
            session, calendar, years_around(local_today), only_empty_years=True
        )
    except ValueError:
        logger.exception("No se pudieron importar los feriados del calendario %s", calendar.name)
    else:
        if result["created"]:
            logger.info("Feriados agregados a %s: %d", calendar.name, result["created"])


def _holiday_calendar_ids(session: Session, data: dict | None) -> list[int]:
    ids = (data or {}).get("holiday_calendar_ids")
    if isinstance(ids, list):
        return [int(i) for i in ids if isinstance(i, int)]
    # Marca adoptada sin ids: se usa el calendario hábil por nombre si aún existe.
    calendar_id = session.scalar(select(BusinessCalendar.id).where(BusinessCalendar.name == CALENDAR_BUSINESS))
    return [calendar_id] if calendar_id is not None else []


def run(session: Session) -> None:
    timezone_name = settings.tz_business or "America/Guatemala"
    get_zone(timezone_name)  # valida TZ_BUSINESS antes de crear nada
    state = get_seed_state(session, SEED_KEY)
    if state is None and (
        session.scalar(select(BusinessCalendar.id).limit(1)) is not None
        or session.scalar(select(SlaPolicy.id).limit(1)) is not None
    ):
        # Base anterior a seed_state: se adopta la configuración existente.
        state = mark_seed_applied(session, SEED_KEY, None)
    if state is not None:
        for calendar_id in _holiday_calendar_ids(session, state.data):
            calendar = session.get(BusinessCalendar, calendar_id)
            if calendar is not None:
                _import_holidays(session, calendar)
        session.flush()
        return
    _seed_defaults(session, timezone_name)


def _seed_defaults(session: Session, timezone_name: str) -> None:
    now = utcnow()
    calendars: dict[str, BusinessCalendar] = {}
    holiday_ids: list[int] = []
    for spec in CALENDARS:
        calendar = session.scalar(select(BusinessCalendar).where(BusinessCalendar.name == spec["name"]))
        if calendar is None:
            calendar = BusinessCalendar(
                name=spec["name"],
                description=spec["description"],
                timezone=timezone_name,
                windows=normalize_windows(spec["windows"]),
                created_at=now,
                updated_at=now,
            )
            session.add(calendar)
            session.flush()
            logger.info("Calendario SLA creado: %s", calendar.name)
        calendars[spec["name"]] = calendar
        if spec["holidays"]:
            holiday_ids.append(calendar.id)
            _import_holidays(session, calendar)

    for spec in POLICIES:
        if session.scalar(select(SlaPolicy.id).where(SlaPolicy.name == spec["name"])) is not None:
            continue
        session.add(
            SlaPolicy(
                name=spec["name"],
                description=spec["description"],
                calendar_id=calendars[spec["calendar"]].id,
                priority_keys=spec["priority_keys"],
                type_keys=[],
                department_ids=[],
                precedence=spec["precedence"],
                first_response_minutes=spec["first_response_minutes"],
                resolution_minutes=spec["resolution_minutes"],
                warning_threshold_pct=75,
                pause_categories=["waiting"],
                pause_states=[],
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
        logger.info("Política SLA creada: %s", spec["name"])
    session.flush()
    mark_seed_applied(session, SEED_KEY, {"holiday_calendar_ids": holiday_ids})
