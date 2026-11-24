"""API core: salud, identidad, catálogo, creación/edición de tickets, administración y seeds."""

from __future__ import annotations

import importlib

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models.core import Department, Team, Ticket, User, WorkflowState, WorkflowTransition
from app.seeds import run_all
from app.tasks import celery_app
from tests.conftest import DEPARTMENT_NETWORK, DEPARTMENT_SOC, DEPARTMENT_TELECOM, auth_headers


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}


def test_dev_mode_requires_user_header(client):
    response = client.get("/me")
    assert response.status_code == 401
    assert "X-Dev-User" in response.json()["detail"]
    assert client.get("/me", headers={"X-Dev-User": "mal usuario!"}).status_code == 401


def test_me_provisions_user(client, db, ids):
    headers = auth_headers("nuevo.usuario", "agent,approver,desconocido")
    headers["X-Dev-Name"] = "Nuevo Usuario"
    body = client.get("/me", headers=headers).json()
    assert body["username"] == "nuevo.usuario"
    assert body["sub"] == "dev:nuevo.usuario"
    assert body["display_name"] == "Nuevo Usuario"
    assert body["roles"] == ["agent", "approver", "requester"]
    assert body["teams"] == [] and body["department_ids"] == []
    user = db.scalar(select(User).where(User.keycloak_sub == "dev:nuevo.usuario"))
    assert user is not None and user.roles == ["agent", "approver", "requester"]
    assert user.last_login_at is not None
    # Peticiones posteriores no duplican el usuario.
    client.get("/me", headers=headers)
    assert db.scalar(select(func.count()).select_from(User)) == 1


def test_inactive_user_is_rejected(client, db, requester):
    user = db.get(User, requester.id)
    user.active = False
    db.commit()
    assert client.get("/me", headers=requester.headers).status_code == 403


def test_catalog(client, requester):
    body = client.get("/catalog", headers=requester.headers).json()
    assert {d["name"] for d in body["departments"]} >= {DEPARTMENT_TELECOM, DEPARTMENT_SOC, "General"}
    assert len(body["teams"]) == 6
    assert [p["key"] for p in body["priorities"]] == ["low", "medium", "high", "critical"]
    assert {t["key"] for t in body["ticket_types"]} == {"incident", "service_request", "change", "problem"}
    states = body["workflow"]["states"]
    assert [s["key"] for s in states] == [
        "new", "triage", "assigned", "in_progress", "pending", "resolved", "closed",
    ]
    assert [s["key"] for s in states if s["is_initial"]] == ["new"]
    assert [s["key"] for s in states if s["is_final"]] == ["closed"]
    pairs = {(t["from_key"], t["to_key"]) for t in body["workflow"]["transitions"]}
    assert ("resolved", "in_progress") in pairs and ("resolved", "closed") in pairs
    assert all(t["requires_approval"] is False for t in body["workflow"]["transitions"])


def test_create_ticket_validation(client, ids, requester):
    dept = ids["departments"][DEPARTMENT_NETWORK]
    ok = client.post(
        "/tickets",
        json={"title": "  VPN caída  ", "department_id": dept, "type_key": "service_request"},
        headers=requester.headers,
    )
    assert ok.status_code == 201
    body = ok.json()
    assert body["title"] == "VPN caída"
    assert body["type"]["key"] == "service_request"
    assert body["priority"]["key"] == "medium"
    assert body["team"]["id"] == ids["teams"][DEPARTMENT_NETWORK]
    assert body["requester"]["id"] == requester.id

    for payload in (
        {"title": "ab", "department_id": dept},
        {"title": "Válido", "department_id": 9999},
        {"title": "Válido", "department_id": dept, "priority_key": "urgente"},
        {"title": "Válido", "department_id": dept, "type_key": "otro"},
        {"title": "Válido", "department_id": dept, "project_id": 9999},
    ):
        assert client.post("/tickets", json=payload, headers=requester.headers).status_code == 422


