"""Motor de reglas de automatización.

``process_event(session, event)`` lo invoca el suscriptor ``"*"``: busca las reglas activas
cuyo ``trigger`` es el tipo del evento, evalúa sus condiciones sobre el ticket y la ``data``
del evento y ejecuta sus acciones como el usuario técnico "Automatización".

Anti-bucles: las acciones de una regla se ejecutan con profundidad +1 (``session.info``).
Los eventos generados con profundidad > ``MAX_DEPTH`` no se evalúan, y una misma regla no
se vuelve a disparar sobre el mismo ticket dentro de la misma cadena de eventos.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from fastapi import HTTPException
from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.events import Event
from app.models.automation import AutomationExecution, AutomationRule
from app.models.core import (
    Priority,
    Team,
    TeamMember,
    Ticket,
    User,
    WorkflowState,
    WorkflowTransition,
)
from app.services.automation.schemas import ACTION_TYPES, NOTIFY_TARGETS, ActionIn
from app.services.automation.system import SYSTEM_SUB, get_system_principal

logger = logging.getLogger(__name__)

MAX_DEPTH = 1
DEPTH_KEY = "flows_automation_depth"
CHAIN_KEY = "flows_automation_chain"
# Eventos que nunca disparan reglas (evitan ruido/bucles).
IGNORED_EVENTS = frozenset({"notification.created", "approval.cancelled"})

_MISSING = object()
_TEMPLATE_RE = re.compile(r"\{\{\s*((?:ticket|event)\.[A-Za-z0-9_.]+)\s*\}\}")


class ActionError(Exception):
    """Error de configuración o ejecución de una acción (mensaje legible)."""


# --------------------------------------------------------------------------- contexto


class RuleContext:
    """Valores disponibles para condiciones y plantillas."""

    def __init__(
        self,
        ticket: Ticket,
        event_type: str | None,
        event_data: dict[str, Any] | None,
        actor_id: int | None,
    ) -> None:
        self.ticket = ticket
        self.event_type = event_type
        self.event_data = event_data or {}
        self.actor_id = actor_id
        self.rule_name: str | None = None

    def get(self, field: str) -> Any:
        ticket = self.ticket
        if field.startswith("ticket."):
            name = field[len("ticket."):]
            if name == "priority":
                return ticket.priority.key if ticket.priority else None
            if name == "priority_rank":
                return ticket.priority.rank if ticket.priority else None
            if name == "priority_name":
                return ticket.priority.name if ticket.priority else None
            if name == "status":
                return ticket.status.key if ticket.status else None
            if name == "status_name":
                return ticket.status.name if ticket.status else None
            if name == "status_category":
                return ticket.status.category if ticket.status else None
            if name == "type":
                return ticket.type.key if ticket.type else None
            if name == "department":
                return ticket.department.name if ticket.department else None
            if name == "team":
                return ticket.team.name if ticket.team else None
            if name == "text":
                return f"{ticket.title or ''}\n{ticket.description or ''}"
            if name == "key":
                return ticket.key
            if name == "assignee":
                return ticket.assignee.display_name if ticket.assignee else None
            if name == "requester":
                return ticket.requester.display_name if ticket.requester else None
            if name in (
                "id",
                "title",
                "description",
                "department_id",
                "team_id",
                "assignee_id",
                "requester_id",
                "project_id",
            ):
                return getattr(ticket, name)
            return None
        if field == "event.type":
            return self.event_type
        if field == "event.actor_id":
            return self.actor_id
        if field.startswith("event."):
            value: Any = self.event_data
            for part in field[len("event."):].split("."):
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return None
            return value
        return None


# --------------------------------------------------------------------------- condiciones


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _norm(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().casefold()
    return value


def _equals(actual: Any, expected: Any) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    a_num, e_num = _as_number(actual), _as_number(expected)
    if a_num is not None and e_num is not None:
        return a_num == e_num
    if isinstance(actual, bool) or isinstance(expected, bool):
        return str(actual).casefold() == str(expected).casefold()
    return _norm(str(actual)) == _norm(str(expected))


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, (str, list, dict)) and len(value) == 0) or (
        isinstance(value, str) and not value.strip()
    )


def compare(actual: Any, op: str, expected: Any) -> bool:
    if op == "is_empty":
        return _is_empty(actual)
    if op == "is_not_empty":
        return not _is_empty(actual)
    if op == "eq":
        if isinstance(actual, list):
            return any(_equals(item, expected) for item in actual)
        return _equals(actual, expected)
    if op == "neq":
        return not compare(actual, "eq", expected)
    if op == "in":
        return any(compare(actual, "eq", item) for item in _as_list(expected))
    if op == "not_in":
        return not compare(actual, "in", expected)
    if op in ("contains", "not_contains"):
        if actual is None:
            found = False
        elif isinstance(actual, list):
            found = any(_equals(item, expected) for item in actual)
        else:
            found = _norm(str(expected)) in _norm(str(actual))
        return found if op == "contains" else not found
    if op == "starts_with":
        return actual is not None and _norm(str(actual)).startswith(_norm(str(expected)))
    if op in ("gt", "gte", "lt", "lte"):
        a_num, e_num = _as_number(actual), _as_number(expected)
        if a_num is None or e_num is None:
            return False
        return {
            "gt": a_num > e_num,
            "gte": a_num >= e_num,
            "lt": a_num < e_num,
            "lte": a_num <= e_num,
        }[op]
    raise ValueError(f"Operador desconocido: {op}")


def evaluate_conditions(
    conditions: dict[str, Any] | None, context: RuleContext
) -> tuple[bool, list[dict[str, Any]]]:
    """Evalúa ``{"match": "all"|"any", "rules": [...]}``. Sin reglas = siempre coincide."""
    conditions = conditions or {}
    rules = conditions.get("rules") or []
    match = conditions.get("match", "all")
    details: list[dict[str, Any]] = []
    for rule in rules:
        field = str(rule.get("field", ""))
        op = str(rule.get("op", "eq"))
        expected = rule.get("value")
        actual = context.get(field)
        try:
            ok = compare(actual, op, expected)
        except ValueError:
            ok = False
        details.append(
            {"field": field, "op": op, "value": expected, "actual": _jsonable(actual), "result": ok}
        )
    if not details:
        return True, details
    results = [d["result"] for d in details]
    matched = any(results) if match == "any" else all(results)
    return matched, details


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def render_template(text: str | None, context: RuleContext) -> str:
    """Sustituye ``{{ticket.key}}``, ``{{ticket.title}}``, ``{{event.to_state}}``..."""
    if not text:
        return ""

    def repl(match: re.Match[str]) -> str:
        value = context.get(match.group(1))
        return "" if value is None else str(value)

    return _TEMPLATE_RE.sub(repl, text)


# --------------------------------------------------------------------------- destinatarios


def resolve_targets(session: Session, ticket: Ticket, targets: Sequence[str]) -> list[int]:
    ids: list[int] = []
    for target in targets:
        if target == "requester":
            ids.append(ticket.requester_id)
        elif target == "assignee":
            if ticket.assignee_id:
                ids.append(ticket.assignee_id)
        elif target in ("team_leads", "team_members"):
            if ticket.team_id is None:
                continue
            stmt = select(TeamMember.user_id).where(TeamMember.team_id == ticket.team_id)
            if target == "team_leads":
                stmt = stmt.where(TeamMember.role == "lead")
            ids.extend(session.scalars(stmt).all())
        elif target == "department_agents":
            stmt = (
                select(User.id)
                .join(TeamMember, TeamMember.user_id == User.id)
                .join(Team, Team.id == TeamMember.team_id)
                .where(
                    Team.department_id == ticket.department_id,
                    cast(User.roles, String).like('%"agent"%'),
                )
            )
            ids.extend(session.scalars(stmt).all())
        elif target.startswith("role:"):
            role = target.split(":", 1)[1]
            ids.extend(
                session.scalars(
                    select(User.id).where(
                        User.active.is_(True),
                        User.keycloak_sub != SYSTEM_SUB,
                        cast(User.roles, String).like(f'%"{role}"%'),
                    )
                ).all()
            )
        elif target.startswith("user:"):
            try:
                ids.append(int(target.split(":", 1)[1]))
            except ValueError:
                continue
    return list(dict.fromkeys(i for i in ids if i is not None))


# --------------------------------------------------------------------------- acciones


def describe_action(action: dict[str, Any]) -> str:
    kind = action.get("type", "")
    label = ACTION_TYPES.get(kind, kind)
    if kind == "assign_user":
        return f"{label} #{action.get('user_id')}"
    if kind == "assign_team":
        return f"{label} #{action.get('team_id')}"
    if kind == "set_priority":
        return f"{label}: {action.get('priority_key')}"
    if kind in ("transition", "request_approval"):
        return f"{label}: {action.get('to_state')}"
    if kind == "add_comment":
        return f"{label} ({action.get('visibility', 'internal')})"
    if kind == "notify":
        targets = ", ".join(NOTIFY_TARGETS.get(t, t) for t in action.get("targets", []))
        return f"{label} a {targets}"
    return label


def _find_transition(session: Session, ticket: Ticket, to_state_key: str) -> WorkflowTransition:
    current = session.get(WorkflowState, ticket.status_id)
    if current is None:
        raise ActionError("El ticket no tiene estado válido")
    target = session.scalar(
        select(WorkflowState).where(
            WorkflowState.workflow_id == current.workflow_id, WorkflowState.key == to_state_key
        )
    )
    if target is None:
        raise ActionError(f"Estado desconocido: {to_state_key}")
    transition = session.scalar(
        select(WorkflowTransition).where(
            WorkflowTransition.workflow_id == current.workflow_id,
            WorkflowTransition.from_state_id == current.id,
            WorkflowTransition.to_state_id == target.id,
        )
    )
    if transition is None:
        raise ActionError(f"Transición no permitida: {current.key} → {to_state_key}")
    return transition


def execute_action(
    session: Session, action: dict[str, Any], ticket: Ticket, context: RuleContext
) -> str:
    """Ejecuta una acción y devuelve un detalle legible. Lanza ActionError/HTTPException."""
    from app.services import tickets as ticket_svc
    from app.services.approvals import service as approvals
    from app.services.notifications import KIND_AUTOMATION, notify

    spec = ActionIn.model_validate(action)
    system = get_system_principal(session)
    kind = spec.type

    if kind == "assign_user":
        if ticket.assignee_id == spec.user_id:
            return "Sin cambios: ya estaba asignado"
        ticket_svc.update_ticket(session, system, ticket, {"assignee_id": spec.user_id})
        return f"Asignado al usuario #{spec.user_id}"

    if kind == "assign_team":
        team = session.get(Team, spec.team_id)
        if team is None:
            raise ActionError(f"El equipo #{spec.team_id} no existe")
        if ticket.team_id == team.id:
            return "Sin cambios: ya estaba en el equipo"
        changes: dict[str, Any] = {"team_id": team.id}
        if team.department_id != ticket.department_id:
            changes["department_id"] = team.department_id
        ticket_svc.update_ticket(session, system, ticket, changes)
        return f"Asignado al equipo {team.name}"

    if kind == "set_priority":
        priority = session.scalar(select(Priority).where(Priority.key == spec.priority_key))
        if priority is None:
            raise ActionError(f"Prioridad desconocida: {spec.priority_key}")
        if ticket.priority_id == priority.id:
            return "Sin cambios: ya tenía esa prioridad"
        ticket_svc.update_ticket(session, system, ticket, {"priority_key": priority.key})
        return f"Prioridad cambiada a {priority.name}"

    if kind in ("transition", "request_approval"):
        to_state = spec.to_state or ""
        transition = _find_transition(session, ticket, to_state)
        plan = approvals.resolve_plan(session, ticket, transition)
        if kind == "request_approval" and plan is None:
            plan = approvals.ApprovalPlan(
                None, [approvals.StepSpec(**s) for s in approvals.DEFAULT_STEPS]
            )
        if plan is not None and approvals.usable_approval(session, ticket, transition.id) is None:
            request, created = approvals.ensure_request(
                session,
                ticket,
                transition,
                plan,
                requested_by_id=system.user_id,
                reason=(
                    f"Solicitada por la regla de automatización «{context.rule_name}»"
                    if context.rule_name
                    else "Solicitada por una regla de automatización"
                ),
            )
            verb = "Se creó" if created else "Ya existía"
            return f"{verb} la solicitud de aprobación #{request.id} para «{transition.name}»"
        if kind == "request_approval":
            return "Ya existe una aprobación vigente para esa transición"
        ticket_svc.transition_ticket(
            session,
            system,
            ticket,
            to_state,
            None,
            extra_guard_data={approvals.INLINE_FLAG: True},
        )
        return f"Estado cambiado a {to_state}"

    if kind == "add_comment":
        body = render_template(spec.body, context).strip()
        if not body:
            raise ActionError("El comentario quedó vacío")
        ticket_svc.add_comment(session, system, ticket, body, spec.visibility)
        return f"Comentario {'interno' if spec.visibility == 'internal' else 'público'} agregado"

    if kind == "notify":
        recipients = resolve_targets(session, ticket, spec.targets)
        created = notify(
            session,
            recipients,
            kind=KIND_AUTOMATION,
            title=render_template(spec.title, context),
            body=render_template(spec.body, context),
            ticket=ticket,
            actor_id=None,
        )
        return f"{len(created)} notificaciones enviadas"

    raise ActionError(f"Acción desconocida: {kind}")  # pragma: no cover


def _run_rule(
    session: Session,
    rule: AutomationRule,
    ticket: Ticket,
    context: RuleContext,
    depth: int,
) -> tuple[str, list[dict[str, Any]], str | None]:
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    previous_depth = session.info.get(DEPTH_KEY, 0)
    session.info[DEPTH_KEY] = depth + 1
    context.rule_name = rule.name
    try:
        for action in rule.actions or []:
            kind = action.get("type", "?") if isinstance(action, dict) else "?"
            try:
                with session.begin_nested():
                    detail = execute_action(session, action, ticket, context)
                results.append({"type": kind, "ok": True, "detail": detail})
            except HTTPException as exc:
                message = exc.detail.get("message") if isinstance(exc.detail, dict) else exc.detail
                results.append({"type": kind, "ok": False, "detail": str(message)})
                errors.append(f"{ACTION_TYPES.get(kind, kind)}: {message}")
            except Exception as exc:  # noqa: BLE001 - se registra en la ejecución
                if not isinstance(exc, ActionError):
                    logger.exception("Error ejecutando la acción %s de la regla %s", kind, rule.id)
                results.append({"type": kind, "ok": False, "detail": str(exc)})
                errors.append(f"{ACTION_TYPES.get(kind, kind)}: {exc}")
    finally:
        session.info[DEPTH_KEY] = previous_depth
    ok_count = sum(1 for r in results if r["ok"])
    if not errors:
        status = "success"
    elif ok_count:
        status = "partial"
    else:
        status = "error"
    return status, results, "; ".join(errors)[:4000] or None


def active_rules(session: Session, trigger: str) -> list[AutomationRule]:
    return list(
        session.scalars(
            select(AutomationRule)
            .where(AutomationRule.active.is_(True), AutomationRule.trigger == trigger)
            .order_by(AutomationRule.order, AutomationRule.id)
        ).all()
    )


def process_event(session: Session, event: Event) -> int:
    """Evalúa las reglas del evento. Devuelve cuántas reglas se ejecutaron."""
    if event.type in IGNORED_EVENTS:
        return 0
    depth = int(session.info.get(DEPTH_KEY, 0))
    if depth > MAX_DEPTH:
        logger.debug("Evento %s ignorado por automatización (profundidad %s)", event.type, depth)
        return 0
    rules = active_rules(session, event.type)
    if not rules:
        return 0
    top_level = depth == 0
    if top_level:
        session.info[CHAIN_KEY] = set()
    chain: set[tuple[int, int]] = session.info.setdefault(CHAIN_KEY, set())
    executed = 0
    ticket = event.ticket
    try:
        for rule in rules:
            key = (rule.id, ticket.id)
            if key in chain:
                continue
            context = RuleContext(ticket, event.type, dict(event.data or {}), event.actor_id)
            matched, _ = evaluate_conditions(rule.conditions, context)
            if not matched:
                continue
            chain.add(key)
            status, results, error = _run_rule(session, rule, ticket, context, depth)
            session.add(
                AutomationExecution(
                    rule_id=rule.id,
                    ticket_id=ticket.id,
                    event_type=event.type,
                    history_id=event.history_id,
                    depth=depth,
                    status=status,
                    result=results,
                    error=error,
                    created_at=utcnow(),
                )
            )
            session.flush()
            executed += 1
            if rule.stop_processing:
                break
    finally:
        if top_level:
            session.info.pop(CHAIN_KEY, None)
    return executed


def dry_run(
    session: Session,
    conditions: dict[str, Any],
    actions: list[dict[str, Any]],
    ticket: Ticket,
    event_type: str | None,
    event_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Evalúa una regla contra un ticket sin efectos secundarios."""
    context = RuleContext(ticket, event_type, event_data or {}, None)
    matched, details = evaluate_conditions(conditions, context)
    preview = []
    for action in actions:
        item = {"type": action.get("type"), "description": describe_action(action)}
        if action.get("type") in ("add_comment", "notify"):
            item["rendered_title"] = render_template(action.get("title"), context) or None
            item["rendered_body"] = render_template(action.get("body"), context) or None
        if action.get("type") == "notify":
            item["recipient_ids"] = resolve_targets(session, ticket, action.get("targets", []))
        preview.append(item)
    return {
        "matched": matched,
        "conditions": details,
        "actions": preview,
        "ticket": {"id": ticket.id, "key": ticket.key, "title": ticket.title},
    }


