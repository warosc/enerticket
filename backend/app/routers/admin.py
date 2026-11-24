"""Administración (rol admin): departamentos, equipos, miembros y proyectos."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.db import get_db
from app.core.security import require_roles
from app.models.core import Department, Project, Team, TeamMember, User
from app.schemas.core import (
    DepartmentIn,
    DepartmentOut,
    DepartmentPatch,
    ProjectIn,
    ProjectOut,
    ProjectPatch,
    TeamIn,
    TeamMemberIn,
    TeamMemberOut,
    TeamMemberPatch,
    TeamOut,
    TeamPatch,
)

router = APIRouter(prefix="/admin", tags=["administración"], dependencies=[Depends(require_roles("admin"))])


def _commit(session: Session, conflict_detail: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=conflict_detail) from exc


def _get_or_404(session: Session, model: type, obj_id: Any, label: str) -> Any:
    obj = session.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{label} no encontrado")
    return obj


def _require_exists(session: Session, model: type, obj_id: int | None, label: str) -> None:
    if obj_id is not None and session.get(model, obj_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{label} no existe")


def _department_out(d: Department) -> dict:
    return {"id": d.id, "name": d.name, "parent_id": d.parent_id}


def _team_out(t: Team) -> dict:
    return {
        "id": t.id,
        "department_id": t.department_id,
        "name": t.name,
        "external_group": t.external_group,
    }


def _project_out(p: Project) -> dict:
    return {"id": p.id, "code": p.code, "name": p.name, "owner_id": p.owner_id, "team_id": p.team_id}


def _member_out(m: TeamMember) -> dict:
    return {
        "team_id": m.team_id,
        "user_id": m.user_id,
        "role": m.role,
        "username": m.user.username,
        "display_name": m.user.display_name,
        "email": m.user.email,
    }


# --------------------------------------------------------------------------- departamentos


@router.get("/departments", response_model=list[DepartmentOut])
def list_departments(session: Session = Depends(get_db)) -> list[dict]:
    return [_department_out(d) for d in session.scalars(select(Department).order_by(Department.name))]


@router.post("/departments", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
def create_department(payload: DepartmentIn, session: Session = Depends(get_db)) -> dict:
    _require_exists(session, Department, payload.parent_id, "El departamento padre")
    department = Department(name=payload.name, parent_id=payload.parent_id)
    session.add(department)
    _commit(session, "Ya existe un departamento con ese nombre")
    return _department_out(department)


def _creates_cycle(session: Session, department_id: int, parent_id: int | None) -> bool:
    seen: set[int] = set()
    current = parent_id
    while current is not None:
        if current == department_id or current in seen:
            return True
        seen.add(current)
        current = session.scalar(select(Department.parent_id).where(Department.id == current))
    return False


@router.patch("/departments/{department_id}", response_model=DepartmentOut)
def update_department(
    department_id: int, payload: DepartmentPatch, session: Session = Depends(get_db)
) -> dict:
    department = _get_or_404(session, Department, department_id, "Departamento")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        department.name = changes["name"]
    if "parent_id" in changes:
        parent_id = changes["parent_id"]
        _require_exists(session, Department, parent_id, "El departamento padre")
        if _creates_cycle(session, department.id, parent_id):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La jerarquía de departamentos no puede tener ciclos",
            )
        department.parent_id = parent_id
    _commit(session, "Ya existe un departamento con ese nombre")
    return _department_out(department)


# --------------------------------------------------------------------------- equipos


@router.get("/teams", response_model=list[TeamOut])
def list_teams(session: Session = Depends(get_db)) -> list[dict]:
    return [_team_out(t) for t in session.scalars(select(Team).order_by(Team.name))]


@router.post("/teams", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
def create_team(payload: TeamIn, session: Session = Depends(get_db)) -> dict:
    _require_exists(session, Department, payload.department_id, "El departamento")
    team = Team(
        department_id=payload.department_id,
        name=payload.name,
        external_group=payload.external_group or None,
    )
    session.add(team)
    _commit(session, "Ya existe un equipo con ese nombre en el departamento")
    return _team_out(team)


@router.patch("/teams/{team_id}", response_model=TeamOut)
def update_team(team_id: int, payload: TeamPatch, session: Session = Depends(get_db)) -> dict:
    team = _get_or_404(session, Team, team_id, "Equipo")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("department_id") is not None:
        _require_exists(session, Department, changes["department_id"], "El departamento")
        team.department_id = changes["department_id"]
    if changes.get("name") is not None:
        team.name = changes["name"]
    if "external_group" in changes:
        team.external_group = changes["external_group"] or None
    _commit(session, "Ya existe un equipo con ese nombre en el departamento")
    return _team_out(team)


@router.get("/teams/{team_id}/members", response_model=list[TeamMemberOut])
def list_members(team_id: int, session: Session = Depends(get_db)) -> list[dict]:
    _get_or_404(session, Team, team_id, "Equipo")
    members = session.scalars(
        select(TeamMember)
        .options(joinedload(TeamMember.user))
        .where(TeamMember.team_id == team_id)
        .order_by(TeamMember.role.desc(), TeamMember.user_id)
    ).all()
    return [_member_out(m) for m in members]


@router.post(
    "/teams/{team_id}/members", response_model=TeamMemberOut, status_code=status.HTTP_201_CREATED
)
def add_member(team_id: int, payload: TeamMemberIn, session: Session = Depends(get_db)) -> dict:
    _get_or_404(session, Team, team_id, "Equipo")
    user = session.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="El usuario no existe")
    if session.get(TeamMember, (team_id, user.id)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="El usuario ya es miembro del equipo")
    member = TeamMember(team_id=team_id, user_id=user.id, role=payload.role)
    session.add(member)
    _commit(session, "El usuario ya es miembro del equipo")
    session.refresh(member)
    return _member_out(member)


@router.patch("/teams/{team_id}/members/{user_id}", response_model=TeamMemberOut)
def update_member(
    team_id: int, user_id: int, payload: TeamMemberPatch, session: Session = Depends(get_db)
) -> dict:
    member = _get_or_404(session, TeamMember, (team_id, user_id), "Miembro")
    member.role = payload.role
    session.commit()
    session.refresh(member)
    return _member_out(member)


@router.delete("/teams/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(team_id: int, user_id: int, session: Session = Depends(get_db)) -> Response:
    member = _get_or_404(session, TeamMember, (team_id, user_id), "Miembro")
    session.delete(member)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- proyectos


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(session: Session = Depends(get_db)) -> list[dict]:
    return [_project_out(p) for p in session.scalars(select(Project).order_by(Project.code))]


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectIn, session: Session = Depends(get_db)) -> dict:
    _require_exists(session, User, payload.owner_id, "El responsable")
    _require_exists(session, Team, payload.team_id, "El equipo")
    code = payload.code.upper()
    if session.scalar(select(func.count()).select_from(Project).where(Project.code == code)):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Ya existe un proyecto con ese código")
    project = Project(code=code, name=payload.name, owner_id=payload.owner_id, team_id=payload.team_id)
    session.add(project)
    _commit(session, "Ya existe un proyecto con ese código")
    return _project_out(project)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int, payload: ProjectPatch, session: Session = Depends(get_db)
) -> dict:
    project = _get_or_404(session, Project, project_id, "Proyecto")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("code") is not None:
        project.code = changes["code"].upper()
    if changes.get("name") is not None:
        project.name = changes["name"]
    if "owner_id" in changes:
        _require_exists(session, User, changes["owner_id"], "El responsable")
        project.owner_id = changes["owner_id"]
    if "team_id" in changes:
        _require_exists(session, Team, changes["team_id"], "El equipo")
        project.team_id = changes["team_id"]
    _commit(session, "Ya existe un proyecto con ese código")
    return _project_out(project)