def test_patch_validations(client, ids, admin, agent, requester, create_ticket):
    ticket = create_ticket(requester)
    url = f"/tickets/{ticket['id']}"
    # El asignado debe ser agente.
    assert client.patch(url, json={"assignee_id": requester.id}, headers=admin.headers).status_code == 422
    # El equipo debe pertenecer al departamento.
    soc_team = ids["teams"][DEPARTMENT_SOC]
    assert client.patch(url, json={"team_id": soc_team}, headers=admin.headers).status_code == 422
    # Cambiar de departamento reasigna el equipo por defecto del nuevo departamento.
    moved = client.patch(url, json={"department_id": ids["departments"][DEPARTMENT_SOC]}, headers=admin.headers)
    assert moved.status_code == 200
    assert moved.json()["team"]["id"] == soc_team
    # Campos desconocidos se rechazan.
    assert client.patch(url, json={"status": "closed"}, headers=admin.headers).status_code == 422
    # Desasignar con null.
    client.patch(url, json={"assignee_id": agent.id}, headers=admin.headers)
    cleared = client.patch(url, json={"assignee_id": None}, headers=admin.headers)
    assert cleared.json()["assignee"] is None


def test_stats(client, agent, requester, create_ticket, transition):
    first = create_ticket(requester, priority_key="high")
    create_ticket(requester)
    transition(agent, first["id"], "in_progress")
    body = client.get("/stats", headers=requester.headers).json()
    assert body["total"] == 2
    assert body["by_status"] == {"new": 1, "in_progress": 1}
    assert body["by_category"] == {"todo": 1, "in_progress": 1}
    assert body["by_priority"] == {"high": 1, "medium": 1}


def test_admin_requires_admin_role(client, agent):
    assert client.get("/admin/departments", headers=agent.headers).status_code == 403


def test_admin_departments_and_teams(client, admin, ids):
    created = client.post("/admin/departments", json={"name": "Infraestructura"}, headers=admin.headers)
    assert created.status_code == 201
    dept_id = created.json()["id"]
    assert client.post("/admin/departments", json={"name": "Infraestructura"}, headers=admin.headers).status_code == 409

    child = client.post(
        "/admin/departments", json={"name": "Centro de datos", "parent_id": dept_id}, headers=admin.headers
    ).json()
    cycle = client.patch(f"/admin/departments/{dept_id}", json={"parent_id": child["id"]}, headers=admin.headers)
    assert cycle.status_code == 422

    team = client.post(
        "/admin/teams",
        json={"department_id": dept_id, "name": "Equipo DC", "external_group": "GG-SD-DC"},
        headers=admin.headers,
    )
    assert team.status_code == 201
    team_id = team.json()["id"]
    renamed = client.patch(
        f"/admin/teams/{team_id}", json={"name": "Equipo Centro de Datos", "external_group": ""}, headers=admin.headers
    )
    assert renamed.json() == {
        "id": team_id, "department_id": dept_id, "name": "Equipo Centro de Datos", "external_group": None,
    }
    assert client.post(
        "/admin/teams", json={"department_id": 9999, "name": "Fantasma"}, headers=admin.headers
    ).status_code == 422


def test_admin_team_members(client, admin, agent, ids):
    team_id = ids["teams"][DEPARTMENT_SOC]
    url = f"/admin/teams/{team_id}/members"
    added = client.post(url, json={"user_id": agent.id, "role": "lead"}, headers=admin.headers)
    assert added.status_code == 201
    assert added.json()["role"] == "lead" and added.json()["username"] == "ana.telecom"
    assert client.post(url, json={"user_id": agent.id}, headers=admin.headers).status_code == 409
    assert client.post(url, json={"user_id": 9999}, headers=admin.headers).status_code == 422

    patched = client.patch(f"{url}/{agent.id}", json={"role": "member"}, headers=admin.headers)
    assert patched.json()["role"] == "member"
    assert [m["user_id"] for m in client.get(url, headers=admin.headers).json()] == [agent.id]
    me = client.get("/me", headers=agent.headers).json()
    assert team_id in [t["id"] for t in me["teams"]]

    assert client.delete(f"{url}/{agent.id}", headers=admin.headers).status_code == 204
    assert client.get(url, headers=admin.headers).json() == []
    assert client.delete(f"{url}/{agent.id}", headers=admin.headers).status_code == 404


