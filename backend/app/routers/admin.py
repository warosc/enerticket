"""Administración (rol admin): departamentos, equipos, miembros, proyectos y catálogo de
servicio (gerencias, valores del AD, categorías, subcategorías, proveedores, importación CSV,
perfil de usuarios y usuarios que ven los tickets de una gerencia)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.accounts import apply_alias_to_users
from app.core.catalog import (
    MAX_IMPORT_BYTES,
    CatalogImportError,
    import_catalog_csv,
    normalize_ad_value,
    sanitize_import_cell,
)
from app.core.db import get_db, utcnow
from app.core.security import require_roles
from app.models.catalog import (
    DepartmentAlias,
    Gerencia,
    GerenciaViewer,
    Provider,
    TicketCategory,
    TicketSubcategory,
)
from app.models.core import Department, Project, Team, TeamMember, TicketType, User
from app.routers.users import user_out
from app.schemas.core import (
    CatalogImportOut,
    CategoryIn,
    CategoryOut,
    CategoryPatch,
    DepartmentAliasIn,
    DepartmentAliasOut,
    DepartmentAliasPatch,
    DepartmentIn,
    DepartmentOut,
    DepartmentPatch,
    GerenciaIn,
    GerenciaOut,
    GerenciaPatch,
    GerenciaViewerOut,
    GerenciaViewersIn,
    ProjectIn,
    ProjectOut,
    ProjectPatch,
    ProviderIn,
    ProviderOut,
    ProviderPatch,
    SubcategoryIn,
    SubcategoryPatch,
    TeamIn,
    TeamMemberIn,
    TeamMemberOut,
    TeamMemberPatch,
    TeamOut,
    TeamPatch,
    UserOut,
    UserProfilePatch,
)

logger = logging.getLogger(__name__)

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
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{label} no existe")


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _department_out(d: Department) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "parent_id": d.parent_id,
        "gerencia_id": d.gerencia_id,
        "serves_tickets": bool(d.serves_tickets),
        "active": bool(d.active),
    }


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
    _require_exists(session, Gerencia, payload.gerencia_id, "La gerencia")
    department = Department(
        name=payload.name,
        parent_id=payload.parent_id,
        gerencia_id=payload.gerencia_id,
        serves_tickets=payload.serves_tickets,
        active=payload.active,
    )
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
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="La jerarquía de departamentos no puede tener ciclos",
            )
        department.parent_id = parent_id
    if "gerencia_id" in changes:
        _require_exists(session, Gerencia, changes["gerencia_id"], "La gerencia")
        department.gerencia_id = changes["gerencia_id"]
    if changes.get("serves_tickets") is not None:
        department.serves_tickets = changes["serves_tickets"]
    if changes.get("active") is not None:
        department.active = changes["active"]
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
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="El usuario no existe")
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


# --------------------------------------------------------------------------- gerencias


def _gerencia_out(session: Session, g: Gerencia) -> dict:
    department_ids = session.scalars(
        select(Department.id).where(Department.gerencia_id == g.id).order_by(Department.id)
    ).all()
    return {
        "id": g.id,
        "name": g.name,
        "external_group": g.external_group,
        "active": g.active,
        "department_ids": list(department_ids),
    }


@router.get("/gerencias", response_model=list[GerenciaOut])
def list_gerencias(session: Session = Depends(get_db)) -> list[dict]:
    return [_gerencia_out(session, g) for g in session.scalars(select(Gerencia).order_by(Gerencia.name))]


@router.post("/gerencias", response_model=GerenciaOut, status_code=status.HTTP_201_CREATED)
def create_gerencia(payload: GerenciaIn, session: Session = Depends(get_db)) -> dict:
    gerencia = Gerencia(
        name=payload.name, external_group=payload.external_group or None, active=payload.active
    )
    session.add(gerencia)
    _commit(session, "Ya existe una gerencia con ese nombre")
    return _gerencia_out(session, gerencia)


@router.patch("/gerencias/{gerencia_id}", response_model=GerenciaOut)
def update_gerencia(gerencia_id: int, payload: GerenciaPatch, session: Session = Depends(get_db)) -> dict:
    gerencia = _get_or_404(session, Gerencia, gerencia_id, "Gerencia")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        gerencia.name = changes["name"]
    if "external_group" in changes:
        gerencia.external_group = changes["external_group"] or None
    if changes.get("active") is not None:
        gerencia.active = changes["active"]
    _commit(session, "Ya existe una gerencia con ese nombre")
    return _gerencia_out(session, gerencia)


def _viewer_out(user: User) -> dict:
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "email": user.email}


def _list_viewers(session: Session, gerencia_id: int) -> list[dict]:
    users = session.scalars(
        select(User)
        .join(GerenciaViewer, GerenciaViewer.user_id == User.id)
        .where(GerenciaViewer.gerencia_id == gerencia_id)
        .order_by(User.display_name, User.id)
    ).all()
    return [_viewer_out(u) for u in users]


@router.get("/gerencias/{gerencia_id}/viewers", response_model=list[GerenciaViewerOut])
def list_gerencia_viewers(gerencia_id: int, session: Session = Depends(get_db)) -> list[dict]:
    _get_or_404(session, Gerencia, gerencia_id, "Gerencia")
    return _list_viewers(session, gerencia_id)


@router.put("/gerencias/{gerencia_id}/viewers", response_model=list[GerenciaViewerOut])
def replace_gerencia_viewers(
    gerencia_id: int, payload: GerenciaViewersIn, session: Session = Depends(get_db)
) -> list[dict]:
    """Reemplaza la lista de usuarios asignados manualmente (además del grupo AD)."""
    _get_or_404(session, Gerencia, gerencia_id, "Gerencia")
    wanted = set(payload.user_ids)
    found = set(session.scalars(select(User.id).where(User.id.in_(sorted(wanted)))).all()) if wanted else set()
    missing = sorted(wanted - found)
    if missing:
        raise _unprocessable(f"Usuarios inexistentes: {', '.join(str(i) for i in missing[:20])}")
    current = {
        v.user_id: v
        for v in session.scalars(select(GerenciaViewer).where(GerenciaViewer.gerencia_id == gerencia_id)).all()
    }
    for user_id, viewer in current.items():
        if user_id not in wanted:
            session.delete(viewer)
    for user_id in wanted - set(current):
        session.add(GerenciaViewer(gerencia_id=gerencia_id, user_id=user_id))
    _commit(session, "No se pudo actualizar la lista de usuarios")
    return _list_viewers(session, gerencia_id)


# --------------------------------------------------------------------------- valores del AD (alias)


def _alias_out(alias: DepartmentAlias) -> dict:
    department = alias.department
    return {
        "id": alias.id,
        "normalized_value": alias.normalized_value,
        "raw_value": alias.raw_value,
        "department_id": alias.department_id,
        "department": {"id": department.id, "name": department.name} if department else None,
        "pending": alias.department_id is None,
        "created_at": alias.created_at,
    }


@router.get("/department-aliases", response_model=list[DepartmentAliasOut])
def list_department_aliases(
    pending: Optional[bool] = None, session: Session = Depends(get_db)
) -> list[dict]:
    stmt = select(DepartmentAlias).options(joinedload(DepartmentAlias.department))
    if pending is True:
        stmt = stmt.where(DepartmentAlias.department_id.is_(None))
    elif pending is False:
        stmt = stmt.where(DepartmentAlias.department_id.is_not(None))
    stmt = stmt.order_by(DepartmentAlias.department_id.is_not(None), DepartmentAlias.normalized_value)
    return [_alias_out(a) for a in session.scalars(stmt).all()]


@router.post("/department-aliases", response_model=DepartmentAliasOut, status_code=status.HTTP_201_CREATED)
def create_department_alias(payload: DepartmentAliasIn, session: Session = Depends(get_db)) -> dict:
    normalized = normalize_ad_value(payload.value)
    if not normalized:
        raise _unprocessable("El valor queda vacío al normalizarlo")
    _require_exists(session, Department, payload.department_id, "El departamento")
    if session.scalar(select(DepartmentAlias.id).where(DepartmentAlias.normalized_value == normalized)):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"El valor «{normalized}» ya está registrado")
    alias = DepartmentAlias(
        normalized_value=normalized,
        raw_value=sanitize_import_cell(payload.value)[:200] or normalized,
        department_id=payload.department_id,
        created_at=utcnow(),
    )
    session.add(alias)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"El valor «{normalized}» ya está registrado") from exc
    apply_alias_to_users(session, alias.normalized_value, alias.department_id)
    _commit(session, f"El valor «{normalized}» ya está registrado")
    session.refresh(alias)
    return _alias_out(alias)


@router.patch("/department-aliases/{alias_id}", response_model=DepartmentAliasOut)
def update_department_alias(
    alias_id: int, payload: DepartmentAliasPatch, session: Session = Depends(get_db)
) -> dict:
    """Asigna (o retira) el departamento del valor del AD.

    Se aplica de inmediato a los usuarios cuyo último valor del AD es este alias (sin esperar a
    su siguiente inicio de sesión) y, al asignar, completa el departamento solicitante de sus
    tickets creados mientras el valor estaba pendiente.
    """
    alias = _get_or_404(session, DepartmentAlias, alias_id, "Valor del AD")
    changes = payload.model_dump(exclude_unset=True)
    if "department_id" in changes:
        _require_exists(session, Department, changes["department_id"], "El departamento")
        alias.department_id = changes["department_id"]
        applied = apply_alias_to_users(session, alias.normalized_value, alias.department_id)
        if applied["users"] or applied["tickets"]:
            logger.info(
                "Alias del AD %s aplicado: %d usuarios, %d tickets completados",
                alias.id, applied["users"], applied["tickets"],
            )
    session.commit()
    session.refresh(alias)
    return _alias_out(alias)


@router.delete("/department-aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_department_alias(alias_id: int, session: Session = Depends(get_db)) -> Response:
    alias = _get_or_404(session, DepartmentAlias, alias_id, "Valor del AD")
    session.delete(alias)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- categorías


def _validate_type_keys(session: Session, keys: list[str] | None) -> list[str]:
    keys = list(dict.fromkeys(k.strip() for k in keys or [] if k and k.strip()))
    if keys:
        found = set(session.scalars(select(TicketType.key).where(TicketType.key.in_(keys))).all())
        unknown = [k for k in keys if k not in found]
        if unknown:
            raise _unprocessable(f"Tipos de ticket desconocidos: {', '.join(unknown)}")
    return keys


def _category_out(category: TicketCategory) -> dict:
    subcategories = sorted(category.subcategories, key=lambda s: (s.sort_order, s.name.lower(), s.id))
    return {
        "id": category.id,
        "department_id": category.department_id,
        "department": {"id": category.department.id, "name": category.department.name},
        "name": category.name,
        "type_keys": list(category.type_keys or []),
        "active": category.active,
        "sort_order": category.sort_order,
        "subcategories": [
            {"id": s.id, "name": s.name, "active": s.active, "sort_order": s.sort_order}
            for s in subcategories
        ],
    }


def _load_category(session: Session, category_id: int) -> TicketCategory:
    category = session.scalars(
        select(TicketCategory)
        .options(joinedload(TicketCategory.department), selectinload(TicketCategory.subcategories))
        .where(TicketCategory.id == category_id)
        .execution_options(populate_existing=True)
    ).first()
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Categoría no encontrada")
    return category


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(
    department_id: Optional[int] = None, session: Session = Depends(get_db)
) -> list[dict]:
    stmt = select(TicketCategory).options(
        joinedload(TicketCategory.department), selectinload(TicketCategory.subcategories)
    )
    if department_id is not None:
        stmt = stmt.where(TicketCategory.department_id == department_id)
    stmt = stmt.order_by(TicketCategory.department_id, TicketCategory.sort_order, TicketCategory.name)
    return [_category_out(c) for c in session.scalars(stmt).unique().all()]


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryIn, session: Session = Depends(get_db)) -> dict:
    _require_exists(session, Department, payload.department_id, "El departamento")
    category = TicketCategory(
        department_id=payload.department_id,
        name=sanitize_import_cell(payload.name) or payload.name,
        type_keys=_validate_type_keys(session, payload.type_keys),
        sort_order=payload.sort_order,
        active=True,
    )
    session.add(category)
    _commit(session, "Ya existe una categoría con ese nombre en el departamento")
    return _category_out(_load_category(session, category.id))


@router.patch("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, payload: CategoryPatch, session: Session = Depends(get_db)) -> dict:
    category = _get_or_404(session, TicketCategory, category_id, "Categoría")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        category.name = sanitize_import_cell(changes["name"]) or changes["name"]
    if "type_keys" in changes:
        category.type_keys = _validate_type_keys(session, changes["type_keys"])
    if changes.get("active") is not None:
        category.active = changes["active"]
    if changes.get("sort_order") is not None:
        category.sort_order = changes["sort_order"]
    _commit(session, "Ya existe una categoría con ese nombre en el departamento")
    return _category_out(_load_category(session, category_id))


@router.post(
    "/categories/{category_id}/subcategories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED
)
def create_subcategory(category_id: int, payload: SubcategoryIn, session: Session = Depends(get_db)) -> dict:
    _get_or_404(session, TicketCategory, category_id, "Categoría")
    session.add(
        TicketSubcategory(
            category_id=category_id,
            name=sanitize_import_cell(payload.name) or payload.name,
            sort_order=payload.sort_order,
            active=True,
        )
    )
    _commit(session, "Ya existe una subcategoría con ese nombre en la categoría")
    return _category_out(_load_category(session, category_id))


@router.patch("/subcategories/{subcategory_id}", response_model=CategoryOut)
def update_subcategory(
    subcategory_id: int, payload: SubcategoryPatch, session: Session = Depends(get_db)
) -> dict:
    subcategory = _get_or_404(session, TicketSubcategory, subcategory_id, "Subcategoría")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        subcategory.name = sanitize_import_cell(changes["name"]) or changes["name"]
    if changes.get("active") is not None:
        subcategory.active = changes["active"]
    if changes.get("sort_order") is not None:
        subcategory.sort_order = changes["sort_order"]
    category_id = subcategory.category_id
    _commit(session, "Ya existe una subcategoría con ese nombre en la categoría")
    return _category_out(_load_category(session, category_id))


# --------------------------------------------------------------------------- proveedores


def _provider_out(p: Provider) -> dict:
    return {"id": p.id, "name": p.name, "department_ids": list(p.department_ids or []), "active": p.active}


def _validate_department_ids(session: Session, ids: list[int] | None) -> list[int]:
    ids = list(dict.fromkeys(ids or []))
    if ids:
        found = set(session.scalars(select(Department.id).where(Department.id.in_(ids))).all())
        missing = [str(i) for i in ids if i not in found]
        if missing:
            raise _unprocessable(f"Departamentos inexistentes: {', '.join(missing)}")
    return ids


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(session: Session = Depends(get_db)) -> list[dict]:
    return [_provider_out(p) for p in session.scalars(select(Provider).order_by(Provider.name))]


@router.post("/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
def create_provider(payload: ProviderIn, session: Session = Depends(get_db)) -> dict:
    provider = Provider(
        name=sanitize_import_cell(payload.name) or payload.name,
        department_ids=_validate_department_ids(session, payload.department_ids),
        active=payload.active,
    )
    session.add(provider)
    _commit(session, "Ya existe un proveedor con ese nombre")
    return _provider_out(provider)


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
def update_provider(provider_id: int, payload: ProviderPatch, session: Session = Depends(get_db)) -> dict:
    provider = _get_or_404(session, Provider, provider_id, "Proveedor")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        provider.name = sanitize_import_cell(changes["name"]) or changes["name"]
    if "department_ids" in changes:
        provider.department_ids = _validate_department_ids(session, changes["department_ids"])
    if changes.get("active") is not None:
        provider.active = changes["active"]
    _commit(session, "Ya existe un proveedor con ese nombre")
    return _provider_out(provider)


# --------------------------------------------------------------------------- importación CSV


@router.post("/catalog/import", response_model=CatalogImportOut)
def import_catalog(
    file: UploadFile = File(...),
    dry_run: bool = True,
    session: Session = Depends(get_db),
) -> dict:
    """Importa el catálogo desde CSV (``departamento,categoria,subcategoria,tipos,ans_horas``).

    ``dry_run=true`` (por defecto) solo muestra lo que se crearía/actualizaría y los errores.
    """
    raw = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="El archivo supera el máximo de 2 MB")
    try:
        result = import_catalog_csv(session, raw)
    except CatalogImportError as exc:
        session.rollback()
        raise _unprocessable(str(exc)) from exc
    if dry_run:
        session.rollback()
    else:
        _commit(session, "Conflicto al guardar el catálogo (otra importación simultánea); reintente")
    return {"dry_run": dry_run, **result}


# --------------------------------------------------------------------------- perfil de usuarios


@router.patch("/users/{user_id}/profile", response_model=UserOut)
def update_user_profile(
    user_id: int, payload: UserProfilePatch, session: Session = Depends(get_db)
) -> dict:
    """Ajuste manual de VIP y departamento (el siguiente login con claim del AD lo vuelve a fijar)."""
    user = _get_or_404(session, User, user_id, "Usuario")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("vip") is not None:
        user.vip = changes["vip"]
    if "department_id" in changes:
        _require_exists(session, Department, changes["department_id"], "El departamento")
        user.department_id = changes["department_id"]
    session.commit()
    session.refresh(user)
    return user_out(user)
