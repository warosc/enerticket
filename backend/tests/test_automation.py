"""Automatizaciones: reglas sembradas, acciones, anti-bucle, CRUD, dry-run y operadores."""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.automation import AutomationExecution, AutomationRule, Notification
from app.models.core import TicketComment, TicketType
from app.services.automation.engine import compare
from tests.conftest import DEPARTMENT_SOC, DEPARTMENT_TELECOM


@pytest.fixture(autouse=True)
def change_type_enabled():
    """Fase 3 desactiva el tipo «change» (``core.ticket_types.v2``); estas pruebas de flujos lo
    usan como tipo con aprobación, así que lo reactivan."""
    with SessionLocal() as session:
        session.scalar(select(TicketType).where(TicketType.key == "change")).active = True
        session.commit()


@pytest.fixture
def flows_seed():
    seed = importlib.import_module("app.seeds.20_flows")
    with SessionLocal() as session:
        seed.run(session)
        session.commit()


def _executions(rule_name: str | None = None) -> list[AutomationExecution]:
    with SessionLocal() as session:
        stmt = select(AutomationExecution).order_by(AutomationExecution.id)
        if rule_name:
            stmt = stmt.join(AutomationRule).where(AutomationRule.name == rule_name)
        return list(session.scalars(stmt).all())


def _create_rule(client, admin, **payload):
    body = {
        "name": payload.pop("name", "Regla de prueba"),
        "trigger": payload.pop("trigger", "ticket.created"),
        "conditions": payload.pop("conditions", {"match": "all", "rules": []}),
        "actions": payload.pop("actions"),
        **payload,
    }
    response = client.post("/automations/rules", json=body, headers=admin.headers)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- reglas sembradas


def test_soc_phishing_rule_raises_priority_and_comments(
    flows_seed, client, other_agent, requester, create_ticket
):
    ticket = create_ticket(
        requester, department=DEPARTMENT_SOC, title="Correo sospechoso", description="Posible PHISHING a finanzas"
    )
    assert ticket["priority"]["key"] == "high"  # la respuesta refleja el cambio de la regla
    with SessionLocal() as session:
        comments = session.scalars(select(TicketComment).where(TicketComment.ticket_id == ticket["id"])).all()
    assert len(comments) == 1 and comments[0].visibility == "internal"
    assert "phishing" in comments[0].body and "Equipo Ciberseguridad / SOC" in comments[0].body

    executions = _executions("SOC: posible phishing")
    assert len(executions) == 1
    assert executions[0].status == "success" and executions[0].depth == 0
    assert [r["type"] for r in executions[0].result] == ["set_priority", "assign_team", "add_comment"]

    history = client.get(f"/tickets/{ticket['id']}/history", headers=other_agent.headers).json()
    assert any(h["event_type"] == "ticket.updated" and h["actor"]["display_name"] == "Automatización" for h in history)

    # Otro departamento o texto sin la palabra: no aplica.
    other = create_ticket(requester, department=DEPARTMENT_TELECOM, description="phishing")
    assert other["priority"]["key"] == "medium"


def test_critical_ticket_notifies_department_agents(flows_seed, agent, make_user, requester, create_ticket):
    lead = make_user("jefe.telecom", roles=("agent",), departments=(DEPARTMENT_TELECOM,), team_role="lead")
    soc = make_user("luis.soc", roles=("agent",), departments=(DEPARTMENT_SOC,))
    ticket = create_ticket(requester, priority_key="critical", title="Caída total")
    with SessionLocal() as session:
        rows = session.scalars(
            select(Notification).where(Notification.kind == "automation", Notification.channel == "in_app")
        ).all()
    recipients = {n.user_id for n in rows}
    assert recipients == {agent.id, lead.id}
    assert soc.id not in recipients
    assert all(n.title == f"Ticket crítico {ticket['key']}: Caída total" for n in rows)


def test_escalation_rule_on_update(flows_seed, client, agent, requester, create_ticket):
    ticket = create_ticket(requester)
    assert _executions("Escalado a crítico: avisar a los agentes del departamento") == []
    client.patch(f"/tickets/{ticket['id']}", json={"priority_key": "critical"}, headers=agent.headers)
    executions = _executions("Escalado a crítico: avisar a los agentes del departamento")
    assert len(executions) == 1 and executions[0].status == "success"


# --------------------------------------------------------------------------- motor


