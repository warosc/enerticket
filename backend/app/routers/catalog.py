"""Catálogo: departamentos, equipos, prioridades, tipos, proyectos, flujo por defecto y
catálogo de servicio (categorías, subcategorías, proveedores, canales y gerencias)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import Principal, get_principal
from app.models.catalog import Gerencia, Provider, TicketCategory, TicketSubcategory
from app.models.core import (
    Department,
    Priority,
    Project,
    Team,
    TicketType,
    WorkflowDefinition,
    WorkflowState,
    WorkflowTransition,
)
from app.schemas.core import CatalogOut
from app.services.tickets import CHANNEL_LABELS

router = APIRouter(tags=["catálogo"])


def workflow_payload(session: Session) -> dict:
    workflow = session.scalars(
        select(WorkflowDefinition).order_by(
            WorkflowDefinition.is_default.desc(), WorkflowDefinition.id
        )
    ).first()
    if workflow is None:
        return {"id": None, "key": None, "name": None, "states": [], "transitions": []}
    states = session.scalars(
        select(WorkflowState)
        .where(WorkflowState.workflow_id == workflow.id)
        .order_by(WorkflowState.order, WorkflowState.id)
    ).all()
    keys = {s.id: s.key for s in states}
    transitions = session.scalars(
        select(WorkflowTransition)
        .where(WorkflowTransition.workflow_id == workflow.id)
        .order_by(WorkflowTransition.id)
    ).all()
    return {
        "id": workflow.id,
        "key": workflow.key,
        "name": workflow.name,
        "states": [
            {
                "id": s.id,
                "key": s.key,
                "name": s.name,
                "category": s.category,
                "order": s.order,
                "is_initial": s.is_initial,
                "is_final": s.is_final,
            }
            for s in states
        ],
        "transitions": [
            {
                "id": t.id,
                "from_state_id": t.from_state_id,
                "to_state_id": t.to_state_id,
                "from_key": keys.get(t.from_state_id, ""),
                "to_key": keys.get(t.to_state_id, ""),
                "name": t.name,
                "requires_approval": t.requires_approval,
            }
            for t in transitions
        ],
    }


@router.get("/catalog", response_model=CatalogOut)
def catalog(
    _: Principal = Depends(get_principal), session: Session = Depends(get_db)
) -> dict:
    departments = session.scalars(select(Department).order_by(Department.name)).all()
    teams = session.scalars(select(Team).order_by(Team.name)).all()
    priorities = session.scalars(select(Priority).order_by(Priority.rank)).all()
    types = session.scalars(
        select(TicketType).where(TicketType.active.is_(True)).order_by(TicketType.id)
    ).all()
    projects = session.scalars(select(Project).order_by(Project.code)).all()
    return {
        "departments": [
            {
                "id": d.id,
                "name": d.name,
                "parent_id": d.parent_id,
                "gerencia_id": d.gerencia_id,
                "serves_tickets": bool(d.serves_tickets),
                "active": bool(d.active),
            }
            for d in departments
        ],
        "teams": [
            {
                "id": t.id,
                "department_id": t.department_id,
                "name": t.name,
                "external_group": t.external_group,
            }
            for t in teams
        ],
        "priorities": [
            {"id": p.id, "key": p.key, "name": p.name, "rank": p.rank} for p in priorities
        ],
        "ticket_types": [{"id": t.id, "key": t.key, "name": t.name} for t in types],
        "projects": [
            {
                "id": p.id,
                "code": p.code,
                "name": p.name,
                "owner_id": p.owner_id,
                "team_id": p.team_id,
            }
            for p in projects
        ],
        "workflow": workflow_payload(session),
        **service_catalog_payload(session),
    }


def service_catalog_payload(session: Session) -> dict:
    """Categorías activas (con sus subcategorías activas), proveedores, canales y gerencias."""
    categories = session.scalars(
        select(TicketCategory)
        .where(TicketCategory.active.is_(True))
        .order_by(TicketCategory.department_id, TicketCategory.sort_order, TicketCategory.name)
    ).all()
    subcategories: dict[int, list[TicketSubcategory]] = {}
    if categories:
        for sub in session.scalars(
            select(TicketSubcategory)
            .where(
                TicketSubcategory.active.is_(True),
                TicketSubcategory.category_id.in_([c.id for c in categories]),
            )
            .order_by(TicketSubcategory.sort_order, TicketSubcategory.name)
        ).all():
            subcategories.setdefault(sub.category_id, []).append(sub)
    providers = session.scalars(
        select(Provider).where(Provider.active.is_(True)).order_by(Provider.name)
    ).all()
    gerencias = session.scalars(
        select(Gerencia).where(Gerencia.active.is_(True)).order_by(Gerencia.name)
    ).all()
    return {
        "categories": [
            {
                "id": c.id,
                "department_id": c.department_id,
                "name": c.name,
                "type_keys": list(c.type_keys or []),
                "subcategories": [{"id": s.id, "name": s.name} for s in subcategories.get(c.id, [])],
            }
            for c in categories
        ],
        "providers": [
            {"id": p.id, "name": p.name, "department_ids": list(p.department_ids or [])}
            for p in providers
        ],
        "channels": [{"key": key, "label": label} for key, label in CHANNEL_LABELS.items()],
        "gerencias": [{"id": g.id, "name": g.name} for g in gerencias],
    }
