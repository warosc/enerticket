"""Seeds de la fundación: departamentos, equipos, tipos, prioridades y flujo por defecto.

Idempotente: solo crea lo que falta; no sobrescribe cambios hechos por administradores
(salvo completar ``external_group`` de un equipo cuando está vacío).

Fase 3 (marcas de una sola vez en ``seed_state``):
- ``core.ticket_types.v2``: ``incident`` → «Incidencia», ``service_request`` → «Petición»,
  crea ``work_order`` → «Orden de trabajo» y desactiva ``change`` y ``problem`` (no los borra).
- ``core.departments.v2``: crea, si no existen, los departamentos de atención de TI (cada uno
  con su «Equipo <nombre>») con ``serves_tickets=true``. No borra los existentes ni crea
  categorías (las carga el administrador).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.seed_state import get_seed_state, mark_seed_applied
from app.models.core import (
    Department,
    Priority,
    Team,
    TicketType,
    WorkflowDefinition,
    WorkflowState,
    WorkflowTransition,
)

logger = logging.getLogger(__name__)

DEPARTMENTS = (
    "Telecomunicaciones",
    "Seguridad de Redes",
    "Ciberseguridad / SOC",
    "Riesgo y Cumplimiento",
    "Proyectos TI",
    "General",
)

TICKET_TYPES = (
    ("incident", "Incidente"),
    ("service_request", "Solicitud de servicio"),
    ("change", "Cambio"),
    ("problem", "Problema"),
)

PRIORITIES = (
    ("low", "Baja", 1),
    ("medium", "Media", 2),
    ("high", "Alta", 3),
    ("critical", "Crítica", 4),
)

# Fase 3: nombres y tipos vigentes (``core.ticket_types.v2``).
TICKET_TYPES_V2 = (
    ("incident", "Incidencia"),
    ("service_request", "Petición"),
    ("work_order", "Orden de trabajo"),
)
DEACTIVATED_TYPES_V2 = ("change", "problem")

# Fase 3: departamentos de atención de TI (``core.departments.v2``).
DEPARTMENTS_V2 = (
    "Sistemas Comerciales",
    "Sistemas Corporativos",
    "Telecomunicaciones",
    "Seguridad Informática",
    "Soporte Técnico",
)

DEFAULT_WORKFLOW_KEY = "default"

# key, nombre, categoría, orden, inicial, final
STATES = (
    ("new", "Nuevo", "todo", 1, True, False),
    ("triage", "En triaje", "todo", 2, False, False),
    ("assigned", "Asignado", "todo", 3, False, False),
    ("in_progress", "En progreso", "in_progress", 4, False, False),
    ("pending", "En espera", "waiting", 5, False, False),
    ("resolved", "Resuelto", "done", 6, False, False),
    ("closed", "Cerrado", "done", 7, False, True),
)

# origen, destino, nombre
TRANSITIONS = (
    ("new", "triage", "Enviar a triaje"),
    ("new", "assigned", "Asignar"),
    ("new", "in_progress", "Iniciar trabajo"),
    ("new", "closed", "Cerrar"),
    ("triage", "assigned", "Asignar"),
    ("triage", "in_progress", "Iniciar trabajo"),
    ("triage", "closed", "Cerrar"),
    ("assigned", "triage", "Devolver a triaje"),
    ("assigned", "in_progress", "Iniciar trabajo"),
    ("in_progress", "pending", "Poner en espera"),
    ("in_progress", "assigned", "Devolver a asignado"),
    ("in_progress", "resolved", "Resolver"),
    ("pending", "in_progress", "Reanudar"),
    ("pending", "resolved", "Resolver"),
    ("resolved", "closed", "Cerrar"),
    ("resolved", "in_progress", "Reabrir"),
)


SEED_KEY_DEPARTMENTS = "core.departments.v1"
SEED_KEY_TYPES_V2 = "core.ticket_types.v2"
SEED_KEY_DEPARTMENTS_V2 = "core.departments.v2"


def _seed_departments_and_teams(session: Session) -> None:
    """Departamentos y equipos iniciales: una sola vez (el administrador puede renombrarlos
    o borrarlos sin que vuelvan a aparecer en el siguiente arranque)."""
    if get_seed_state(session, SEED_KEY_DEPARTMENTS) is not None:
        return
    if session.scalar(select(Department.id).limit(1)) is not None:
        # Base creada antes de existir seed_state: no se toca lo que haya.
        mark_seed_applied(session, SEED_KEY_DEPARTMENTS, {"adopted": True})
        return
    groups = settings.team_group_map
    for name in DEPARTMENTS:
        department = session.scalar(select(Department).where(Department.name == name))
        if department is None:
            department = Department(name=name, serves_tickets=True, active=True)
            session.add(department)
            session.flush()
        team_name = f"Equipo {name}"
        team = session.scalar(
            select(Team).where(Team.department_id == department.id, Team.name == team_name)
        )
        group = groups.get(name)
        if team is None:
            session.add(Team(department_id=department.id, name=team_name, external_group=group))
        elif team.external_group is None and group:
            team.external_group = group
    session.flush()
    mark_seed_applied(session, SEED_KEY_DEPARTMENTS)


def _seed_types_and_priorities(session: Session) -> None:
    for key, name in TICKET_TYPES:
        if session.scalar(select(TicketType).where(TicketType.key == key)) is None:
            session.add(TicketType(key=key, name=name))
    for key, name, rank in PRIORITIES:
        if session.scalar(select(Priority).where(Priority.key == key)) is None:
            session.add(Priority(key=key, name=name, rank=rank))
    session.flush()


def _seed_types_v2(session: Session) -> None:
    """Tipos de la Fase 3 (una sola vez: el administrador puede ajustarlos después)."""
    if get_seed_state(session, SEED_KEY_TYPES_V2) is not None:
        return
    for key, name in TICKET_TYPES_V2:
        ticket_type = session.scalar(select(TicketType).where(TicketType.key == key))
        if ticket_type is None:
            session.add(TicketType(key=key, name=name, active=True))
        else:
            ticket_type.name = name
            ticket_type.active = True
    for key in DEACTIVATED_TYPES_V2:
        ticket_type = session.scalar(select(TicketType).where(TicketType.key == key))
        if ticket_type is not None:
            ticket_type.active = False
    session.flush()
    mark_seed_applied(session, SEED_KEY_TYPES_V2)


def _seed_departments_v2(session: Session) -> None:
    """Departamentos de atención de TI (una sola vez; no toca los existentes)."""
    if get_seed_state(session, SEED_KEY_DEPARTMENTS_V2) is not None:
        return
    created: list[str] = []
    for name in DEPARTMENTS_V2:
        department = session.scalar(select(Department).where(Department.name == name))
        if department is None:
            department = Department(name=name, serves_tickets=True, active=True)
            session.add(department)
            session.flush()
            created.append(name)
        # Un departamento de atención necesita al menos un equipo (asignación por defecto).
        has_team = session.scalar(select(Team.id).where(Team.department_id == department.id).limit(1))
        if has_team is None:
            session.add(
                Team(
                    department_id=department.id,
                    name=f"Equipo {name}",
                    external_group=settings.team_group_map.get(name),
                )
            )
    session.flush()
    mark_seed_applied(session, SEED_KEY_DEPARTMENTS_V2, {"created": created})


def _seed_workflow(session: Session) -> None:
    workflow = session.scalar(
        select(WorkflowDefinition).where(WorkflowDefinition.key == DEFAULT_WORKFLOW_KEY)
    )
    if workflow is None:
        has_default = session.scalar(
            select(WorkflowDefinition.id).where(WorkflowDefinition.is_default.is_(True))
        )
        workflow = WorkflowDefinition(
            key=DEFAULT_WORKFLOW_KEY,
            name="Flujo estándar de tickets",
            is_default=has_default is None,
        )
        session.add(workflow)
        session.flush()

    states: dict[str, WorkflowState] = {
        s.key: s
        for s in session.scalars(
            select(WorkflowState).where(WorkflowState.workflow_id == workflow.id)
        ).all()
    }
    for key, name, category, order, is_initial, is_final in STATES:
        if key not in states:
            state = WorkflowState(
                workflow_id=workflow.id,
                key=key,
                name=name,
                category=category,
                order=order,
                is_initial=is_initial,
                is_final=is_final,
            )
            session.add(state)
            states[key] = state
    session.flush()

    existing = {
        (t.from_state_id, t.to_state_id)
        for t in session.scalars(
            select(WorkflowTransition).where(WorkflowTransition.workflow_id == workflow.id)
        ).all()
    }
    for source, target, name in TRANSITIONS:
        pair = (states[source].id, states[target].id)
        if pair not in existing:
            session.add(
                WorkflowTransition(
                    workflow_id=workflow.id,
                    from_state_id=pair[0],
                    to_state_id=pair[1],
                    name=name,
                    requires_approval=False,
                )
            )
    session.flush()


def run(session: Session) -> None:
    _seed_departments_and_teams(session)
    _seed_types_and_priorities(session)
    _seed_types_v2(session)
    _seed_departments_v2(session)
    _seed_workflow(session)
    logger.info("Catálogo core verificado")