def test_anti_loop_limits_depth_and_repetition(client, admin, agent, requester, create_ticket):
    _create_rule(
        client, admin, name="A: alta → baja", trigger="ticket.updated",
        conditions={"match": "all", "rules": [{"field": "ticket.priority", "op": "eq", "value": "high"}]},
        actions=[{"type": "set_priority", "priority_key": "low"}],
    )
    _create_rule(
        client, admin, name="B: baja → alta", trigger="ticket.updated",
        conditions={"match": "all", "rules": [{"field": "ticket.priority", "op": "eq", "value": "low"}]},
        actions=[{"type": "set_priority", "priority_key": "high"}],
    )
    ticket = create_ticket(requester)
    response = client.patch(f"/tickets/{ticket['id']}", json={"priority_key": "high"}, headers=agent.headers)
    assert response.status_code == 200
    executions = _executions()
    # Usuario (prof. 0) → A (prof. 0) → B (prof. 1) → el evento de prof. 2 ya no se evalúa.
    assert sorted(e.depth for e in executions) == [0, 1]  # la interna se registra primero
    assert len({e.rule_id for e in executions}) == 2
    assert response.json()["priority"]["key"] == "high"


def test_rule_does_not_fire_twice_in_same_chain(client, admin, agent, requester, create_ticket):
    _create_rule(
        client, admin, name="Comentar actualizaciones", trigger="ticket.updated",
        actions=[
            {"type": "set_priority", "priority_key": "low"},
            {"type": "add_comment", "body": "Actualizado {{ticket.key}}", "visibility": "internal"},
        ],
    )
    ticket = create_ticket(requester)
    client.patch(f"/tickets/{ticket['id']}", json={"title": "Nuevo título"}, headers=agent.headers)
    assert len(_executions()) == 1
    with SessionLocal() as session:
        bodies = session.scalars(select(TicketComment.body)).all()
    assert bodies == [f"Actualizado {ticket['key']}"]


def test_failed_action_is_recorded_without_breaking_request(client, admin, requester, create_ticket):
    _create_rule(
        client, admin, name="Transición imposible",
        actions=[
            {"type": "transition", "to_state": "in_progress"},  # new → in_progress sí existe
            {"type": "transition", "to_state": "triage"},  # in_progress → triage no existe
        ],
    )
    ticket = create_ticket(requester)
    assert ticket["status"]["key"] == "in_progress"
    execution = _executions()[0]
    assert execution.status == "partial"
    assert execution.result[0]["ok"] is True and execution.result[1]["ok"] is False
    assert "no permitida" in (execution.error or "")


def test_stop_processing(client, admin, requester, create_ticket):
    _create_rule(
        client, admin, name="Primera", order=1, stop_processing=True,
        actions=[{"type": "set_priority", "priority_key": "low"}],
    )
    _create_rule(client, admin, name="Segunda", order=2, actions=[{"type": "set_priority", "priority_key": "critical"}])
    ticket = create_ticket(requester)
    assert ticket["priority"]["key"] == "low"
    assert [e.rule_id for e in _executions()] == [_executions("Primera")[0].rule_id]


def test_transition_action_requests_approval_when_required(
    flows_seed, client, admin, make_user, approver, requester, create_ticket
):
    make_user("jefe.telecom", roles=("agent",), departments=(DEPARTMENT_TELECOM,), team_role="lead")
    _create_rule(
        client, admin, name="Iniciar cambios",
        conditions={"match": "all", "rules": [{"field": "ticket.type", "op": "eq", "value": "change"}]},
        actions=[{"type": "transition", "to_state": "in_progress"}],
    )
    ticket = create_ticket(requester, type_key="change")
    assert ticket["status"]["key"] == "new"
    assert ticket["approvals"] == {"pending": 1}
    execution = _executions("Iniciar cambios")[0]
    assert execution.status == "success"
    assert "solicitud de aprobación" in execution.result[0]["detail"]


def test_assign_and_notify_actions(client, admin, agent, requester, create_ticket):
    _create_rule(
        client, admin, name="Asignar y avisar",
        actions=[
            {"type": "assign_user", "user_id": agent.id},
            {"type": "notify", "targets": ["requester"], "title": "Recibimos {{ticket.key}}", "body": "Asignado a {{ticket.assignee}}"},
        ],
    )
    ticket = create_ticket(requester)
    assert ticket["assignee"]["id"] == agent.id
    with SessionLocal() as session:
        mine = session.scalar(
            select(Notification).where(Notification.user_id == requester.id, Notification.kind == "automation")
        )
        assigned = session.scalar(
            select(Notification).where(Notification.user_id == agent.id, Notification.kind == "ticket_assigned")
        )
    assert mine is not None and mine.title == f"Recibimos {ticket['key']}"
    assert agent.username.replace(".", " ").title() in mine.body
    assert assigned is not None  # la asignación automática también notifica al agente