def test_admin_projects(client, admin, ids, requester):
    created = client.post(
        "/admin/projects",
        json={"code": "red-2027", "name": "Renovación de red", "team_id": ids["teams"][DEPARTMENT_TELECOM]},
        headers=admin.headers,
    )
    assert created.status_code == 201
    project = created.json()
    assert project["code"] == "RED-2027"
    assert client.post(
        "/admin/projects", json={"code": "RED-2027", "name": "Duplicado"}, headers=admin.headers
    ).status_code == 409
    updated = client.patch(f"/admin/projects/{project['id']}", json={"owner_id": admin.id}, headers=admin.headers)
    assert updated.json()["owner_id"] == admin.id
    ticket = client.post(
        "/tickets",
        json={"title": "Tarea del proyecto", "department_id": ids["departments"][DEPARTMENT_TELECOM],
              "project_id": project["id"]},
        headers=requester.headers,
    ).json()
    assert ticket["project"] == {"id": project["id"], "code": "RED-2027", "name": "Renovación de red"}


def test_core_seeds_are_idempotent():
    def counts():
        with SessionLocal() as session:
            return tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (Department, Team, WorkflowState, WorkflowTransition)
            )

    before = counts()
    seed = importlib.import_module("app.seeds.00_core")
    for _ in range(2):
        with SessionLocal() as session:
            seed.run(session)
            session.commit()
    assert counts() == before == (6, 6, 7, 16)


def test_demo_seed(monkeypatch, client):
    from app.core.config import settings

    monkeypatch.setattr(settings, "seed_demo", True)
    executed = run_all()
    assert "app.seeds.00_core" in executed and "app.seeds.90_core_demo" in executed
    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(Ticket))
        assert total == 7
        closed = session.scalar(
            select(func.count()).select_from(Ticket).join(WorkflowState).where(WorkflowState.key == "closed")
        )
        assert closed == 1
    run_all()  # idempotente
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Ticket)) == 7
    # Los usuarios demo coinciden con los headers dev.
    body = client.get("/tickets", headers=auth_headers("ana.telecom", "agent")).json()
    assert body["total"] >= 2


def test_celery_app_registers_core_tasks():
    assert "core.ping" in celery_app.tasks
    assert isinstance(celery_app.conf.beat_schedule, dict)
    result = celery_app.tasks["core.ping"].apply()
    assert result.get()["status"] == "ok"


def test_requester_priority_cap_and_hourly_limit(client, ids, requester, agent, monkeypatch):
    """Anti-abuso: un solicitante no elige «Crítica» ni crea tickets sin límite."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "requester_max_priority", "high")
    monkeypatch.setattr(settings, "tickets_per_user_hour", 2)
    body = {"title": "Caída", "description": "x", "department_id": ids["departments"]["Telecomunicaciones"]}
    critical = client.post("/tickets", json={**body, "priority_key": "critical"}, headers=requester.headers)
    assert critical.status_code == 422
    assert "agentes" in critical.json()["detail"]
    assert client.post("/tickets", json={**body, "priority_key": "high"}, headers=requester.headers).status_code == 201
    assert client.post("/tickets", json=body, headers=requester.headers).status_code == 201
    limited = client.post("/tickets", json=body, headers=requester.headers)
    assert limited.status_code == 429 and limited.headers.get("retry-after")
    # Los agentes no tienen estos límites.
    as_agent = client.post("/tickets", json={**body, "priority_key": "critical"}, headers=agent.headers)
    assert as_agent.status_code == 201
