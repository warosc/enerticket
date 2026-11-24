"""Fixtures compartidas de pruebas.

- Base de datos SQLite en memoria (``DATABASE_URL=sqlite://``) recreada con ``create_all``
  y el seed core antes de cada prueba.
- ``AUTH_MODE=dev``: la identidad se envía con headers ``X-Dev-User``/``X-Dev-Roles``/``X-Dev-Groups``.
- Los eventos publicados se capturan en memoria (fixture ``published_events``); no se usa Redis.
- ``EVENTS_RAISE_ERRORS=true``: un error en un suscriptor hace fallar la prueba.

Nota: la conexión SQLite en memoria es única y compartida. Las sesiones propias de una
prueba deben confirmar (``commit``) sus escrituras antes de llamar a la API.
"""

from __future__ import annotations

import os

os.environ.update(
    {
        "AUTH_MODE": "dev",
        # TEST_DATABASE_URL permite correr la suite contra PostgreSQL (job de CI).
        "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", "sqlite://"),
        "SEED_DEMO": "false",
        "EVENTS_RAISE_ERRORS": "true",
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "LOG_LEVEL": "WARNING",
        # Límites anti-abuso desactivados por defecto en las pruebas (tienen pruebas propias).
        "REQUESTER_MAX_PRIORITY": "",
        "TICKETS_PER_USER_HOUR": "0",
    }
)

import copy  # noqa: E402
import importlib  # noqa: E402
import json  # noqa: E402
from collections.abc import Callable, Iterator  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import enrich, events, permissions  # noqa: E402
from app.core.db import Base, SessionLocal, engine, utcnow  # noqa: E402
from app.main import app  # noqa: E402
from app.models.core import Department, Team, TeamMember, User  # noqa: E402

DEPARTMENT_TELECOM = "Telecomunicaciones"
DEPARTMENT_SOC = "Ciberseguridad / SOC"
DEPARTMENT_NETWORK = "Seguridad de Redes"


def auth_headers(
    username: str, roles: str | list[str] = "requester", groups: str | list[str] | None = None
) -> dict[str, str]:
    """Headers de autenticación dev. ``groups=None`` no envía X-Dev-Groups (sin sincronizar)."""
    headers = {
        "X-Dev-User": username,
        "X-Dev-Roles": roles if isinstance(roles, str) else ",".join(roles),
    }
    if groups is not None:
        headers["X-Dev-Groups"] = groups if isinstance(groups, str) else ",".join(groups)
    return headers


@dataclass
class Persona:
    """Usuario de prueba ya provisionado en BD, con sus headers dev."""

    id: int
    username: str
    roles: tuple[str, ...]
    headers: dict[str, str]


@pytest.fixture(autouse=True)
def database() -> Iterator[None]:
    """Recrea el esquema y aplica el seed core en cada prueba."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    seed_core = importlib.import_module("app.seeds.00_core")
    with SessionLocal() as session:
        seed_core.run(session)
        session.commit()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def isolate_registries() -> Iterator[None]:
    """Restaura suscriptores, guards, extensiones y enriquecedores tras cada prueba."""
    subscribers = copy.copy({k: list(v) for k, v in events._subscribers.items()})
    guards = {k: list(v) for k, v in events._guards.items()}
    extensions = list(permissions._visibility_extensions)
    enrichers = list(enrich._enrichers)
    yield
    events._subscribers.clear()
    events._subscribers.update(subscribers)
    events._guards.clear()
    events._guards.update(guards)
    permissions._visibility_extensions[:] = extensions
    enrich._enrichers[:] = enrichers


@pytest.fixture(autouse=True)
def published_events() -> Iterator[list[dict[str, Any]]]:
    """Lista de payloads publicados (en orden) tras cada commit."""
    captured: list[dict[str, Any]] = []
    events.set_publisher(lambda channel, message: captured.append(json.loads(message)))
    yield captured
    events.set_publisher(None)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db() -> Iterator[Session]:
    """Sesión para preparar/verificar datos. Confirmar escrituras antes de usar la API."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def ids() -> dict[str, Any]:
    """IDs del catálogo sembrado: departamentos y equipo por defecto de cada uno."""
    with SessionLocal() as session:
        departments = {d.name: d.id for d in session.scalars(select(Department)).all()}
        teams = {
            name: session.scalar(
                select(Team.id).where(Team.department_id == dept_id).order_by(Team.id)
            )
            for name, dept_id in departments.items()
        }
    return {"departments": departments, "teams": teams}