def test_inactive_rules_are_ignored(client, admin, requester, create_ticket):
    rule = _create_rule(client, admin, actions=[{"type": "set_priority", "priority_key": "low"}])
    client.patch(f"/automations/rules/{rule['id']}", json={"active": False}, headers=admin.headers)
    ticket = create_ticket(requester)
    assert ticket["priority"]["key"] == "medium"
    assert _executions() == []


# --------------------------------------------------------------------------- API


def test_rules_api_requires_admin_and_validates(client, admin, agent, requester):
    assert client.get("/automations/rules", headers=agent.headers).status_code == 403
    assert client.get("/automations/meta", headers=agent.headers).status_code == 403
    meta = client.get("/automations/meta", headers=admin.headers).json()
    assert {"ticket.created", "sla.breached"} <= {t["key"] for t in meta["triggers"]}

    def post(body):
        return client.post("/automations/rules", json=body, headers=admin.headers)

    base = {"name": "x", "trigger": "ticket.created", "actions": [{"type": "set_priority", "priority_key": "high"}]}
    assert post({**base, "trigger": "ticket.unknown"}).status_code == 422
    assert post({**base, "actions": []}).status_code == 422
    assert post({**base, "actions": [{"type": "assign_user", "user_id": requester.id}]}).status_code == 422
    assert post({**base, "actions": [{"type": "set_priority", "priority_key": "urgentisima"}]}).status_code == 422
    assert post({**base, "actions": [{"type": "notify", "targets": ["everyone"], "title": "t"}]}).status_code == 422
    assert post({**base, "conditions": {"rules": [{"field": "ticket.secret", "op": "eq", "value": 1}]}}).status_code == 422

    created = post(base)
    assert created.status_code == 201
    rule = created.json()
    assert rule["actions"] == [{"type": "set_priority", "priority_key": "high"}]
    patched = client.patch(
        f"/automations/rules/{rule['id']}", json={"name": "Renombrada", "order": 5}, headers=admin.headers
    ).json()
    assert patched["name"] == "Renombrada" and patched["order"] == 5
    assert client.delete(f"/automations/rules/{rule['id']}", headers=admin.headers).status_code == 204
    assert client.get(f"/automations/rules/{rule['id']}", headers=admin.headers).status_code == 404


def test_dry_run_has_no_side_effects(client, admin, agent, requester, create_ticket):
    ticket = create_ticket(requester, description="Alerta de phishing")
    rule = _create_rule(
        client, admin, active=False,
        conditions={"match": "all", "rules": [{"field": "ticket.text", "op": "contains", "value": "PHISHING"}]},
        actions=[
            {"type": "set_priority", "priority_key": "critical"},
            {"type": "notify", "targets": ["requester"], "title": "Hola {{ticket.key}}"},
        ],
    )
    result = client.post(
        f"/automations/rules/{rule['id']}/dry-run", json={"ticket_id": ticket["id"]}, headers=admin.headers
    ).json()
    assert result["matched"] is True
    assert result["conditions"][0]["result"] is True
    assert result["actions"][1]["rendered_title"] == f"Hola {ticket['key']}"
    assert result["actions"][1]["recipient_ids"] == [requester.id]

    draft = client.post(
        "/automations/dry-run",
        json={
            "ticket_id": ticket["id"],
            "rule": {
                "name": "Borrador",
                "trigger": "ticket.transitioned",
                "conditions": {"match": "any", "rules": [{"field": "event.to_state", "op": "eq", "value": "resolved"}]},
                "actions": [{"type": "add_comment", "body": "x"}],
            },
            "event_data": {"to_state": "pending"},
        },
        headers=admin.headers,
    ).json()
    assert draft["matched"] is False

    after = client.get(f"/tickets/{ticket['id']}", headers=agent.headers).json()
    assert after["priority"]["key"] == "medium"
    assert _executions() == []
    missing = client.post(f"/automations/rules/{rule['id']}/dry-run", json={"ticket_id": 9999}, headers=admin.headers)
    assert missing.status_code == 404


