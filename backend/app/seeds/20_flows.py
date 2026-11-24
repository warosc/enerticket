"""Seeds de flujos: usuario técnico, aprobaciones de cambios y reglas de ejemplo.

Idempotente y conservador (no pisa cambios de administradores):
- Crea el usuario técnico "Automatización" si no existe.
- Políticas y reglas de ejemplo: UNA sola vez (marca ``flows.examples.v1`` en
  ``seed_state``). Si el administrador las borra no reaparecen en el siguiente arranque
  (antes se recreaban cada vez que la tabla quedaba vacía).
- Políticas de aprobación: solo si ``approval_policies`` está vacía. Marca como
  ``requires_approval`` las transiciones que inician el trabajo (``new|triage|assigned →
  in_progress``) y crea para cada una la política "Cambios" (solo tickets de tipo ``change``):
  etapa 1 líder del equipo, etapa 2 comité de cambios (rol approver). El workflow core no
  distingue por tipo; la condición por tipo vive en la política (otros tipos no requieren
  aprobación en esas transiciones).
- Reglas de automatización de ejemplo: solo si ``automation_rules`` está vacía.

``SEED_FLOWS_EXAMPLES=false`` desactiva las políticas y reglas de ejemplo (es el valor por
defecto de ``docker-compose.prod.yml``: la etapa «Comité de cambios» es global y da a todos
los aprobadores acceso a los tickets de cambio mientras su etapa está activa).
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.seed_state import get_seed_state, mark_seed_applied
from app.models.approvals import ApprovalPolicy
from app.models.automation import AutomationRule
from app.models.core import Department, Team, WorkflowDefinition, WorkflowState, WorkflowTransition
from app.services.automation.system import get_system_user

logger = logging.getLogger(__name__)

APPROVAL_TRANSITIONS = (("new", "in_progress"), ("triage", "in_progress"), ("assigned", "in_progress"))

CHANGE_STEPS = [
    {"name": "Líder del equipo", "approver_type": "team_lead", "scope": "department", "rule": "any"},
    {
        "name": "Comité de cambios (CAB)",
        "approver_type": "role",
        "role": "approver",
        "scope": "global",
        "rule": "any",
    },
]

SOC_DEPARTMENT = "Ciberseguridad / SOC"


def _enabled() -> bool:
    return os.environ.get("SEED_FLOWS_EXAMPLES", "true").strip().lower() not in ("0", "false", "no")


def _default_workflow(session: Session) -> WorkflowDefinition | None:
    return session.scalars(
        select(WorkflowDefinition).order_by(WorkflowDefinition.is_default.desc(), WorkflowDefinition.id)
    ).first()


def _seed_policies(session: Session) -> int:
    if session.scalar(select(func.count()).select_from(ApprovalPolicy)):
        return 0
    workflow = _default_workflow(session)
    if workflow is None:
        logger.warning("Sin workflow: no se crean políticas de aprobación")
        return 0
    states = {
        s.key: s
        for s in session.scalars(select(WorkflowState).where(WorkflowState.workflow_id == workflow.id)).all()
    }
    created = 0
    now = utcnow()
    for source, target in APPROVAL_TRANSITIONS:
        if source not in states or target not in states:
            continue
        transition = session.scalar(
            select(WorkflowTransition).where(
                WorkflowTransition.workflow_id == workflow.id,
                WorkflowTransition.from_state_id == states[source].id,
                WorkflowTransition.to_state_id == states[target].id,
            )
        )
        if transition is None:
            continue
        transition.requires_approval = True
        session.add(
            ApprovalPolicy(
                name=f"Cambios: {states[source].name} → {states[target].name}",
                active=True,
                transition_id=transition.id,
                ticket_type_keys=["change"],
                department_ids=[],
                steps=CHANGE_STEPS,
                order=10,
                created_at=now,
                updated_at=now,
            )
        )
        created += 1
    session.flush()
    return created


def _critical_notify_action() -> dict:
    return {
        "type": "notify",
        "targets": ["department_agents", "team_leads"],
        "title": "Ticket crítico {{ticket.key}}: {{ticket.title}}",
        "body": "Departamento: {{ticket.department}}. Prioridad: {{ticket.priority_name}}. "
        "Atiéndalo de inmediato.",
    }


def _seed_rules(session: Session) -> int:
    if session.scalar(select(func.count()).select_from(AutomationRule)):
        return 0
    now = utcnow()
    rules = [
        AutomationRule(
            name="Crítico: avisar a los agentes del departamento",
            description="Notifica a los agentes y líderes del departamento cuando se crea un ticket crítico.",
            active=True,
            trigger="ticket.created",
            conditions={"match": "all", "rules": [{"field": "ticket.priority", "op": "eq", "value": "critical"}]},
            actions=[_critical_notify_action()],
            order=10,
            stop_processing=False,
            created_at=now,
            updated_at=now,
        ),
        AutomationRule(
            name="Escalado a crítico: avisar a los agentes del departamento",
            description="Notifica cuando un ticket existente sube a prioridad crítica.",
            active=True,
            trigger="ticket.updated",
            conditions={
                "match": "all",
                "rules": [{"field": "event.changes.priority.to", "op": "eq", "value": "critical"}],
            },
            actions=[_critical_notify_action()],
            order=20,
            stop_processing=False,
            created_at=now,
            updated_at=now,
        ),
    ]
    soc = session.scalar(select(Department).where(Department.name == SOC_DEPARTMENT))
    soc_team = (
        session.scalar(select(Team).where(Team.department_id == soc.id).order_by(Team.id)) if soc else None
    )
    if soc is not None and soc_team is not None:
        rules.append(
            AutomationRule(
                name="SOC: posible phishing",
                description="Tickets del SOC que mencionan phishing pasan a prioridad alta y al equipo SOC.",
                active=True,
                trigger="ticket.created",
                conditions={
                    "match": "all",
                    "rules": [
                        {"field": "ticket.department_id", "op": "eq", "value": soc.id},
                        {"field": "ticket.text", "op": "contains", "value": "phishing"},
                        {"field": "ticket.priority_rank", "op": "lt", "value": 3},
                    ],
                },
                actions=[
                    {"type": "set_priority", "priority_key": "high"},
                    {"type": "assign_team", "team_id": soc_team.id},
                    {
                        "type": "add_comment",
                        "visibility": "internal",
                        "body": "Clasificado automáticamente como posible phishing: prioridad alta "
                        "y asignado a {{ticket.team}}.",
                    },
                ],
                order=30,
                stop_processing=False,
                created_at=now,
                updated_at=now,
            )
        )
    session.add_all(rules)
    session.flush()
    return len(rules)


SEED_KEY = "flows.examples.v1"


def run(session: Session) -> None:
    get_system_user(session)
    if not _enabled():
        logger.info("SEED_FLOWS_EXAMPLES desactivado: sin políticas ni reglas de ejemplo")
        return
    if get_seed_state(session, SEED_KEY) is not None:
        logger.info("Ejemplos de flujos ya sembrados anteriormente: no se recrean")
        return
    policies = _seed_policies(session)
    rules = _seed_rules(session)
    mark_seed_applied(session, SEED_KEY, {"policies": policies, "rules": rules})
    logger.info("Flujos verificados (políticas creadas: %d, reglas creadas: %d)", policies, rules)
