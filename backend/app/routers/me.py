"""Identidad del usuario autenticado."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import Principal, get_principal
from app.models.catalog import Gerencia
from app.models.core import Department, Team
from app.schemas.core import MeOut

router = APIRouter(tags=["identidad"])


@router.get("/me", response_model=MeOut)
def me(principal: Principal = Depends(get_principal), session: Session = Depends(get_db)) -> MeOut:
    teams = []
    if principal.team_ids:
        teams = session.scalars(
            select(Team).where(Team.id.in_(sorted(principal.team_ids))).order_by(Team.name)
        ).all()
    department = (
        session.get(Department, principal.home_department_id)
        if principal.home_department_id is not None
        else None
    )
    gerencia = (
        session.get(Gerencia, principal.home_gerencia_id)
        if principal.home_gerencia_id is not None
        else None
    )
    return MeOut(
        id=principal.user_id,
        sub=principal.sub,
        username=principal.username,
        display_name=principal.display_name,
        email=principal.email,
        roles=sorted(principal.roles),
        teams=[{"id": t.id, "name": t.name, "department_id": t.department_id} for t in teams],
        department_ids=sorted(principal.department_ids),
        department={"id": department.id, "name": department.name} if department else None,
        gerencia={"id": gerencia.id, "name": gerencia.name} if gerencia else None,
        vip=principal.vip,
        viewer_gerencia_ids=sorted(principal.viewer_gerencia_ids),
        lead_team_ids=sorted(principal.lead_team_ids),
    )