def validate_references(session: Session, actions: Sequence[ActionIn]) -> list[str]:
    """Comprueba que usuarios/equipos/prioridades/estados referenciados existan."""
    errors: list[str] = []
    for index, action in enumerate(actions, start=1):
        prefix = f"Acción {index} ({ACTION_TYPES[action.type]})"
        if action.type == "assign_user":
            user = session.get(User, action.user_id)
            if user is None or not user.active:
                errors.append(f"{prefix}: el usuario no existe o está inactivo")
            elif not {"agent", "admin"} & set(user.roles or []):
                errors.append(f"{prefix}: el usuario debe tener rol de agente")
        elif action.type == "assign_team" and session.get(Team, action.team_id) is None:
            errors.append(f"{prefix}: el equipo no existe")
        elif action.type == "set_priority" and session.scalar(
            select(Priority.id).where(Priority.key == action.priority_key)
        ) is None:
            errors.append(f"{prefix}: la prioridad no existe")
        elif action.type in ("transition", "request_approval") and session.scalar(
            select(WorkflowState.id).where(WorkflowState.key == action.to_state)
        ) is None:
            errors.append(f"{prefix}: el estado no existe")
        elif action.type == "notify":
            for target in action.targets:
                if target.startswith("user:") and session.get(User, int(target[5:])) is None:
                    errors.append(f"{prefix}: el usuario {target[5:]} no existe")
    return errors
