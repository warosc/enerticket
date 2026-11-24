"""Aprobaciones: bandeja del aprobador, decisiones, cancelación y políticas (admin)."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db, utcnow
from app.core.permissions import can_view_ticket, visible_tickets_filter
from app.core.security import Principal, get_principal, require_roles
from app.models.approvals import ApprovalPolicy, ApprovalRequest
from app.models.core import (
    Department,
    Team,
    Ticket,
    TicketType,
    User,
    WorkflowState,
    WorkflowTransition,
)
from app.services import tickets as ticket_svc
from app.services.approvals import service
from app.services.approvals.schemas import (
    ApprovalPage,
    ApprovalRequestOut,
    CancelIn,
    DecisionIn,
    PolicyIn,
    PolicyOut,
    PolicyPatch,
    StepSpec,
)

router = APIRouter(tags=["aprobaciones"])

# Roles que permiten figurar como aprobador (y ver el ticket por la extensión de visibilidad).
APPROVER_CAPABLE_ROLES = frozenset({"approver", "agent", "admin"})


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Solicitud de aprobación no encontrada")


def _serialize_one(session: Session, request_id: int, principal: Principal) -> dict:
    requests = service.load_requests(session, ApprovalRequest.id == request_id)
    if not requests:
        raise _not_found()
    return service.serialize_requests(session, requests, principal)[0]


def _get_visible_request(session: Session, principal: Principal, request_id: int) -> ApprovalRequest:
    request = session.get(ApprovalRequest, request_id)
    if request is None or not can_view_ticket(session, principal, request.ticket):
        raise _not_found()
    return request


# --------------------------------------------------------------------------- bandeja


@router.get("/approvals/pending", response_model=list[ApprovalRequestOut])
def my_pending_approvals(
    all_pending: bool = Query(
        default=False, alias="all", description="Solo admin: todas las solicitudes pendientes"
    ),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[dict]:
    """Solicitudes cuya etapa actual espera la decisión del usuario."""
    if all_pending and principal.is_admin:
        conditions = [ApprovalRequest.status == "pending"]
    else:
        conditions = [
            ApprovalRequest.status == "pending",
            service.pending_for_principal_clause(principal),
        ]
    requests = service.load_requests(session, *conditions)
    return service.serialize_requests(session, requests, principal)


@router.get("/approvals", response_model=ApprovalPage)
def list_approvals(
    scope: Literal["mine", "all"] = "mine",
    status_filter: Optional[Literal["pending", "approved", "rejected", "cancelled"]] = Query(
        default=None, alias="status"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    """Historial: ``mine`` = solicitudes en las que participo; ``all`` = todas (admin)."""
    conditions = []
    if scope == "all":
        if not principal.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Solo administradores")
    else:
        conditions.append(service.involved_clause(principal))
        # Nunca listar solicitudes de tickets que el usuario ya no puede ver.
        if not principal.is_admin:
            conditions.append(
                ApprovalRequest.ticket_id.in_(
                    select(Ticket.id).where(visible_tickets_filter(principal))
                )
            )
    if status_filter:
        conditions.append(ApprovalRequest.status == status_filter)
    count_stmt = select(func.count()).select_from(ApprovalRequest)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = session.scalar(count_stmt) or 0
    id_stmt = select(ApprovalRequest.id).order_by(
        ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc()
    )
    if conditions:
        id_stmt = id_stmt.where(*conditions)
    ids = list(session.scalars(id_stmt.offset((page - 1) * page_size).limit(page_size)).all())
    requests = service.load_requests(session, ApprovalRequest.id.in_(ids)) if ids else []
    return {
        "items": service.serialize_requests(session, requests, principal),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/tickets/{ticket_id}/approvals", response_model=list[ApprovalRequestOut])
def ticket_approvals(
    ticket_id: int,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> list[dict]:
    ticket = ticket_svc.get_visible_ticket(session, principal, ticket_id)
    requests = service.load_requests(session, ApprovalRequest.ticket_id == ticket.id)
    return service.serialize_requests(session, requests, principal)


# --------------------------------------------------------------------------- políticas (admin)
# Declaradas antes de /approvals/{request_id} para que "policies" no se interprete como id.


def _transition_ref(session: Session, transition: WorkflowTransition) -> dict:
    from_state = session.get(WorkflowState, transition.from_state_id)
    to_state = session.get(WorkflowState, transition.to_state_id)
    return {
        "id": transition.id,
        "name": transition.name,
        "from_key": from_state.key if from_state else None,
        "to_key": to_state.key if to_state else None,
        "from_name": from_state.name if from_state else None,
        "to_name": to_state.name if to_state else None,
        "requires_approval": transition.requires_approval,
    }


def _policy_out(session: Session, policy: ApprovalPolicy) -> dict:
    return {
        "id": policy.id,
        "name": policy.name,
        "active": policy.active,
        "transition_id": policy.transition_id,
        "transition": _transition_ref(session, policy.transition),
        "ticket_type_keys": list(policy.ticket_type_keys or []),
        "department_ids": list(policy.department_ids or []),
        "steps": list(policy.steps or []),
        "order": policy.order,
        "created_at": policy.created_at,
        "updated_at": policy.updated_at,
    }


def _validate_policy_refs(
    session: Session,
    transition_id: int | None,
    type_keys: list[str] | None,
    department_ids: list[int] | None,
    steps: list[StepSpec] | None,
) -> WorkflowTransition | None:
    errors: list[str] = []
    transition = None
    if transition_id is not None:
        transition = session.get(WorkflowTransition, transition_id)
        if transition is None:
            errors.append("La transición no existe")
    if type_keys:
        known = set(session.scalars(select(TicketType.key)).all())
        unknown = sorted(set(type_keys) - known)
        if unknown:
            errors.append(f"Tipos de ticket desconocidos: {', '.join(unknown)}")
    if department_ids:
        known_ids = set(session.scalars(select(Department.id)).all())
        if set(department_ids) - known_ids:
            errors.append("Algún departamento no existe")
    for index, step in enumerate(steps or [], start=1):
        if step.user_id is not None:
            user = session.get(User, step.user_id)
            if user is None or not user.active:
                errors.append(f"Etapa {index}: el usuario no existe o está inactivo")
            elif not APPROVER_CAPABLE_ROLES.intersection(user.roles or []):
                errors.append(
                    f"Etapa {index}: el usuario debe tener rol de aprobador, agente o administrador"
                )
        if step.team_id is not None and session.get(Team, step.team_id) is None:
            errors.append(f"Etapa {index}: el equipo no existe")
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="; ".join(errors))
    return transition


@router.get("/approvals/policies", response_model=list[PolicyOut])
def list_policies(
    _: Principal = Depends(require_roles("admin")), session: Session = Depends(get_db)
) -> list[dict]:
    policies = session.scalars(
        select(ApprovalPolicy).order_by(
            ApprovalPolicy.transition_id, ApprovalPolicy.order, ApprovalPolicy.id
        )
    ).all()
    return [_policy_out(session, p) for p in policies]


@router.post("/approvals/policies", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
def create_policy(
    payload: PolicyIn,
    _: Principal = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
) -> dict:
    transition = _validate_policy_refs(
        session, payload.transition_id, payload.ticket_type_keys, payload.department_ids, payload.steps
    )
    assert transition is not None
    now = utcnow()
    policy = ApprovalPolicy(
        name=payload.name.strip(),
        active=payload.active,
        transition_id=transition.id,
        ticket_type_keys=sorted(set(payload.ticket_type_keys)),
        department_ids=sorted(set(payload.department_ids)),
        steps=[s.model_dump(exclude_none=True) for s in payload.steps],
        order=payload.order,
        created_at=now,
        updated_at=now,
    )
    session.add(policy)
    if payload.active:
        # Una política activa solo tiene efecto si la transición exige aprobación.
        transition.requires_approval = True
    session.commit()
    return _policy_out(session, policy)


@router.patch("/approvals/policies/{policy_id}", response_model=PolicyOut)
def update_policy(
    policy_id: int,
    payload: PolicyPatch,
    _: Principal = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
) -> dict:
    policy = session.get(ApprovalPolicy, policy_id)
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Política no encontrada")
    changes = payload.model_dump(exclude_unset=True)
    transition = _validate_policy_refs(
        session,
        changes.get("transition_id"),
        changes.get("ticket_type_keys"),
        changes.get("department_ids"),
        payload.steps,
    )
    if "name" in changes and changes["name"] is not None:
        policy.name = changes["name"].strip()
    if changes.get("active") is not None:
        policy.active = changes["active"]
    if transition is not None:
        policy.transition_id = transition.id
    if changes.get("ticket_type_keys") is not None:
        policy.ticket_type_keys = sorted(set(changes["ticket_type_keys"]))
    if changes.get("department_ids") is not None:
        policy.department_ids = sorted(set(changes["department_ids"]))
    if payload.steps is not None:
        policy.steps = [s.model_dump(exclude_none=True) for s in payload.steps]
    if changes.get("order") is not None:
        policy.order = changes["order"]
    policy.updated_at = utcnow()
    if policy.active:
        target = session.get(WorkflowTransition, policy.transition_id)
        if target is not None:
            target.requires_approval = True
    session.commit()
    session.refresh(policy)
    return _policy_out(session, policy)


@router.delete("/approvals/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_policy(
    policy_id: int,
    _: Principal = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
) -> Response:
    policy = session.get(ApprovalPolicy, policy_id)
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Política no encontrada")
    session.delete(policy)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class TransitionApprovalIn(BaseModel):
    requires_approval: bool


@router.get("/approvals/transitions")
def list_transitions(
    _: Principal = Depends(require_roles("admin")), session: Session = Depends(get_db)
) -> list[dict]:
    """Transiciones del flujo con su indicador ``requires_approval`` y nº de políticas."""
    transitions = session.scalars(select(WorkflowTransition).order_by(WorkflowTransition.id)).all()
    counts = dict(
        session.execute(
            select(ApprovalPolicy.transition_id, func.count(ApprovalPolicy.id))
            .where(ApprovalPolicy.active.is_(True))
            .group_by(ApprovalPolicy.transition_id)
        ).all()
    )
    return [
        {**_transition_ref(session, t), "active_policies": int(counts.get(t.id, 0))}
        for t in transitions
    ]


@router.put("/approvals/transitions/{transition_id}")
def set_transition_approval(
    transition_id: int,
    payload: TransitionApprovalIn,
    _: Principal = Depends(require_roles("admin")),
    session: Session = Depends(get_db),
) -> dict:
    """Activa/desactiva ``requires_approval`` de una transición."""
    transition = session.get(WorkflowTransition, transition_id)
    if transition is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Transición no encontrada")
    transition.requires_approval = payload.requires_approval
    session.commit()
    return _transition_ref(session, transition)


# --------------------------------------------------------------------------- solicitud


@router.get("/approvals/{request_id}", response_model=ApprovalRequestOut)
def get_approval(
    request_id: int,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    request = _get_visible_request(session, principal, request_id)
    return _serialize_one(session, request.id, principal)


@router.post("/approvals/{request_id}/decide", response_model=ApprovalRequestOut)
def decide_approval(
    request_id: int,
    payload: DecisionIn,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    request = _get_visible_request(session, principal, request_id)
    service.decide(session, principal, request.id, payload.normalized, payload.comment)
    session.commit()
    return _serialize_one(session, request_id, principal)


@router.post("/approvals/{request_id}/cancel", response_model=ApprovalRequestOut)
def cancel_approval(
    request_id: int,
    payload: Optional[CancelIn] = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
) -> dict:
    request = _get_visible_request(session, principal, request_id)
    service.cancel_request(session, principal, request, payload.reason if payload else None)
    session.commit()
    return _serialize_one(session, request_id, principal)

