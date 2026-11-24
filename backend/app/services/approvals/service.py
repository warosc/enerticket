"""Lógica de aprobaciones de transiciones.

Flujo:
1. ``ticket.transition`` (guard): si la transición requiere aprobación según
   ``resolve_plan`` y no hay una aprobación vigente, se crea (o reutiliza) una
   ``approval_request`` con sus etapas y se responde 409 ``approval_required``.
2. Los aprobadores deciden (``decide``). Las etapas son secuenciales; cada una se aprueba
   con un aprobador (``any``) o con todos (``all``). Un rechazo rechaza la solicitud.
3. Al aprobarse la última etapa se ejecuta la transición automáticamente en nombre de quien
   la solicitó (``transition_ticket`` con ``extra_guard_data`` de aprobación).

Configuración (``approval_policies``): para una transición se elige la primera política
activa (por ``order``) cuyo filtro de tipos/departamentos coincide con el ticket. Si la
transición tiene políticas pero ninguna coincide, no se requiere aprobación (así se distingue
por tipo de ticket, p.ej. solo ``change``). Si no tiene políticas y ``requires_approval`` es
verdadero se usa ``DEFAULT_STEPS``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.accounts import recent_login_clause
from app.core.db import utcnow
from app.core.events import emit
from app.core.permissions import can_edit_ticket
from app.core.security import ROLE_ADMIN, Principal, build_principal
from app.models.approvals import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalStep,
)
from app.models.core import (
    Department,
    Team,
    TeamMember,
    Ticket,
    TicketHistory,
    User,
    WorkflowState,
    WorkflowTransition,
)
from app.services.approvals.schemas import StepSpec
from app.services.automation.system import SYSTEM_SUB, get_system_principal

logger = logging.getLogger(__name__)

EVENT_REQUESTED = "approval.requested"
EVENT_DECIDED = "approval.decided"
EVENT_CANCELLED = "approval.cancelled"
ERROR_CODE = "approval_required"

# Clave de extra_guard_data para pedir que el guard no confirme la sesión (uso interno).
INLINE_FLAG = "approval_inline"

DEFAULT_STEPS: tuple[dict[str, Any], ...] = (
    {
        "name": "Aprobación del departamento",
        "approver_type": "role",
        "role": "approver",
        "scope": "department",
        "rule": "any",
    },
)


@dataclass
class ApprovalPlan:
    policy: ApprovalPolicy | None
    steps: list[StepSpec]


# --------------------------------------------------------------------------- configuración


def _policy_matches(policy: ApprovalPolicy, ticket: Ticket) -> bool:
    type_keys = [k for k in (policy.ticket_type_keys or []) if k]
    if type_keys and (ticket.type is None or ticket.type.key not in type_keys):
        return False
    departments = [d for d in (policy.department_ids or []) if d is not None]
    if departments and ticket.department_id not in departments:
        return False
    return True


def resolve_plan(
    session: Session, ticket: Ticket, transition: WorkflowTransition
) -> ApprovalPlan | None:
    """Etapas de aprobación que necesita ``transition`` para ``ticket`` (None = no requiere)."""
    if not transition.requires_approval:
        return None
    policies = session.scalars(
        select(ApprovalPolicy)
        .where(ApprovalPolicy.transition_id == transition.id, ApprovalPolicy.active.is_(True))
        .order_by(ApprovalPolicy.order, ApprovalPolicy.id)
    ).all()
    if not policies:
        return ApprovalPlan(None, [StepSpec(**spec) for spec in DEFAULT_STEPS])
    for policy in policies:
        if _policy_matches(policy, ticket):
            steps = [StepSpec(**spec) for spec in (policy.steps or [])]
            if not steps:
                steps = [StepSpec(**spec) for spec in DEFAULT_STEPS]
            return ApprovalPlan(policy, steps)
    return None


# --------------------------------------------------------------------------- aprobadores


def _active_users(session: Session, user_ids: Iterable[int]) -> list[int]:
    ids = sorted(set(user_ids))
    if not ids:
        return []
    return list(
        session.scalars(
            select(User.id).where(
                User.id.in_(ids),
                User.active.is_(True),
                User.keycloak_sub != SYSTEM_SUB,
                recent_login_clause(),
            )
        ).all()
    )


def _can_approve(session: Session, user_id: int) -> bool:
    user = session.get(User, user_id)
    return bool(user and {"approver", "agent", ROLE_ADMIN}.intersection(user.roles or []))


def _users_with_role(session: Session, role: str, department_id: int | None = None) -> list[int]:
    stmt = select(User.id).where(
        User.active.is_(True),
        User.keycloak_sub != SYSTEM_SUB,
        cast(User.roles, String).like(f'%"{role}"%'),
        recent_login_clause(),
    )
    if department_id is not None:
        stmt = stmt.where(
            User.id.in_(
                select(TeamMember.user_id)
                .join(Team, Team.id == TeamMember.team_id)
                .where(Team.department_id == department_id)
            )
        )
    return list(session.scalars(stmt.order_by(User.id)).all())


def _team_members(session: Session, team_id: int | None, *, leads_only: bool) -> list[int]:
    if team_id is None:
        return []
    stmt = select(TeamMember.user_id).where(TeamMember.team_id == team_id)
    if leads_only:
        stmt = stmt.where(TeamMember.role == "lead")
    return _active_users(session, session.scalars(stmt).all())


def resolve_approvers(
    session: Session, spec: StepSpec, ticket: Ticket, requested_by_id: int | None
) -> tuple[list[int], dict[str, Any]]:
    """Usuarios que deben decidir la etapa y los atributos descriptivos de la etapa.

    Se excluye a quien solicitó la transición (segregación de funciones). Una etapa por rol
    con alcance ``department`` NO se amplía a toda la organización: si el departamento no
    tiene aprobadores se recurre a los administradores (y se registra un aviso), para no dar
    visibilidad del ticket a aprobadores de otras áreas. Si tampoco hay administradores, la
    etapa queda sin aprobadores designados y solo un administrador puede decidirla.
    """
    attrs: dict[str, Any] = {
        "approver_type": spec.approver_type,
        "approver_user_id": None,
        "approver_role": None,
        "approver_team_id": None,
        "approver_department_id": None,
    }
    candidates: list[int] = []
    if spec.approver_type == "user":
        attrs["approver_user_id"] = spec.user_id
        candidates = _active_users(session, [spec.user_id] if spec.user_id else [])
    elif spec.approver_type == "role":
        role = spec.role or "approver"
        attrs["approver_role"] = role
        if spec.scope == "department":
            attrs["approver_department_id"] = ticket.department_id
            candidates = _users_with_role(session, role, ticket.department_id)
            if not candidates:
                logger.warning(
                    "Etapa «%s» de %s: el departamento %s no tiene usuarios con rol %s; "
                    "se designa a los administradores",
                    spec.name,
                    ticket.key,
                    ticket.department_id,
                    role,
                )
        else:
            candidates = _users_with_role(session, role)
    elif spec.approver_type == "team":
        attrs["approver_team_id"] = spec.team_id
        candidates = _team_members(session, spec.team_id, leads_only=False)
    elif spec.approver_type == "team_lead":
        team_id = spec.team_id or ticket.team_id
        attrs["approver_team_id"] = team_id
        candidates = _team_members(session, team_id, leads_only=True)
        if not candidates:
            # Sin líder: aprobadores del departamento del ticket.
            candidates = _users_with_role(session, "approver", ticket.department_id)

    # Solo usuarios con rol aprobador/agente/admin (los demás no podrían ver el ticket).
    approvers = [
        uid
        for uid in dict.fromkeys(candidates)
        if uid != requested_by_id and _can_approve(session, uid)
    ]
    if not approvers:
        approvers = [uid for uid in _users_with_role(session, ROLE_ADMIN) if uid != requested_by_id]
    return approvers, attrs


# --------------------------------------------------------------------------- consultas


def open_request(session: Session, ticket_id: int, transition_id: int) -> ApprovalRequest | None:
    return session.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.ticket_id == ticket_id,
            ApprovalRequest.transition_id == transition_id,
            ApprovalRequest.status == "pending",
        )
        .order_by(ApprovalRequest.id.desc())
    )


def _last_status_change(session: Session, ticket_id: int):
    return session.scalar(
        select(func.max(TicketHistory.timestamp)).where(
            TicketHistory.ticket_id == ticket_id,
            TicketHistory.event_type == "ticket.transitioned",
        )
    )


def usable_approval(
    session: Session, ticket: Ticket, transition_id: int
) -> ApprovalRequest | None:
    """Aprobación concedida y aún no consumida, decidida después del último cambio de estado."""
    request = session.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.ticket_id == ticket.id,
            ApprovalRequest.transition_id == transition_id,
            ApprovalRequest.status == "approved",
            ApprovalRequest.executed_at.is_(None),
        )
        .order_by(ApprovalRequest.decided_at.desc(), ApprovalRequest.id.desc())
    )
    if request is None or request.decided_at is None:
        return None
    last_change = _last_status_change(session, ticket.id)
    if last_change is not None and last_change > request.decided_at:
        return None  # el ticket cambió de estado después de aprobarse: caducada
    return request


def current_step(request: ApprovalRequest) -> ApprovalStep | None:
    for step in sorted(request.steps, key=lambda s: (s.order, s.id)):
        if step.status == "pending":
            return step
    return None


def _pending_approver_ids(step: ApprovalStep) -> list[int]:
    return [d.approver_id for d in step.decisions if d.decision == "pending"]


# --------------------------------------------------------------------------- creación


def _transition_states(session: Session, transition: WorkflowTransition) -> tuple[WorkflowState, WorkflowState]:
    from_state = session.get(WorkflowState, transition.from_state_id)
    to_state = session.get(WorkflowState, transition.to_state_id)
    assert from_state is not None and to_state is not None
    return from_state, to_state


def _activate_step(
    session: Session,
    request: ApprovalRequest,
    step: ApprovalStep,
    actor_id: int | None,
    *,
    created: bool,
) -> None:
    step.status = "pending"
    session.flush()
    transition = request.transition
    from_state, to_state = _transition_states(session, transition)
    emit(
        session,
        EVENT_REQUESTED,
        request.ticket,
        actor_id,
        {
            "approval_request_id": request.id,
            "transition_id": transition.id,
            "transition_name": transition.name,
            "from_state": from_state.key,
            "to_state": to_state.key,
            "step_id": step.id,
            "step_order": step.order,
            "step_name": step.name,
            "approver_ids": _pending_approver_ids(step),
            "requested_by_id": request.requested_by_id,
            "reason": request.reason,
            "created": created,
        },
    )


def create_request(
    session: Session,
    ticket: Ticket,
    transition: WorkflowTransition,
    plan: ApprovalPlan,
    *,
    requested_by_id: int | None,
    reason: str | None = None,
) -> ApprovalRequest:
    """Crea la solicitud con sus etapas y decisiones pendientes y activa la primera etapa."""
    now = utcnow()
    request = ApprovalRequest(
        ticket_id=ticket.id,
        transition_id=transition.id,
        policy_id=plan.policy.id if plan.policy else None,
        status="pending",
        requested_by_id=requested_by_id,
        reason=(reason or "").strip()[:4000] or None,
        created_at=now,
    )
    session.add(request)
    session.flush()
    for index, spec in enumerate(plan.steps, start=1):
        approvers, attrs = resolve_approvers(session, spec, ticket, requested_by_id)
        step = ApprovalStep(
            request_id=request.id,
            order=index,
            name=spec.name,
            rule=spec.rule,
            status="waiting",
            **attrs,
        )
        session.add(step)
        session.flush()
        for approver_id in approvers:
            session.add(
                ApprovalDecision(
                    step_id=step.id,
                    approver_id=approver_id,
                    decision="pending",
                    is_override=False,
                    created_at=now,
                )
            )
    session.flush()
    session.refresh(request)
    first = min(request.steps, key=lambda s: (s.order, s.id))
    _activate_step(session, request, first, requested_by_id, created=True)
    logger.info(
        "Solicitud de aprobación %s creada para %s (%s)", request.id, ticket.key, transition.name
    )
    return request


def ensure_request(
    session: Session,
    ticket: Ticket,
    transition: WorkflowTransition,
    plan: ApprovalPlan,
    *,
    requested_by_id: int | None,
    reason: str | None = None,
) -> tuple[ApprovalRequest, bool]:
    """Reutiliza la solicitud pendiente para (ticket, transición) o crea una. (request, creada)."""
    existing = open_request(session, ticket.id, transition.id)
    if existing is not None:
        return existing, False
    return (
        create_request(
            session, ticket, transition, plan, requested_by_id=requested_by_id, reason=reason
        ),
        True,
    )


def approval_required_error(request: ApprovalRequest, *, created: bool) -> HTTPException:
    step = current_step(request)
    transition_name = request.transition.name if request.transition else "la transición"
    if created:
        message = (
            f"La transición «{transition_name}» requiere aprobación. "
            f"Se creó la solicitud de aprobación #{request.id}"
        )
    else:
        message = (
            f"La transición «{transition_name}» requiere aprobación. "
            f"Ya existe la solicitud #{request.id} pendiente"
        )
    if step is not None:
        message += f" (etapa actual: {step.name})"
    message += ". El cambio se aplicará automáticamente cuando se apruebe."
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "code": ERROR_CODE,
            "message": message,
            "approval_request_id": request.id,
            "created": created,
        },
    )


# --------------------------------------------------------------------------- guard


def transition_guard(
    session: Session, ticket: Ticket, actor: Principal, data: dict[str, Any]
) -> None:
    """Guard ``ticket.transition``: bloquea con 409 si falta una aprobación vigente.

    Si crea la solicitud y la sesión no está dentro de un SAVEPOINT (petición HTTP normal),
    confirma la sesión antes de lanzar el 409 para que la solicitud persista (la petición
    se revierte al fallar). Con ``extra_guard_data[INLINE_FLAG]`` o dentro de un SAVEPOINT
    solo hace flush y el llamador decide.
    """
    transition_id = data.get("transition_id")
    if transition_id is None:
        return
    transition = session.get(WorkflowTransition, transition_id)
    if transition is None:
        return

    granted_id = data.get("approval_request_id")
    if granted_id is not None:
        granted = session.get(ApprovalRequest, granted_id)
        if (
            granted is not None
            and granted.status == "approved"
            and granted.ticket_id == ticket.id
            and granted.transition_id == transition.id
        ):
            if granted.executed_at is None:
                granted.executed_at = utcnow()
            return

    plan = resolve_plan(session, ticket, transition)
    if plan is None:
        return

    usable = usable_approval(session, ticket, transition.id)
    if usable is not None:
        usable.executed_at = utcnow()
        usable.execution_error = None
        session.flush()
        return

    request, created = ensure_request(
        session,
        ticket,
        transition,
        plan,
        requested_by_id=actor.user_id,
        reason=data.get("comment"),
    )
    error = approval_required_error(request, created=created)
    if created:
        if data.get(INLINE_FLAG) or session.in_nested_transaction():
            session.flush()
        else:
            session.commit()
    raise error


# --------------------------------------------------------------------------- decisiones


def _load_request(session: Session, request_id: int) -> ApprovalRequest | None:
    return session.scalar(
        select(ApprovalRequest)
        .options(
            selectinload(ApprovalRequest.steps).selectinload(ApprovalStep.decisions),
        )
        .where(ApprovalRequest.id == request_id)
        .execution_options(populate_existing=True)
    )


def can_decide(principal: Principal, request: ApprovalRequest) -> bool:
    if request.status != "pending":
        return False
    if principal.user_id == request.requested_by_id:
        return False  # segregación de funciones: nadie aprueba su propia solicitud
    step = current_step(request)
    if step is None:
        return False
    if principal.user_id in _pending_approver_ids(step):
        return True
    return principal.is_admin


def can_cancel(principal: Principal, request: ApprovalRequest) -> bool:
    if request.status != "pending":
        return False
    if principal.is_admin or principal.user_id == request.requested_by_id:
        return True
    return can_edit_ticket(principal, request.ticket)


def _close_remaining(request: ApprovalRequest, *, from_order: int) -> None:
    for step in request.steps:
        if step.order > from_order and step.status in ("waiting", "pending"):
            step.status = "cancelled"
        for decision in step.decisions:
            if decision.decision == "pending":
                decision.decision = "skipped"


def _executor_principal(session: Session, request: ApprovalRequest) -> Principal:
    user = session.get(User, request.requested_by_id) if request.requested_by_id else None
    if user is not None and user.active and user.keycloak_sub != SYSTEM_SUB:
        return build_principal(session, user, set(user.roles or []))
    return get_system_principal(session)


def execute_approved_transition(session: Session, request: ApprovalRequest) -> bool:
    """Ejecuta la transición aprobada. Devuelve True si se aplicó (errores quedan registrados)."""
    from app.services.tickets import transition_ticket

    ticket = session.get(Ticket, request.ticket_id)
    transition = request.transition
    if ticket is None or transition is None:
        request.execution_error = "El ticket o la transición ya no existen"
        return False
    to_state = session.get(WorkflowState, transition.to_state_id)
    if ticket.status_id != transition.from_state_id:
        request.execution_error = (
            "No se ejecutó: el ticket ya no está en el estado de origen de la transición"
        )
        return False
    principal = _executor_principal(session, request)
    try:
        with session.begin_nested():
            transition_ticket(
                session,
                principal,
                ticket,
                to_state.key if to_state else "",
                None,
                extra_guard_data={"approval_request_id": request.id, INLINE_FLAG: True},
            )
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict):
            detail = detail.get("message") or str(detail)
        request.execution_error = f"No se pudo aplicar la transición: {detail}"[:2000]
        request.executed_at = None
        logger.warning("Aprobación %s: transición no aplicada (%s)", request.id, detail)
        return False
    request.executed_at = request.executed_at or utcnow()
    request.execution_error = None
    return True


def decide(
    session: Session,
    principal: Principal,
    request_id: int,
    decision: str,
    comment: str | None = None,
) -> ApprovalRequest:
    """Registra la decisión de ``principal`` sobre la etapa actual. No hace commit."""
    if decision not in ("approved", "rejected"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Decisión inválida")
    base = session.get(ApprovalRequest, request_id)
    if base is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Solicitud de aprobación no encontrada")
    # Serializa con transiciones concurrentes del mismo ticket.
    session.scalar(select(Ticket.id).where(Ticket.id == base.ticket_id).with_for_update())
    request = _load_request(session, request_id)
    assert request is not None
    if request.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"La solicitud #{request.id} ya no está pendiente (estado: {request.status})",
        )
    if principal.user_id == request.requested_by_id:
        # Segregación de funciones: tampoco un administrador decide su propia solicitud.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="No puede decidir una solicitud de aprobación que usted mismo pidió",
        )
    step = current_step(request)
    if step is None:  # pragma: no cover - integridad
        raise HTTPException(status.HTTP_409_CONFLICT, detail="La solicitud no tiene etapas pendientes")

    now = utcnow()
    clean_comment = (comment or "").strip()[:4000] or None
    row = next(
        (d for d in step.decisions if d.approver_id == principal.user_id and d.decision == "pending"),
        None,
    )
    override = False
    if row is None:
        already = any(
            d.approver_id == principal.user_id and d.decision in ("approved", "rejected")
            for d in step.decisions
        )
        if already:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Ya registró su decisión en esta etapa")
        if not principal.is_admin:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="No es aprobador de la etapa actual de esta solicitud"
            )
        override = True
        row = ApprovalDecision(
            step_id=step.id,
            approver_id=principal.user_id,
            decision="pending",
            is_override=True,
            created_at=now,
        )
        session.add(row)
        step.decisions.append(row)
    if decision == "rejected" and not clean_comment:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Indique el motivo del rechazo"
        )
    row.decision = decision
    row.comment = clean_comment
    row.decided_at = now

    next_step: ApprovalStep | None = None
    if decision == "rejected":
        step.status = "rejected"
        step.decided_at = now
        request.status = "rejected"
        request.decided_at = now
        _close_remaining(request, from_order=step.order)
    else:
        step_done = override or step.rule == "any" or not _pending_approver_ids(step)
        if step_done:
            step.status = "approved"
            step.decided_at = now
            for other in step.decisions:
                if other.decision == "pending":
                    other.decision = "skipped"
            later = sorted(
                (s for s in request.steps if s.order > step.order and s.status == "waiting"),
                key=lambda s: (s.order, s.id),
            )
            if later:
                next_step = later[0]
            else:
                request.status = "approved"
                request.decided_at = now
    session.flush()

    transition = request.transition
    emit(
        session,
        EVENT_DECIDED,
        request.ticket,
        principal.user_id,
        {
            "approval_request_id": request.id,
            "step_id": step.id,
            "step_name": step.name,
            "decision": decision,
            "comment": clean_comment,
            "override": override,
            "request_status": request.status,
            "transition_id": transition.id,
            "transition_name": transition.name,
            "requested_by_id": request.requested_by_id,
        },
    )
    if next_step is not None:
        _activate_step(session, request, next_step, principal.user_id, created=False)
    if request.status == "approved":
        execute_approved_transition(session, request)
    session.flush()
    return request


def cancel_request(
    session: Session, principal: Principal | None, request: ApprovalRequest, reason: str | None
) -> ApprovalRequest:
    """Cancela una solicitud pendiente (``principal=None`` = cancelación del sistema).

    Toma el mismo bloqueo que ``decide`` (fila del ticket) y recarga la solicitud antes de
    validar, para no pisar una aprobación concurrente que ya ejecutó la transición.
    """
    session.scalar(select(Ticket.id).where(Ticket.id == request.ticket_id).with_for_update())
    reloaded = _load_request(session, request.id)
    if reloaded is None:  # pragma: no cover - borrada en paralelo
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Solicitud de aprobación no encontrada")
    request = reloaded
    if request.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"La solicitud #{request.id} ya no está pendiente"
        )
    if principal is not None and not can_cancel(principal, request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No puede cancelar esta solicitud")
    now = utcnow()
    request.status = "cancelled"
    request.decided_at = now
    for step in request.steps:
        if step.status in ("waiting", "pending"):
            step.status = "cancelled"
        for decision in step.decisions:
            if decision.decision == "pending":
                decision.decision = "skipped"
    session.flush()
    emit(
        session,
        EVENT_CANCELLED,
        request.ticket,
        principal.user_id if principal else None,
        {
            "approval_request_id": request.id,
            "transition_id": request.transition_id,
            "reason": (reason or "").strip()[:2000] or None,
        },
    )
    return request


def cancel_stale_requests(session: Session, ticket: Ticket, actor_id: int | None) -> int:
    """Cancela las solicitudes pendientes cuyo estado de origen ya no es el del ticket."""
    pending = session.scalars(
        select(ApprovalRequest)
        .join(WorkflowTransition, WorkflowTransition.id == ApprovalRequest.transition_id)
        .where(
            ApprovalRequest.ticket_id == ticket.id,
            ApprovalRequest.status == "pending",
            WorkflowTransition.from_state_id != ticket.status_id,
        )
    ).all()
    for request in pending:
        cancel_request(
            session,
            None,
            request,
            "Cancelada automáticamente: el ticket cambió de estado",
        )
    return len(pending)


# --------------------------------------------------------------------------- visibilidad


def _approver_involvement(principal: Principal):
    """Decisiones que dan acceso al ticket: la etapa activa que espera a ``principal`` o una
    decisión que tomó de verdad (aprobó/rechazó). Etapas en espera, omitidas o canceladas no
    dan acceso."""
    return and_(
        ApprovalDecision.approver_id == principal.user_id,
        or_(
            and_(
                ApprovalDecision.decision == "pending",
                ApprovalStep.status == "pending",
                ApprovalRequest.status == "pending",
            ),
            ApprovalDecision.decision.in_(("approved", "rejected")),
        ),
    )


def approver_visibility_clause(principal: Principal):
    """Tickets que ``principal`` ve como aprobador (etapa activa o decisión tomada)."""
    return Ticket.id.in_(
        select(ApprovalRequest.ticket_id)
        .join(ApprovalStep, ApprovalStep.request_id == ApprovalRequest.id)
        .join(ApprovalDecision, ApprovalDecision.step_id == ApprovalStep.id)
        .where(_approver_involvement(principal))
    )


def pending_for_principal_clause(principal: Principal):
    """Solicitudes cuya etapa actual espera la decisión de ``principal``."""
    return ApprovalRequest.id.in_(
        select(ApprovalStep.request_id)
        .join(ApprovalDecision, ApprovalDecision.step_id == ApprovalStep.id)
        .where(
            ApprovalStep.status == "pending",
            ApprovalDecision.approver_id == principal.user_id,
            ApprovalDecision.decision == "pending",
        )
    )


def involved_clause(principal: Principal):
    """Solicitudes en las que participa ``principal`` (solicitante, etapa activa o decisión)."""
    return or_(
        ApprovalRequest.requested_by_id == principal.user_id,
        ApprovalRequest.id.in_(
            select(ApprovalStep.request_id)
            .join(ApprovalRequest, ApprovalRequest.id == ApprovalStep.request_id)
            .join(ApprovalDecision, ApprovalDecision.step_id == ApprovalStep.id)
            .where(_approver_involvement(principal))
        ),
    )


# --------------------------------------------------------------------------- serialización


def _user_ref(user: User | None) -> dict[str, Any] | None:
    return {"id": user.id, "display_name": user.display_name} if user else None


def _approver_label(session: Session, step: ApprovalStep, cache: dict[tuple[str, int], str]) -> str:
    def name_of(model, key: int | None) -> str | None:
        if key is None:
            return None
        cache_key = (model.__tablename__, key)
        if cache_key not in cache:
            obj = session.get(model, key)
            cache[cache_key] = (
                getattr(obj, "display_name", None) or getattr(obj, "name", None) or f"#{key}"
                if obj
                else f"#{key}"
            )
        return cache[cache_key]

    if step.approver_type == "user":
        return f"Usuario: {name_of(User, step.approver_user_id) or 'no disponible'}"
    if step.approver_type == "role":
        role_names = {"approver": "Aprobadores", "agent": "Agentes", "admin": "Administradores"}
        label = role_names.get(step.approver_role or "", step.approver_role or "Rol")
        department = name_of(Department, step.approver_department_id)
        return f"{label} de {department}" if department else label
    if step.approver_type == "team":
        return f"Equipo: {name_of(Team, step.approver_team_id) or 'no disponible'}"
    team = name_of(Team, step.approver_team_id)
    return f"Líder de {team}" if team else "Líder del equipo"


def serialize_requests(
    session: Session, requests: Sequence[ApprovalRequest], principal: Principal
) -> list[dict[str, Any]]:
    label_cache: dict[tuple[str, int], str] = {}
    state_cache: dict[int, WorkflowState | None] = {}

    def state(state_id: int) -> WorkflowState | None:
        if state_id not in state_cache:
            state_cache[state_id] = session.get(WorkflowState, state_id)
        return state_cache[state_id]

    policy_cache: dict[int, dict[str, Any] | None] = {}

    def policy_ref(policy_id: int | None) -> dict[str, Any] | None:
        if policy_id is None:
            return None
        if policy_id not in policy_cache:
            policy = session.get(ApprovalPolicy, policy_id)
            policy_cache[policy_id] = {"id": policy.id, "name": policy.name} if policy else None
        return policy_cache[policy_id]

    result: list[dict[str, Any]] = []
    for request in requests:
        ticket = request.ticket
        transition = request.transition
        from_state = state(transition.from_state_id)
        to_state = state(transition.to_state_id)
        step = current_step(request)
        result.append(
            {
                "id": request.id,
                "status": request.status,
                "ticket": {
                    "id": ticket.id,
                    "key": ticket.key,
                    "title": ticket.title,
                    "status": {
                        "key": ticket.status.key,
                        "name": ticket.status.name,
                        "category": ticket.status.category,
                    },
                    "priority": {
                        "key": ticket.priority.key,
                        "name": ticket.priority.name,
                        "rank": ticket.priority.rank,
                    },
                    "type": {"key": ticket.type.key, "name": ticket.type.name},
                    "department": {"id": ticket.department.id, "name": ticket.department.name},
                },
                "transition": {
                    "id": transition.id,
                    "name": transition.name,
                    "from_state": {"key": from_state.key, "name": from_state.name} if from_state else None,
                    "to_state": {"key": to_state.key, "name": to_state.name} if to_state else None,
                },
                "policy": policy_ref(request.policy_id),
                "reason": request.reason,
                "requested_by": _user_ref(request.requested_by),
                "created_at": request.created_at,
                "decided_at": request.decided_at,
                "executed_at": request.executed_at,
                "execution_error": request.execution_error,
                "current_step": step.order if step else None,
                "steps": [
                    {
                        "id": s.id,
                        "order": s.order,
                        "name": s.name,
                        "approver_type": s.approver_type,
                        "approver_label": _approver_label(session, s, label_cache),
                        "rule": s.rule,
                        "status": s.status,
                        "decided_at": s.decided_at,
                        "decisions": [
                            {
                                "id": d.id,
                                "approver": _user_ref(d.approver),
                                "decision": d.decision,
                                "comment": d.comment,
                                "is_override": d.is_override,
                                "decided_at": d.decided_at,
                            }
                            for d in s.decisions
                        ],
                    }
                    for s in sorted(request.steps, key=lambda s: (s.order, s.id))
                ],
                "can_decide": can_decide(principal, request),
                "can_cancel": can_cancel(principal, request),
            }
        )
    return result


REQUEST_LOAD_OPTIONS = (
    selectinload(ApprovalRequest.steps).selectinload(ApprovalStep.decisions).selectinload(
        ApprovalDecision.approver
    ),
    selectinload(ApprovalRequest.ticket).selectinload(Ticket.status),
    selectinload(ApprovalRequest.ticket).selectinload(Ticket.priority),
    selectinload(ApprovalRequest.ticket).selectinload(Ticket.type),
    selectinload(ApprovalRequest.ticket).selectinload(Ticket.department),
    selectinload(ApprovalRequest.transition),
    selectinload(ApprovalRequest.requested_by),
)


def load_requests(session: Session, *conditions) -> list[ApprovalRequest]:
    stmt = (
        select(ApprovalRequest)
        .options(*REQUEST_LOAD_OPTIONS)
        .order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc())
    )
    if conditions:
        stmt = stmt.where(and_(*conditions))
    return list(session.scalars(stmt).unique().all())