@pytest.fixture
def make_user() -> Callable[..., Persona]:
    """Fábrica: ``make_user("ana", roles=("agent",), departments=("Telecomunicaciones",))``.

    Crea el usuario (sub ``dev:<username>``) y su pertenencia al equipo por defecto de
    cada departamento indicado; devuelve una ``Persona`` con headers dev listos.
    """

    def factory(
        username: str,
        roles: tuple[str, ...] | list[str] = ("requester",),
        departments: tuple[str, ...] | list[str] = (),
        team_role: str = "member",
        groups: str | list[str] | None = None,
    ) -> Persona:
        role_set = sorted(set(roles) | {"requester"})
        with SessionLocal() as session:
            user = session.scalar(select(User).where(User.keycloak_sub == f"dev:{username}"))
            if user is None:
                user = User(
                    keycloak_sub=f"dev:{username}",
                    username=username,
                    display_name=username.replace(".", " ").title(),
                    email=f"{username}@example.test",
                    roles=role_set,
                    active=True,
                    created_at=utcnow(),
                )
                session.add(user)
                session.flush()
            for department_name in departments:
                team_id = session.scalar(
                    select(Team.id)
                    .join(Department, Department.id == Team.department_id)
                    .where(Department.name == department_name)
                    .order_by(Team.id)
                )
                assert team_id is not None, f"Departamento sin equipo: {department_name}"
                if session.get(TeamMember, (team_id, user.id)) is None:
                    session.add(TeamMember(team_id=team_id, user_id=user.id, role=team_role))
            session.commit()
            user_id = user.id
        return Persona(
            id=user_id,
            username=username,
            roles=tuple(role_set),
            headers=auth_headers(username, role_set, groups),
        )

    return factory


@pytest.fixture
def admin(make_user) -> Persona:
    return make_user("admin.user", roles=("admin",))


@pytest.fixture
def agent(make_user) -> Persona:
    """Agente del equipo de Telecomunicaciones."""
    return make_user("ana.telecom", roles=("agent",), departments=(DEPARTMENT_TELECOM,))


@pytest.fixture
def other_agent(make_user) -> Persona:
    """Agente del equipo de Ciberseguridad / SOC (otra área)."""
    return make_user("luis.soc", roles=("agent",), departments=(DEPARTMENT_SOC,))


@pytest.fixture
def requester(make_user) -> Persona:
    return make_user("carlos.solicitante")


@pytest.fixture
def other_requester(make_user) -> Persona:
    return make_user("sofia.solicitante")


@pytest.fixture
def approver(make_user) -> Persona:
    return make_user("marta.aprobadora", roles=("approver",))


@pytest.fixture
def create_ticket(client: TestClient, ids: dict[str, Any]) -> Callable[..., dict[str, Any]]:
    """Fábrica: crea un ticket vía API y devuelve el TicketOut (falla si no es 201)."""

    def factory(
        persona: Persona, department: str = DEPARTMENT_TELECOM, **payload: Any
    ) -> dict[str, Any]:
        body = {
            "title": payload.pop("title", "Falla de enlace en agencia"),
            "description": payload.pop("description", "Detalle de prueba"),
            "department_id": ids["departments"][department],
            **payload,
        }
        response = client.post("/tickets", json=body, headers=persona.headers)
        assert response.status_code == 201, response.text
        return response.json()

    return factory


# Estados de categoría ``done`` del flujo por defecto: resolver exige solución (Fase 3).
DONE_STATES = frozenset({"resolved", "closed"})
DEFAULT_RESOLUTION_NOTE = "Solución aplicada (prueba)"
_AUTO = object()


@pytest.fixture
def transition(client: TestClient) -> Callable[..., Any]:
    """Fábrica: ``transition(persona, ticket_id, "in_progress", comment=None)`` → Response.

    Al pasar a un estado ``done`` envía una ``resolution_note`` por defecto; con
    ``resolution_note=None`` no la envía (para probar la validación) y con un texto usa ese.
    """

    def factory(
        persona: Persona,
        ticket_id: int,
        to_state: str,
        comment: str | None = None,
        resolution_note: Any = _AUTO,
    ):
        body: dict[str, Any] = {"to_state_key": to_state}
        if comment is not None:
            body["comment"] = comment
        if resolution_note is _AUTO:
            if to_state in DONE_STATES:
                body["resolution_note"] = DEFAULT_RESOLUTION_NOTE
        elif resolution_note is not None:
            body["resolution_note"] = resolution_note
        return client.post(f"/tickets/{ticket_id}/transition", json=body, headers=persona.headers)

    return factory