def test_executions_endpoint(flows_seed, client, admin, requester, create_ticket):
    create_ticket(requester, department=DEPARTMENT_SOC, description="phishing")
    page = client.get("/automations/executions", headers=admin.headers).json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["rule"]["name"] == "SOC: posible phishing" and item["ticket"]["key"].startswith("ESD-")
    assert client.get("/automations/executions", params={"status": "error"}, headers=admin.headers).json()["total"] == 0
    rules = client.get("/automations/rules", headers=admin.headers).json()
    soc_rule = next(r for r in rules if r["name"] == "SOC: posible phishing")
    assert soc_rule["execution_count"] == 1 and soc_rule["last_execution_at"] is not None


# --------------------------------------------------------------------------- operadores


@pytest.mark.parametrize(
    ("actual", "op", "expected", "result"),
    [
        ("Critical", "eq", "critical", True),
        (3, "eq", "3", True),
        (None, "eq", "x", False),
        ("high", "neq", "low", True),
        ("high", "in", ["low", "high"], True),
        ("high", "in", "low, high", True),
        ("medium", "not_in", ["low", "high"], True),
        ("Alerta de PHISHING", "contains", "phishing", True),
        ("Todo bien", "not_contains", "phishing", True),
        ("ESD-12", "starts_with", "esd-", True),
        (2, "lt", 3, True),
        (4, "gte", "4", True),
        ("abc", "gt", 1, False),
        (None, "is_empty", None, True),
        ("  ", "is_empty", None, True),
        ("x", "is_not_empty", None, True),
    ],
)
def test_compare_operators(actual, op, expected, result):
    assert compare(actual, op, expected) is result


def test_automation_actions_do_not_count_as_first_response(client, admin, agent, requester, create_ticket):
    """Un acuse automático o una transición de una regla no cumplen la primera respuesta del SLA."""
    _create_rule(
        client,
        admin,
        name="Acuse y triaje automático",
        actions=[
            {"type": "add_comment", "body": "Recibimos su solicitud {{ticket.key}}", "visibility": "public"},
            {"type": "transition", "to_state": "triage"},
        ],
    )
    ticket = create_ticket(requester)
    detail = client.get(f"/tickets/{ticket['id']}", headers=agent.headers).json()
    assert detail["status"]["key"] == "triage"
    comments = client.get(f"/tickets/{ticket['id']}/comments", headers=requester.headers).json()
    assert [c["author"]["display_name"] for c in comments] == ["Automatización"]
    assert detail["first_response_at"] is None

    client.post(f"/tickets/{ticket['id']}/comments", json={"body": "Lo reviso", "visibility": "public"}, headers=agent.headers)
    after = client.get(f"/tickets/{ticket['id']}", headers=agent.headers).json()
    assert after["first_response_at"] is not None


def test_transition_action_resolves_with_solution_template(client, admin, requester, create_ticket):
    """Resolver exige solución: la acción de cambio de estado la toma de ``body`` (plantilla)."""
    _create_rule(
        client, admin, name="Sin solución",
        conditions={"match": "all", "rules": [{"field": "ticket.title", "op": "contains", "value": "sin-nota"}]},
        actions=[
            {"type": "transition", "to_state": "in_progress"},
            {"type": "transition", "to_state": "resolved"},
        ],
    )
    _create_rule(
        client, admin, name="Con solución",
        conditions={"match": "all", "rules": [{"field": "ticket.title", "op": "contains", "value": "con-nota"}]},
        actions=[
            {"type": "transition", "to_state": "in_progress"},
            {"type": "transition", "to_state": "resolved", "body": "Cerrado por regla {{ticket.key}}"},
        ],
    )
    without = create_ticket(requester, title="Ticket sin-nota")
    assert without["status"]["key"] == "in_progress"
    assert "solución" in (_executions("Sin solución")[0].error or "")

    with_note = create_ticket(requester, title="Ticket con-nota")
    assert with_note["status"]["key"] == "resolved"
    assert with_note["resolution_note"] == f"Cerrado por regla {with_note['key']}"
    assert _executions("Con solución")[0].status == "success"
    rule = client.get("/automations/rules", headers=admin.headers).json()
    stored = next(r for r in rule if r["name"] == "Con solución")
    assert stored["actions"][1]["body"].startswith("Cerrado por regla")
