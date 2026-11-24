"""Lógica de negocio de tickets: creación, edición, transiciones, comentarios y serialización.

Otros módulos pueden reutilizar estas funciones (p.ej. flows al aprobar una transición).
Las funciones no hacen commit: el llamador decide cuándo confirmar la transacción.

Catálogo de servicio (Fase 3):
- Clasificación: ``category_id`` (activa, del departamento del ticket y que admita su tipo),
  ``subcategory_id`` (de esa categoría), ``provider_id`` (activo y aplicable al departamento)
  y ``channel``. Canal y proveedor solo los fija un agente/admin.
- Al crear se copian ``requester_department_id`` y ``vip`` del solicitante.
- Resolver (entrar en un estado ``done``) exige solución (``resolution_note``) y, si el
  departamento tiene categorías activas y quien resuelve atiende el ticket, la categoría.
  Se guardan ``resolved_by_id`` y ``business_resolution_minutes``; reabrir los limpia.
- Orden de trabajo (``create_work_order``): ticket ``work_order`` hijo del original.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, joinedload

from app.core.accounts import is_stale
from app.core.config import settings
from app.core.db import utcnow
from app.core.events import collect_enrichments, emit, run_guards
from app.core.permissions import (
    can_edit_ticket,
    can_requester_transition,
    can_view_internal_comments,
    can_view_ticket,
)
from app.core.security import ROLE_ADMIN, ROLE_AGENT, Principal
from app.models.catalog import Provider, TicketCategory, TicketSubcategory
from app.models.core import (
    TICKET_CHANNELS,
    Department,
    Priority,
    Project,
    Team,
    Ticket,
    TicketComment,
    TicketHistory,
    TicketType,
    User,
    WorkflowDefinition,
    WorkflowState,
    WorkflowTransition,
)

TICKET_LOAD_OPTIONS = (
    joinedload(Ticket.status),
    joinedload(Ticket.priority),
    joinedload(Ticket.type),
    joinedload(Ticket.department),
    joinedload(Ticket.team),
    joinedload(Ticket.project),
    joinedload(Ticket.requester),
    joinedload(Ticket.assignee),
    joinedload(Ticket.category),
    joinedload(Ticket.subcategory),
    joinedload(Ticket.provider),
    joinedload(Ticket.resolved_by),
    joinedload(Ticket.requester_department).joinedload(Department.gerencia),
)

CHANNEL_LABELS = {"portal": "Portal", "email": "Correo", "phone": "Teléfono", "other": "Otro"}
DEFAULT_CHANNEL = "portal"
WORK_ORDER_TYPE = "work_order"
DONE_CATEGORY = "done"
MSG_RESOLUTION_REQUIRED = "Indique la solución antes de resolver"
MSG_CATEGORY_REQUIRED = "Clasifique el ticket (categoría) antes de resolverlo"

EVENT_CREATED = "ticket.created"
EVENT_UPDATED = "ticket.updated"
EVENT_ASSIGNED = "ticket.assigned"
EVENT_TRANSITIONED = "ticket.transitioned"
EVENT_COMMENTED = "ticket.commented"
GUARD_TRANSITION = "ticket.transition"


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Ticket no encontrado")


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


# --------------------------------------------------------------------------- serialización


def _user_ref(user: User | None) -> dict[str, Any] | None:
    return {"id": user.id, "display_name": user.display_name} if user else None


def _id_name(obj: Any) -> dict[str, Any] | None:
    return {"id": obj.id, "name": obj.name} if obj is not None else None


def serialize_ticket(ticket: Ticket) -> dict[str, Any]:
    """Serializa a ``TicketOut`` (sin enriquecedores)."""
    return {
        "id": ticket.id,
        "key": ticket.key,
        "title": ticket.title,
        "description": ticket.description or "",
        "status": {
            "id": ticket.status.id,
            "key": ticket.status.key,
            "name": ticket.status.name,
            "category": ticket.status.category,
        },
        "priority": {
            "id": ticket.priority.id,
            "key": ticket.priority.key,
            "name": ticket.priority.name,
            "rank": ticket.priority.rank,
        },
        "type": {"id": ticket.type.id, "key": ticket.type.key, "name": ticket.type.name},
        "department": {"id": ticket.department.id, "name": ticket.department.name},
        "team": {"id": ticket.team.id, "name": ticket.team.name} if ticket.team else None,
        "project": (
            {"id": ticket.project.id, "code": ticket.project.code, "name": ticket.project.name}
            if ticket.project
            else None
        ),
        "requester": _user_ref(ticket.requester),
        "assignee": _user_ref(ticket.assignee),
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
        "first_response_at": ticket.first_response_at,
        "resolved_at": ticket.resolved_at,
        "closed_at": ticket.closed_at,
        "category": _id_name(ticket.category),
        "subcategory": _id_name(ticket.subcategory),
        "provider": _id_name(ticket.provider),
        "channel": ticket.channel or DEFAULT_CHANNEL,
        "vip": bool(ticket.vip),
        "resolution_note": ticket.resolution_note,
        "resolved_by": _user_ref(ticket.resolved_by),
        "requester_department": _id_name(ticket.requester_department),
        "gerencia": (
            _id_name(ticket.requester_department.gerencia) if ticket.requester_department else None
        ),
        "parent_ticket_id": ticket.parent_ticket_id,
        "child_ticket_ids": [],
        "business_resolution_minutes": ticket.business_resolution_minutes,
    }


def _children_map(session: Session, ticket_ids: list[int]) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {}
    if not ticket_ids:
        return result
    rows = session.execute(
        select(Ticket.parent_ticket_id, Ticket.id)
        .where(Ticket.parent_ticket_id.in_(ticket_ids))
        .order_by(Ticket.id)
    ).all()
    for parent_id, child_id in rows:
        result.setdefault(parent_id, []).append(child_id)
    return result


def serialize_tickets(session: Session, tickets: Sequence[Ticket]) -> list[dict[str, Any]]:
    """Serializa tickets aplicando los enriquecedores registrados (``register_ticket_enricher``)."""
    extras = collect_enrichments(session, tickets)
    children = _children_map(session, [t.id for t in tickets])
    result = []
    for ticket in tickets:
        item = serialize_ticket(ticket)
        item["child_ticket_ids"] = children.get(ticket.id, [])
        for field_name, value in extras.get(ticket.id, {}).items():
            item.setdefault(field_name, value)
        result.append(item)
    return result


def load_ticket(session: Session, ticket_id: int) -> Ticket | None:
    """Carga un ticket con sus relaciones (refresca valores de la BD)."""
    stmt = (
        select(Ticket)
        .options(*TICKET_LOAD_OPTIONS)
        .where(Ticket.id == ticket_id)
        .execution_options(populate_existing=True)
    )
    return session.scalars(stmt).unique().first()


def ticket_out(session: Session, ticket_id: int) -> dict[str, Any]:
    ticket = load_ticket(session, ticket_id)
    if ticket is None:
        raise _not_found()
    return serialize_tickets(session, [ticket])[0]


def snapshot(ticket: Ticket) -> dict[str, Any]:
    """Estado editable del ticket para ``ticket_history`` (before/after)."""
    return {
        "title": ticket.title,
        "description": ticket.description,
        "priority": ticket.priority.key if ticket.priority else None,
        "status": ticket.status.key if ticket.status else None,
        "type": ticket.type.key if ticket.type else None,
        "department_id": ticket.department_id,
        "team_id": ticket.team_id,
        "assignee_id": ticket.assignee_id,
        "project_id": ticket.project_id,
        "category_id": ticket.category_id,
        "subcategory_id": ticket.subcategory_id,
        "provider_id": ticket.provider_id,
        "channel": ticket.channel,
        "resolution_note": ticket.resolution_note,
    }


# --------------------------------------------------------------------------- acceso


def get_visible_ticket(
    session: Session, principal: Principal, ticket_id: int, *, for_update: bool = False
) -> Ticket:
    """Devuelve el ticket si existe y es visible; si no, 404 (no revela su existencia)."""
    stmt: Select[tuple[Ticket]] = select(Ticket).where(Ticket.id == ticket_id)
    if for_update:
        stmt = stmt.with_for_update()
    ticket = session.scalars(stmt).first()
    if ticket is None or not can_view_ticket(session, principal, ticket):
        raise _not_found()
    return ticket


def get_default_workflow(session: Session) -> WorkflowDefinition:
    workflow = session.scalars(
        select(WorkflowDefinition).order_by(
            WorkflowDefinition.is_default.desc(), WorkflowDefinition.id
        )
    ).first()
    if workflow is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No hay flujo de trabajo configurado (ejecute los seeds)",
        )
    return workflow


def get_initial_state(session: Session, workflow: WorkflowDefinition) -> WorkflowState:
    state = session.scalars(
        select(WorkflowState)
        .where(WorkflowState.workflow_id == workflow.id)
        .order_by(WorkflowState.is_initial.desc(), WorkflowState.order, WorkflowState.id)
    ).first()
    if state is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="El flujo de trabajo no tiene estados"
        )
    return state


def default_team_for_department(session: Session, department_id: int) -> Team | None:
    return session.scalars(
        select(Team).where(Team.department_id == department_id).order_by(Team.id)
    ).first()


def _priority_by_key(session: Session, key: str) -> Priority:
    priority = session.scalar(select(Priority).where(Priority.key == key))
    if priority is None:
        raise _unprocessable(f"Prioridad desconocida: {key}")
    return priority


def _validate_assignee(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None or not user.active:
        raise _unprocessable("El usuario asignado no existe o está inactivo")
    if is_stale(user):
        raise _unprocessable(
            f"{user.display_name} no ha iniciado sesión en los últimos {settings.account_stale_days} "
            "días; sus permisos no están verificados. Elija otro agente."
        )
    roles = set(user.roles or [])
    if not roles.intersection({ROLE_AGENT, ROLE_ADMIN}):
        raise _unprocessable("El usuario asignado debe tener rol de agente")
    return user


def _validate_service_department(session: Session, department_id: int) -> Department:
    department = session.get(Department, department_id)
    if department is None:
        raise _unprocessable("El departamento no existe")
    if not department.active or not department.serves_tickets:
        raise _unprocessable(f"El departamento «{department.name}» no atiende tickets")
    return department


def validate_classification(
    session: Session,
    *,
    department_id: int,
    type_key: str | None,
    category_id: int | None,
    subcategory_id: int | None,
) -> tuple[TicketCategory | None, TicketSubcategory | None]:
    """Valida categoría/subcategoría para el departamento y tipo del ticket (422 si no).

    Una subcategoría sin categoría toma la categoría a la que pertenece.
    """
    subcategory = None
    if subcategory_id is not None:
        subcategory = session.get(TicketSubcategory, subcategory_id)
        if subcategory is None or not subcategory.active:
            raise _unprocessable("La subcategoría no existe o está inactiva")
        if category_id is None:
            category_id = subcategory.category_id
        elif subcategory.category_id != category_id:
            raise _unprocessable("La subcategoría no pertenece a la categoría indicada")
    category = None
    if category_id is not None:
        category = session.get(TicketCategory, category_id)
        if category is None or not category.active:
            raise _unprocessable("La categoría no existe o está inactiva")
        if category.department_id != department_id:
            raise _unprocessable("La categoría no pertenece al departamento del ticket")
        if not category.admits_type(type_key):
            type_name = session.scalar(select(TicketType.name).where(TicketType.key == type_key))
            raise _unprocessable(
                f"La categoría «{category.name}» no admite tickets de tipo «{type_name or type_key}»"
            )
    return category, subcategory


def validate_provider(session: Session, provider_id: int, department_id: int) -> Provider:
    provider = session.get(Provider, provider_id)
    if provider is None or not provider.active:
        raise _unprocessable("El proveedor no existe o está inactivo")
    if not provider.applies_to(department_id):
        raise _unprocessable("El proveedor no atiende el departamento del ticket")
    return provider


def _validate_channel(channel: str) -> str:
    if channel not in TICKET_CHANNELS:
        raise _unprocessable(f"Canal inválido: {channel}")
    return channel


def department_has_categories(
    session: Session, department_id: int, type_key: str | None = None
) -> bool:
    """¿El departamento tiene categorías activas que admitan ``type_key``?

    Con ``type_key`` solo cuentan las categorías sin restricción de tipo (``type_keys`` vacía)
    o que incluyan ese tipo: si ninguna lo admite el ticket no se puede clasificar y, por lo
    tanto, resolverlo no puede exigir categoría.
    """
    categories = session.scalars(
        select(TicketCategory).where(
            TicketCategory.department_id == department_id, TicketCategory.active.is_(True)
        )
    ).all()
    if type_key is None:
        return bool(categories)
    return any(category.admits_type(type_key) for category in categories)


def _validate_team(session: Session, team_id: int, department_id: int) -> Team:
    team = session.get(Team, team_id)
    if team is None:
        raise _unprocessable("El equipo no existe")
    if team.department_id != department_id:
        raise _unprocessable("El equipo no pertenece al departamento del ticket")
    return team


# --------------------------------------------------------------------------- operaciones


def _check_requester_limits(session: Session, principal: Principal, priority: Priority) -> None:
    """Límites para quien no es agente: prioridad máxima y tickets por hora (429)."""
    if principal.is_agent:
        return
    max_key = (settings.requester_max_priority or "").strip()
    if max_key:
        max_priority = session.scalar(select(Priority).where(Priority.key == max_key))
        if max_priority is not None and priority.rank > max_priority.rank:
            raise _unprocessable(
                f"Solo los agentes pueden registrar tickets con prioridad «{priority.name}». "
                f"Elija «{max_priority.name}» o inferior; el equipo la escalará si corresponde."
            )
    limit = settings.tickets_per_user_hour
    if limit:
        since = utcnow() - timedelta(hours=1)
        recent = session.scalar(
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.requester_id == principal.user_id, Ticket.created_at >= since)
        )
        if (recent or 0) >= limit:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Alcanzó el máximo de {limit} tickets por hora. Intente más tarde.",
                headers={"Retry-After": "600"},
            )


def create_ticket(
    session: Session,
    principal: Principal,
    *,
    title: str,
    description: str = "",
    type_key: str = "incident",
    priority_key: str = "medium",
    department_id: int,
    project_id: int | None = None,
    category_id: int | None = None,
    subcategory_id: int | None = None,
    provider_id: int | None = None,
    channel: str | None = None,
    enforce_limits: bool = True,
    requester_id: int | None = None,
    parent_ticket_id: int | None = None,
) -> Ticket:
    """Crea un ticket en el estado inicial del flujo por defecto y emite ``ticket.created``.

    Quien no es agente está limitado por ``REQUESTER_MAX_PRIORITY`` (422) y
    ``TICKETS_PER_USER_HOUR`` (429); ``enforce_limits=False`` solo para seeds/procesos internos
    (también admite tipos desactivados, p.ej. datos de demostración históricos).
    ``requester_id`` (por defecto quien crea) y ``parent_ticket_id`` los usa la orden de trabajo.
    """
    ticket_type = session.scalar(select(TicketType).where(TicketType.key == type_key))
    if ticket_type is None:
        raise _unprocessable(f"Tipo de ticket desconocido: {type_key}")
    if enforce_limits and not ticket_type.active:
        raise _unprocessable(f"El tipo de ticket «{ticket_type.name}» no está disponible")
    priority = _priority_by_key(session, priority_key)
    if enforce_limits:
        _check_requester_limits(session, principal, priority)
    department = _validate_service_department(session, department_id)
    requester = session.get(User, requester_id if requester_id is not None else principal.user_id)
    if requester is None:  # pragma: no cover - el principal siempre existe
        raise _unprocessable("El solicitante no existe")
    if not principal.is_agent:
        # Canal y proveedor los registra la mesa de ayuda; el portal es el canal del solicitante.
        channel = DEFAULT_CHANNEL
        provider_id = None
    channel = _validate_channel(channel or DEFAULT_CHANNEL)
    category, subcategory = validate_classification(
        session,
        department_id=department.id,
        type_key=ticket_type.key,
        category_id=category_id,
        subcategory_id=subcategory_id,
    )
    provider = validate_provider(session, provider_id, department.id) if provider_id is not None else None
    project = None
    if project_id is not None:
        project = session.get(Project, project_id)
        if project is None:
            raise _unprocessable("El proyecto no existe")
    workflow = get_default_workflow(session)
    state = get_initial_state(session, workflow)
    team = default_team_for_department(session, department.id)

    now = utcnow()
    ticket = Ticket(
        title=title,
        description=description or "",
        type_id=ticket_type.id,
        priority_id=priority.id,
        status_id=state.id,
        department_id=department.id,
        team_id=team.id if team else None,
        project_id=project.id if project else None,
        requester_id=requester.id,
        category_id=category.id if category else None,
        subcategory_id=subcategory.id if subcategory else None,
        provider_id=provider.id if provider else None,
        channel=channel,
        vip=bool(requester.vip),
        requester_department_id=requester.department_id,
        parent_ticket_id=parent_ticket_id,
        created_at=now,
        updated_at=now,
    )
    session.add(ticket)
    session.flush()
    ticket.public_key = f"ESD-{ticket.id}"
    session.flush()
    session.expire(ticket)
    emit(session, EVENT_CREATED, ticket, principal.user_id, {"after": snapshot(ticket)})
    return ticket


def update_ticket(
    session: Session, principal: Principal, ticket: Ticket, changes: dict[str, Any]
) -> Ticket:
    """Aplica cambios de PATCH (solo claves presentes). Emite ``ticket.updated`` / ``ticket.assigned``."""
    if not can_edit_ticket(principal, ticket):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No puede editar este ticket")
    before = snapshot(ticket)
    old_assignee = ticket.assignee_id

    if "title" in changes and changes["title"] is not None:
        ticket.title = changes["title"]
    if "description" in changes and changes["description"] is not None:
        ticket.description = changes["description"]
    if changes.get("priority_key") is not None:
        ticket.priority_id = _priority_by_key(session, changes["priority_key"]).id

    department_id = ticket.department_id
    department_changed = False
    if changes.get("department_id") is not None and changes["department_id"] != ticket.department_id:
        department_id = _validate_service_department(session, changes["department_id"]).id
        department_changed = True
        ticket.department_id = department_id
        if "team_id" not in changes:
            default_team = default_team_for_department(session, department_id)
            ticket.team_id = default_team.id if default_team else None
    _apply_classification_changes(session, ticket, changes, department_id, department_changed)
    if "team_id" in changes:
        team_id = changes["team_id"]
        ticket.team_id = _validate_team(session, team_id, department_id).id if team_id else None
    elif ticket.team_id is not None:
        current_team = session.get(Team, ticket.team_id)
        if current_team is not None and current_team.department_id != department_id:
            ticket.team_id = None
    if "assignee_id" in changes:
        assignee_id = changes["assignee_id"]
        ticket.assignee_id = _validate_assignee(session, assignee_id).id if assignee_id else None

    session.flush()
    session.expire(ticket)
    after = snapshot(ticket)
    diff = {
        field: {"from": before[field], "to": after[field]}
        for field in after
        if before[field] != after[field]
    }
    if not diff:
        return ticket
    ticket.updated_at = utcnow()
    session.flush()
    emit(
        session,
        EVENT_UPDATED,
        ticket,
        principal.user_id,
        {"before": before, "after": after, "changes": diff},
    )
    if "category_id" in diff or "subcategory_id" in diff:
        # La categoría/subcategoría condiciona la política SLA (como la prioridad).
        from app.services.sla.engine import ensure_ticket_sla

        ensure_ticket_sla(session, ticket)
    if ticket.assignee_id != old_assignee:
        emit(
            session,
            EVENT_ASSIGNED,
            ticket,
            principal.user_id,
            {
                "before": {"assignee_id": old_assignee},
                "after": {"assignee_id": ticket.assignee_id},
                "from_assignee_id": old_assignee,
                "to_assignee_id": ticket.assignee_id,
            },
        )
    return ticket


def _apply_classification_changes(
    session: Session,
    ticket: Ticket,
    changes: dict[str, Any],
    department_id: int,
    department_changed: bool,
) -> None:
    """Categoría, subcategoría, proveedor, canal y solución (PATCH de un agente)."""
    category_id = changes["category_id"] if "category_id" in changes else ticket.category_id
    subcategory_id = changes["subcategory_id"] if "subcategory_id" in changes else ticket.subcategory_id
    explicit = "category_id" in changes or "subcategory_id" in changes
    if department_changed and "category_id" not in changes and category_id is not None:
        # La categoría pertenece al departamento anterior: se retira (como el equipo).
        current = session.get(TicketCategory, category_id)
        if current is None or current.department_id != department_id:
            category_id = None
            if "subcategory_id" not in changes:
                subcategory_id = None
    if "category_id" in changes and "subcategory_id" not in changes and subcategory_id is not None:
        current_sub = session.get(TicketSubcategory, subcategory_id)
        if category_id is None or current_sub is None or current_sub.category_id != category_id:
            subcategory_id = None
    if explicit:
        type_key = session.scalar(select(TicketType.key).where(TicketType.id == ticket.type_id))
        category, subcategory = validate_classification(
            session,
            department_id=department_id,
            type_key=type_key,
            category_id=category_id,
            subcategory_id=subcategory_id,
        )
        category_id = category.id if category else None
        subcategory_id = subcategory.id if subcategory else None
    ticket.category_id = category_id
    ticket.subcategory_id = subcategory_id

    if "provider_id" in changes:
        provider_id = changes["provider_id"]
        ticket.provider_id = (
            validate_provider(session, provider_id, department_id).id if provider_id else None
        )
    elif department_changed and ticket.provider_id is not None:
        provider = session.get(Provider, ticket.provider_id)
        if provider is None or not provider.applies_to(department_id):
            ticket.provider_id = None
    if changes.get("channel") is not None:
        ticket.channel = _validate_channel(changes["channel"])
    if "resolution_note" in changes:
        note = (changes["resolution_note"] or "").strip() or None
        if note is None and ticket.resolved_at is not None:
            raise _unprocessable("La solución no puede quedar vacía en un ticket resuelto")
        ticket.resolution_note = note


def find_transition(
    session: Session, from_state: WorkflowState, to_state_key: str
) -> tuple[WorkflowState, WorkflowTransition]:
    """Busca el estado destino (mismo flujo) y la transición permitida; 422/409 si no aplica."""
    to_state = session.scalar(
        select(WorkflowState).where(
            WorkflowState.workflow_id == from_state.workflow_id, WorkflowState.key == to_state_key
        )
    )
    if to_state is None:
        raise _unprocessable(f"Estado desconocido: {to_state_key}")
    transition = session.scalar(
        select(WorkflowTransition).where(
            WorkflowTransition.workflow_id == from_state.workflow_id,
            WorkflowTransition.from_state_id == from_state.id,
            WorkflowTransition.to_state_id == to_state.id,
        )
    )
    if transition is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Transición no permitida: {from_state.key} → {to_state.key}",
        )
    return to_state, transition


def counts_as_first_response(principal: Principal, ticket: Ticket) -> bool:
    """¿La acción de ``principal`` cuenta como primera respuesta (SLA)?

    Solo la de un agente humano distinto del solicitante que **atiende** el ticket
    (``can_edit_ticket``): las automatizaciones (usuario técnico) no la cumplen, ni un agente de
    otra área que solo lo ve (aprobación, visibilidad por gerencia) y comenta en público.
    """
    return (
        principal.is_agent
        and not principal.is_system
        and principal.user_id != ticket.requester_id
        and can_edit_ticket(principal, ticket)
    )


def transition_ticket(
    session: Session,
    principal: Principal,
    ticket: Ticket,
    to_state_key: str,
    comment: str | None = None,
    *,
    extra_guard_data: dict[str, Any] | None = None,
    resolution_note: str | None = None,
) -> Ticket:
    """Cambia el estado validando ``workflow_transitions``, permisos y ``run_guards``.

    Datos que reciben los guards de ``ticket.transition``: ``transition_id``, ``transition_name``,
    ``requires_approval``, ``from_state``, ``to_state``, ``from_category``, ``to_category``,
    ``comment`` (+ ``extra_guard_data``).

    Al resolver (entrar en categoría ``done`` desde otra) exige solución (``resolution_note``
    o la ya guardada) y, si quien resuelve atiende el ticket y el departamento tiene
    categorías activas, la categoría (422). Guarda ``resolved_by_id`` y
    ``business_resolution_minutes``; reabrir los limpia.
    """
    from_state = session.get(WorkflowState, ticket.status_id)
    if from_state is None:  # pragma: no cover - integridad referencial
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Estado actual inválido")
    to_state, transition = find_transition(session, from_state, to_state_key)

    can_edit = can_edit_ticket(principal, ticket)
    if not can_edit and not can_requester_transition(principal, ticket, from_state, to_state):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="No tiene permiso para esta transición"
        )
    resolving = to_state.category == DONE_CATEGORY and from_state.category != DONE_CATEGORY
    note = (resolution_note or "").strip() or None
    if resolving:
        if note is None and not (ticket.resolution_note or "").strip():
            raise _unprocessable(MSG_RESOLUTION_REQUIRED)
        if can_edit and ticket.category_id is None:
            type_key = session.scalar(select(TicketType.key).where(TicketType.id == ticket.type_id))
            if department_has_categories(session, ticket.department_id, type_key):
                raise _unprocessable(MSG_CATEGORY_REQUIRED)

    if note is not None and not resolving and not can_edit:
        # done → done (p.ej. el solicitante cierra su ticket resuelto): la solución oficial es
        # la de quien lo resolvió; solo quien atiende el ticket puede modificarla.
        note = None
    previous_note = ticket.resolution_note
    note_changed = note is not None and note != previous_note and to_state.category == DONE_CATEGORY
    if note_changed and resolving:
        # Se guarda antes de los guards: si la transición queda pendiente de aprobación, la
        # solicitud se confirma junto con la solución y al aprobarse la transición la encuentra.
        ticket.resolution_note = note

    guard_data: dict[str, Any] = {
        "transition_id": transition.id,
        "transition_name": transition.name,
        "requires_approval": transition.requires_approval,
        "from_state": from_state.key,
        "to_state": to_state.key,
        "from_category": from_state.category,
        "to_category": to_state.category,
        "comment": comment,
    }
    if extra_guard_data:
        guard_data.update(extra_guard_data)
    run_guards(session, GUARD_TRANSITION, ticket, principal, guard_data)

    now = utcnow()
    before = {
        "status": from_state.key,
        "resolved_at": ticket.resolved_at,
        "closed_at": ticket.closed_at,
    }
    if note_changed:
        before["resolution_note"] = previous_note
    ticket.status_id = to_state.id
    if to_state.category == DONE_CATEGORY:
        if ticket.resolved_at is None:
            ticket.resolved_at = now
        if to_state.is_final:
            ticket.closed_at = now
        if note_changed:
            ticket.resolution_note = note
        if resolving:
            from app.services.sla.engine import business_resolution_minutes

            ticket.resolved_by_id = principal.user_id
            ticket.business_resolution_minutes = business_resolution_minutes(
                session, ticket, ticket.resolved_at
            )
    elif from_state.category == DONE_CATEGORY:
        # Reapertura: se limpian las marcas de resolución/cierre.
        ticket.resolved_at = None
        ticket.closed_at = None
        ticket.resolved_by_id = None
        ticket.business_resolution_minutes = None
    if (
        ticket.first_response_at is None
        and from_state.is_initial
        and counts_as_first_response(principal, ticket)
    ):
        ticket.first_response_at = now
    ticket.updated_at = now
    session.flush()
    session.expire(ticket)

    after: dict[str, Any] = {
        "status": to_state.key,
        "resolved_at": ticket.resolved_at,
        "closed_at": ticket.closed_at,
    }
    if note_changed:
        after["resolution_note"] = ticket.resolution_note
    emit(
        session,
        EVENT_TRANSITIONED,
        ticket,
        principal.user_id,
        {
            "before": before,
            "after": after,
            "transition_id": transition.id,
            "from_state": from_state.key,
            "to_state": to_state.key,
            "from_category": from_state.category,
            "to_category": to_state.category,
        },
    )
    if comment and comment.strip():
        add_comment(session, principal, ticket, comment.strip(), "public")
    return ticket


def _resolve_target(session: Session, from_state: WorkflowState) -> WorkflowState | None:
    """Estado ``done`` alcanzable desde ``from_state`` (prefiere uno no final: «Resuelto»)."""
    return session.scalars(
        select(WorkflowState)
        .join(WorkflowTransition, WorkflowTransition.to_state_id == WorkflowState.id)
        .where(
            WorkflowTransition.workflow_id == from_state.workflow_id,
            WorkflowTransition.from_state_id == from_state.id,
            WorkflowState.category == DONE_CATEGORY,
        )
        .order_by(WorkflowState.is_final, WorkflowState.order, WorkflowState.id)
    ).first()


def create_work_order(
    session: Session,
    principal: Principal,
    parent: Ticket,
    *,
    title: str | None = None,
    description: str | None = None,
    category_id: int | None = None,
    subcategory_id: int | None = None,
    resolve_parent: bool = False,
    resolution_note: str | None = None,
) -> Ticket:
    """Traslada un ticket a una orden de trabajo: crea un ticket ``work_order`` hijo (mismo
    departamento, solicitante y prioridad) y, si ``resolve_parent``, resuelve el original con
    la solución indicada o «Trasladado a orden de trabajo #<id>». Requiere ``can_edit_ticket``.
    """
    if not can_edit_ticket(principal, parent):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="No puede trasladar este ticket a una orden de trabajo"
        )
    priority_key = session.scalar(select(Priority.key).where(Priority.id == parent.priority_id))
    child = create_ticket(
        session,
        principal,
        title=(title or f"Orden de trabajo: {parent.title}")[:200],
        description=parent.description if description is None else description,
        type_key=WORK_ORDER_TYPE,
        priority_key=priority_key or "medium",
        department_id=parent.department_id,
        project_id=parent.project_id,
        category_id=category_id,
        subcategory_id=subcategory_id,
        channel=parent.channel or DEFAULT_CHANNEL,
        requester_id=parent.requester_id,
        parent_ticket_id=parent.id,
    )
    emit(
        session,
        EVENT_UPDATED,
        parent,
        principal.user_id,
        {
            "before": {"work_order_id": None},
            "after": {"work_order_id": child.id},
            "changes": {"work_order_id": {"from": None, "to": child.id}},
        },
    )
    if resolve_parent:
        from_state = session.get(WorkflowState, parent.status_id)
        if from_state is not None and from_state.category != DONE_CATEGORY:
            target = _resolve_target(session, from_state)
            if target is None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail=f"No hay una transición para resolver el ticket desde «{from_state.name}»",
                )
            note = (resolution_note or "").strip() or f"Trasladado a orden de trabajo #{child.id}"
            # SAVEPOINT: si un guard (p.ej. aprobación) bloquea, no confirma nada a medias.
            with session.begin_nested():
                transition_ticket(session, principal, parent, target.key, resolution_note=note)
    return child


def add_comment(
    session: Session, principal: Principal, ticket: Ticket, body: str, visibility: str = "public"
) -> TicketComment:
    """Agrega un comentario. Solo agent/admin pueden crear comentarios ``internal``."""
    if visibility not in ("public", "internal"):
        raise _unprocessable("Visibilidad inválida")
    if visibility == "internal" and not can_view_internal_comments(principal, ticket):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Solo los agentes que atienden el ticket pueden crear comentarios internos",
        )
    now = utcnow()
    comment = TicketComment(
        ticket_id=ticket.id,
        author_id=principal.user_id,
        body=body,
        visibility=visibility,
        created_at=now,
    )
    session.add(comment)
    if (
        visibility == "public"
        and ticket.first_response_at is None
        and counts_as_first_response(principal, ticket)
    ):
        ticket.first_response_at = now
    ticket.updated_at = now
    session.flush()
    emit(
        session,
        EVENT_COMMENTED,
        ticket,
        principal.user_id,
        {
            "comment_id": comment.id,
            "visibility": visibility,
            "author_is_agent": principal.is_agent,
        },
    )
    return comment


def serialize_comment(comment: TicketComment) -> dict[str, Any]:
    return {
        "id": comment.id,
        "ticket_id": comment.ticket_id,
        "author": _user_ref(comment.author),
        "body": comment.body,
        "visibility": comment.visibility,
        "created_at": comment.created_at,
    }


def list_comments(session: Session, principal: Principal, ticket: Ticket) -> list[dict[str, Any]]:
    stmt = (
        select(TicketComment)
        .options(joinedload(TicketComment.author))
        .where(TicketComment.ticket_id == ticket.id)
        .order_by(TicketComment.created_at, TicketComment.id)
    )
    if not can_view_internal_comments(principal, ticket):
        stmt = stmt.where(TicketComment.visibility == "public")
    return [serialize_comment(c) for c in session.scalars(stmt).all()]


def _is_internal_history(entry: TicketHistory) -> bool:
    for payload in (entry.after_json, entry.before_json):
        if isinstance(payload, dict) and payload.get("visibility") == "internal":
            return True
    return False


def list_history(session: Session, principal: Principal, ticket: Ticket) -> list[dict[str, Any]]:
    """Historial del ticket; oculta eventos de comentarios internos a no agentes."""
    entries = session.scalars(
        select(TicketHistory)
        .options(joinedload(TicketHistory.actor))
        .where(TicketHistory.ticket_id == ticket.id)
        .order_by(TicketHistory.timestamp, TicketHistory.id)
    ).all()
    show_internal = can_view_internal_comments(principal, ticket)
    return [
        {
            "id": entry.id,
            "ticket_id": entry.ticket_id,
            "event_type": entry.event_type,
            "actor": _user_ref(entry.actor),
            "before": entry.before_json,
            "after": entry.after_json,
            "timestamp": entry.timestamp,
        }
        for entry in entries
        if show_internal or not _is_internal_history(entry)
    ]
