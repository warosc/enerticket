"""Catálogo: departamentos, equipos, prioridades, tipos, proyectos y flujo por defecto."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import Principal, get_principal
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
    types = session.scalars(select(TicketType).order_by(TicketType.id)).all()
    projects = session.scalars(select(Project).order_by(Project.code)).all()
    return {
        "departments": [
            {"id": d.id, "name": d.name, "parent_id": d.parent_id} for d in departments
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
    }
