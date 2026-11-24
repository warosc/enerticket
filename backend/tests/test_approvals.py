"""Aprobaciones: guard 409, etapas múltiples, rechazo, visibilidad del aprobador y políticas."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.core.db import Base, SessionLocal
from app.models.approvals import ApprovalDecision, ApprovalRequest
from app.models.automation import Notification
from app.models.core import TicketType, WorkflowState, WorkflowTransition
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


@pytest.fixture
def lead(make_user):
    """Líder del equipo de Telecomunicaciones (aprobador de la etapa 1 de cambios)."""
    return make_user("jefe.telecom", roles=("agent",), departments=(DEPARTMENT_TELECOM,), team_role="lead")


@pytest.fixture
def change_ticket(flows_seed, create_ticket, requester):
    return create_ticket(requester, type_key="change", title="Cambio de firmware en core")


def _transition_id(source: str, target: str) -> int:
    with SessionLocal() as session:
        states = {s.key: s.id for s in session.scalars(select(WorkflowState)).all()}
        return session.scalar(
            select(WorkflowTransition.id).where(
                WorkflowTransition.from_state_id == states[source],
                WorkflowTransition.to_state_id == states[target],
            )
        )


def _request_approval(transition, agent, ticket_id):
    response = transition(agent, ticket_id, "in_progress", comment="Ventana del sábado")
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "approval_required"
    return detail


# --------------------------------------------------------------------------- guard


def test_transition_requiring_approval_returns_409_and_creates_request(
    client, agent, lead, approver, change_ticket, transition, published_events
):
    published_events.clear()
    detail = _request_approval(transition, agent, change_ticket["id"])
    assert detail["created"] is True
    assert "requiere aprobación" in detail["message"]
    request_id = detail["approval_request_id"]

    # El ticket no cambió de estado, pero la solicitud persiste (se confirmó antes del 409).
    ticket = client.get(f"/tickets/{change_ticket['id']}", headers=agent.headers).json()
    assert ticket["status"]["key"] == "new"
    assert ticket["approvals"] == {"pending": 1}
    assert "approval.requested" in [p["type"] for p in published_events]

    approvals = client.get(f"/tickets/{change_ticket['id']}/approvals", headers=agent.headers).json()
    assert len(approvals) == 1
    request = approvals[0]
    assert request["id"] == request_id
    assert request["status"] == "pending"
    assert request["reason"] == "Ventana del sábado"
    assert request["requested_by"]["id"] == agent.id
    assert [s["status"] for s in request["steps"]] == ["pending", "waiting"]
    assert [d["approver"]["id"] for d in request["steps"][0]["decisions"]] == [lead.id]
    assert [d["approver"]["id"] for d in request["steps"][1]["decisions"]] == [approver.id]
    assert request["can_decide"] is False and request["can_cancel"] is True

    # Reintentar reutiliza la solicitud pendiente.
    again = _request_approval(transition, agent, change_ticket["id"])
    assert again["approval_request_id"] == request_id and again["created"] is False
    with SessionLocal() as session:
        assert session.scalar(select(sa.func.count()).select_from(ApprovalRequest)) == 1


def test_policy_filters_by_ticket_type(flows_seed, client, agent, requester, create_ticket, transition):
    incident = create_ticket(requester, type_key="incident")
    response = transition(agent, incident["id"], "in_progress")
    assert response.status_code == 200, response.text
    assert response.json()["status"]["key"] == "in_progress"


# --------------------------------------------------------------------------- decisiones


def test_multi_step_approval_executes_transition(
    client, agent, lead, approver, requester, change_ticket, transition, published_events
):
    request_id = _request_approval(transition, agent, change_ticket["id"])["approval_request_id"]

    # El aprobador de la etapa 2 aún no puede decidir (ni ve el ticket: su etapa está en espera).
    early = client.post(f"/approvals/{request_id}/decide", json={"decision": "approve"}, headers=approver.headers)
    assert early.status_code == 404

    pending_lead = client.get("/approvals/pending", headers=lead.headers).json()
    assert [r["id"] for r in pending_lead] == [request_id]
    assert pending_lead[0]["can_decide"] is True
    assert client.get("/approvals/pending", headers=approver.headers).json() == []

    step1 = client.post(
        f"/approvals/{request_id}/decide",
        json={"decision": "approve", "comment": "Riesgo bajo"},
        headers=lead.headers,
    )
    assert step1.status_code == 200, step1.text
    body = step1.json()
    assert body["status"] == "pending" and body["current_step"] == 2
    assert [s["status"] for s in body["steps"]] == ["approved", "pending"]

    # Doble decisión del mismo aprobador: conflicto (la etapa ya avanzó).
    dup = client.post(f"/approvals/{request_id}/decide", json={"decision": "approve"}, headers=lead.headers)
    assert dup.status_code == 403

    assert [r["id"] for r in client.get("/approvals/pending", headers=approver.headers).json()] == [request_id]
    published_events.clear()
    step2 = client.post(f"/approvals/{request_id}/decide", json={"decision": "approved"}, headers=approver.headers)
    assert step2.status_code == 200, step2.text
    final = step2.json()
    assert final["status"] == "approved"
    assert final["executed_at"] is not None and final["execution_error"] is None

    ticket = client.get(f"/tickets/{change_ticket['id']}", headers=agent.headers).json()
    assert ticket["status"]["key"] == "in_progress"
    assert ticket["approvals"] == {"pending": 0}
    types = [p["type"] for p in published_events]
    assert types.index("approval.decided") < types.index("ticket.transitioned")
    transitioned = next(p for p in published_events if p["type"] == "ticket.transitioned")
    assert transitioned["actor_id"] == agent.id  # se ejecuta en nombre de quien la solicitó

    # El solicitante de la transición recibe la notificación de la decisión.
    with SessionLocal() as session:
        kinds = session.scalars(
            select(Notification.kind).where(Notification.user_id == agent.id, Notification.channel == "in_app")
        ).all()
    assert "approval_decided" in kinds

    # Una solicitud ya resuelta no admite más decisiones.
    late = client.post(f"/approvals/{request_id}/decide", json={"decision": "approve"}, headers=approver.headers)
    assert late.status_code == 409


def test_rejection_requires_reason_and_blocks(
    client, agent, lead, approver, change_ticket, transition
):
    request_id = _request_approval(transition, agent, change_ticket["id"])["approval_request_id"]
    no_reason = client.post(f"/approvals/{request_id}/decide", json={"decision": "reject"}, headers=lead.headers)
    assert no_reason.status_code == 422

    rejected = client.post(
        f"/approvals/{request_id}/decide",
        json={"decision": "reject", "comment": "Falta plan de reversa"},
        headers=lead.headers,
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "rejected"
    assert [s["status"] for s in body["steps"]] == ["rejected", "cancelled"]
    assert body["steps"][1]["decisions"][0]["decision"] == "skipped"

    ticket = client.get(f"/tickets/{change_ticket['id']}", headers=agent.headers).json()
    assert ticket["status"]["key"] == "new"
    with SessionLocal() as session:
        notification = session.scalar(
            select(Notification).where(
                Notification.user_id == agent.id, Notification.kind == "approval_decided"
            )
        )
        assert notification is not None and "rechazada" in notification.title
        assert "Falta plan de reversa" in notification.body

    # Un nuevo intento abre una solicitud nueva.
    retry = _request_approval(transition, agent, change_ticket["id"])
    assert retry["created"] is True and retry["approval_request_id"] != request_id


def test_rule_all_requires_every_approver(
    flows_seed, client, admin, agent, approver, make_user, requester, create_ticket, transition
):
    second = make_user("pedro.aprobador", roles=("approver",))
    transition_id = _transition_id("triage", "assigned")
    created = client.post(
        "/approvals/policies",
        json={
            "name": "Asignación de solicitudes",
            "transition_id": transition_id,
            "ticket_type_keys": ["service_request"],
            "steps": [
                {"name": "Todos los aprobadores", "approver_type": "role", "role": "approver", "scope": "global", "rule": "all"}
            ],
        },
        headers=admin.headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["transition"]["requires_approval"] is True

    ticket = create_ticket(requester, type_key="service_request")
    assert transition(agent, ticket["id"], "triage").status_code == 200
    response = transition(agent, ticket["id"], "assigned")
    assert response.status_code == 409
    request_id = response.json()["detail"]["approval_request_id"]

    first = client.post(f"/approvals/{request_id}/decide", json={"decision": "approve"}, headers=approver.headers)
    assert first.json()["status"] == "pending"
    second_decision = client.post(f"/approvals/{request_id}/decide", json={"decision": "approve"}, headers=second.headers)
    assert second_decision.json()["status"] == "approved"
    ticket_after = client.get(f"/tickets/{ticket['id']}", headers=agent.headers).json()
    assert ticket_after["status"]["key"] == "assigned"


def test_admin_override_completes_step(client, admin, agent, lead, change_ticket, transition):
    request_id = _request_approval(transition, agent, change_ticket["id"])["approval_request_id"]
    response = client.post(
        f"/approvals/{request_id}/decide", json={"decision": "approve", "comment": "Urgente"}, headers=admin.headers
    )
    assert response.status_code == 200
    step = response.json()["steps"][0]
    assert step["status"] == "approved"
    override = [d for d in step["decisions"] if d["approver"]["id"] == admin.id]
    assert override and override[0]["is_override"] is True
    lead_row = [d for d in step["decisions"] if d["approver"]["id"] == lead.id]
    assert lead_row[0]["decision"] == "skipped"


def test_requester_of_transition_is_not_approver(flows_seed, client, make_user, requester, create_ticket, transition):
    """Segregación de funciones: el líder que solicita no se aprueba a sí mismo."""
    boss = make_user("jefe.telecom", roles=("agent",), departments=(DEPARTMENT_TELECOM,), team_role="lead")
    admin_user = make_user("root.admin", roles=("admin",))
    ticket = create_ticket(requester, type_key="change")
    response = transition(boss, ticket["id"], "in_progress")
    assert response.status_code == 409
    approvals = client.get(f"/tickets/{ticket['id']}/approvals", headers=boss.headers).json()
    approvers = [d["approver"]["id"] for d in approvals[0]["steps"][0]["decisions"]]
    assert boss.id not in approvers
    assert approvers == [admin_user.id]  # sin otros líderes ni aprobadores: administradores


# --------------------------------------------------------------------------- visibilidad y permisos


def test_approver_sees_tickets_with_assigned_steps(
    client, agent, lead, approver, make_user, change_ticket, transition, create_ticket, requester
):
    assert client.get(f"/tickets/{change_ticket['id']}", headers=approver.headers).status_code == 404

    request_id = _request_approval(transition, agent, change_ticket["id"])["approval_request_id"]
    # Aprobador creado después: los aprobadores se fijan al abrir la solicitud.
    outsider = make_user("pedro.aprobador", roles=("approver",))

    # Aprobador de la etapa 2: mientras su etapa espera NO ve el ticket.
    assert client.get(f"/tickets/{change_ticket['id']}", headers=approver.headers).status_code == 404
    assert client.get("/approvals", params={"scope": "mine"}, headers=approver.headers).json()["total"] == 0
    step1 = client.post(f"/approvals/{request_id}/decide", json={"decision": "approve"}, headers=lead.headers)
    assert step1.status_code == 200, step1.text

    # Al activarse su etapa ya ve el ticket y la solicitud.
    assert client.get(f"/tickets/{change_ticket['id']}", headers=approver.headers).status_code == 200
    listed = client.get("/tickets", headers=approver.headers).json()
    assert [t["id"] for t in listed["items"]] == [change_ticket["id"]]
    assert client.get(f"/approvals/{request_id}", headers=approver.headers).status_code == 200

    # Otro aprobador sin etapas no ve nada.
    assert client.get(f"/tickets/{change_ticket['id']}", headers=outsider.headers).status_code == 404
    assert client.get(f"/approvals/{request_id}", headers=outsider.headers).status_code == 404
    decide = client.post(f"/approvals/{request_id}/decide", json={"decision": "approve"}, headers=outsider.headers)
    assert decide.status_code == 404

    # Agente de otra área tampoco.
    other = make_user("luis.soc", roles=("agent",), departments=(DEPARTMENT_SOC,))
    assert client.get(f"/tickets/{change_ticket['id']}/approvals", headers=other.headers).status_code == 404


def test_cancel_on_state_change_and_manual_cancel(
    client, agent, lead, approver, requester, change_ticket, transition
):
    request_id = _request_approval(transition, agent, change_ticket["id"])["approval_request_id"]
    moved = transition(agent, change_ticket["id"], "triage")
    assert moved.status_code == 200
    request = client.get(f"/approvals/{request_id}", headers=agent.headers).json()
    assert request["status"] == "cancelled"
    assert client.get("/approvals/pending", headers=lead.headers).json() == []

    # Cancelación manual por quien solicitó; el solicitante del ticket no puede.
    second = transition(agent, change_ticket["id"], "in_progress")
    second_id = second.json()["detail"]["approval_request_id"]
    denied = client.post(f"/approvals/{second_id}/cancel", json={}, headers=requester.headers)
    assert denied.status_code == 403
    ok = client.post(f"/approvals/{second_id}/cancel", json={"reason": "Se reprograma"}, headers=agent.headers)
    assert ok.status_code == 200 and ok.json()["status"] == "cancelled"
    again = client.post(f"/approvals/{second_id}/cancel", json={}, headers=agent.headers)
    assert again.status_code == 409


def test_default_steps_when_transition_requires_approval_without_policy(
    client, admin, agent, make_user, requester, create_ticket, transition
):
    dept_approver = make_user("vera.aprobadora", roles=("approver",), departments=(DEPARTMENT_TELECOM,))
    make_user("pedro.aprobador", roles=("approver",))  # global: no aplica si hay del departamento
    transition_id = _transition_id("new", "triage")
    toggled = client.put(
        f"/approvals/transitions/{transition_id}", json={"requires_approval": True}, headers=admin.headers
    )
    assert toggled.status_code == 200 and toggled.json()["requires_approval"] is True

    ticket = create_ticket(requester)
    response = transition(agent, ticket["id"], "triage")
    assert response.status_code == 409
    request = client.get(f"/tickets/{ticket['id']}/approvals", headers=agent.headers).json()[0]
    assert request["steps"][0]["name"] == "Aprobación del departamento"
    assert [d["approver"]["id"] for d in request["steps"][0]["decisions"]] == [dept_approver.id]
    assert "Telecomunicaciones" in request["steps"][0]["approver_label"]


def test_policy_endpoints_are_admin_only_and_validated(flows_seed, client, admin, agent):
    assert client.get("/approvals/policies", headers=agent.headers).status_code == 403
    policies = client.get("/approvals/policies", headers=admin.headers).json()
    assert len(policies) == 3 and all(p["ticket_type_keys"] == ["change"] for p in policies)

    bad = client.post(
        "/approvals/policies",
        json={"name": "x", "transition_id": policies[0]["transition_id"], "steps": [{"name": "s", "approver_type": "user"}]},
        headers=admin.headers,
    )
    assert bad.status_code == 422
    unknown_type = client.post(
        "/approvals/policies",
        json={
            "name": "x",
            "transition_id": policies[0]["transition_id"],
            "ticket_type_keys": ["nope"],
            "steps": [{"name": "s", "approver_type": "role", "role": "approver"}],
        },
        headers=admin.headers,
    )
    assert unknown_type.status_code == 422

    patched = client.patch(
        f"/approvals/policies/{policies[0]['id']}", json={"active": False}, headers=admin.headers
    )
    assert patched.status_code == 200 and patched.json()["active"] is False
    assert client.delete(f"/approvals/policies/{policies[0]['id']}", headers=admin.headers).status_code == 204
    assert len(client.get("/approvals/policies", headers=admin.headers).json()) == 2

    transitions = client.get("/approvals/transitions", headers=admin.headers).json()
    assert any(t["requires_approval"] and t["active_policies"] == 1 for t in transitions)


def test_history_scope(client, admin, agent, lead, approver, change_ticket, transition):
    _request_approval(transition, agent, change_ticket["id"])
    mine = client.get("/approvals", params={"scope": "mine"}, headers=lead.headers).json()
    assert mine["total"] == 1
    # La etapa 2 aún espera: su aprobador no la ve en su historial.
    assert client.get("/approvals", params={"scope": "mine"}, headers=approver.headers).json()["total"] == 0
    assert client.get("/approvals", params={"scope": "all"}, headers=agent.headers).status_code == 403
    everything = client.get("/approvals", params={"scope": "all", "status": "pending"}, headers=admin.headers).json()
    assert everything["total"] == 1
    assert client.get("/approvals/pending", params={"all": "true"}, headers=admin.headers).json()[0]["can_decide"] is True


def test_decisions_are_snapshotted(client, agent, lead, approver, change_ticket, transition):
    _request_approval(transition, agent, change_ticket["id"])
    with SessionLocal() as session:
        rows = session.scalars(select(ApprovalDecision)).all()
        assert {(r.approver_id, r.decision) for r in rows} == {(lead.id, "pending"), (approver.id, "pending")}


# --------------------------------------------------------------------------- migración


def test_flows_migration_matches_models(tmp_path: Path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.models import approvals as approval_models
    from app.models import automation as automation_models

    flows_tables = sorted(
        {
            model.__table__.name
            for module in (approval_models, automation_models)
            for model in vars(module).values()
            if isinstance(model, type) and issubclass(model, Base) and model is not Base
            and model.__module__ == module.__name__
        }
    )
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0003_flows.py"
    spec = importlib.util.spec_from_file_location("flows_migration", path)
    migration = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(migration)
    assert migration.revision == "0003_flows" and migration.down_revision == "0002_sla"

    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'flows.db').as_posix()}")
    try:
        other_tables = [t for name, t in Base.metadata.tables.items() if name not in flows_tables]
        Base.metadata.create_all(engine, tables=other_tables)
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
        inspector = sa.inspect(engine)
        for table_name in flows_tables:
            model_table = Base.metadata.tables[table_name]
            db_columns = {c["name"]: c for c in inspector.get_columns(table_name)}
            assert set(db_columns) == set(model_table.columns.keys()), table_name
            for column in model_table.columns:
                assert db_columns[column.name]["nullable"] == column.nullable, (table_name, column.name)
            model_indexes = {ix.name for ix in model_table.indexes}
            db_indexes = {ix["name"] for ix in inspector.get_indexes(table_name)}
            assert model_indexes <= db_indexes, (table_name, model_indexes - db_indexes)
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
        remaining = set(sa.inspect(engine).get_table_names())
        assert not remaining.intersection(flows_tables)
        assert "tickets" in remaining
    finally:
        engine.dispose()


def test_failed_auto_execution_leaves_usable_approval(
    client, admin, agent, lead, approver, change_ticket, transition
):
    """Si la transición aprobada no puede aplicarse, la aprobación queda vigente para aplicarla a mano."""
    from app.models.core import TeamMember

    request_id = _request_approval(transition, agent, change_ticket["id"])["approval_request_id"]
    # Quien solicitó pierde su pertenencia al área: la ejecución automática fallará (403).
    with SessionLocal() as session:
        for membership in session.scalars(select(TeamMember).where(TeamMember.user_id == agent.id)).all():
            session.delete(membership)
        session.commit()
    client.post(f"/approvals/{request_id}/decide", json={"decision": "approve"}, headers=lead.headers)
    final = client.post(f"/approvals/{request_id}/decide", json={"decision": "approve"}, headers=approver.headers).json()
    assert final["status"] == "approved"
    assert final["executed_at"] is None and "No se pudo aplicar" in final["execution_error"]
    assert client.get(f"/tickets/{change_ticket['id']}", headers=admin.headers).json()["status"]["key"] == "new"

    # Un administrador aplica la transición sin una nueva solicitud.
    response = transition(admin, change_ticket["id"], "in_progress")
    assert response.status_code == 200, response.text
    request = client.get(f"/approvals/{request_id}", headers=admin.headers).json()
    assert request["executed_at"] is not None
    with SessionLocal() as session:
        assert session.scalar(select(sa.func.count()).select_from(ApprovalRequest)) == 1


def test_user_step_requires_approver_capable_user(flows_seed, client, admin, requester):
    policies = client.get("/approvals/policies", headers=admin.headers).json()
    response = client.post(
        "/approvals/policies",
        json={
            "name": "Usuario sin rol",
            "transition_id": policies[0]["transition_id"],
            "steps": [{"name": "Aprobación", "approver_type": "user", "user_id": requester.id}],
        },
        headers=admin.headers,
    )
    assert response.status_code == 422
    assert "rol de aprobador" in response.json()["detail"]


# --------------------------------------------------------------------------- revisión de seguridad


def test_admin_cannot_approve_own_request(flows_seed, client, make_user, requester, create_ticket, transition):
    """Segregación de funciones: la anulación de un admin no aplica a su propia solicitud."""
    boss = make_user("boss", roles=("admin",))
    make_user("otro.admin", roles=("admin",))
    ticket = create_ticket(requester, type_key="change")
    response = transition(boss, ticket["id"], "in_progress")
    assert response.status_code == 409, response.text
    request_id = response.json()["detail"]["approval_request_id"]

    detail = client.get(f"/approvals/{request_id}", headers=boss.headers).json()
    assert detail["can_decide"] is False
    own = client.post(f"/approvals/{request_id}/decide", json={"decision": "approved"}, headers=boss.headers)
    assert own.status_code == 403
    after = client.get(f"/tickets/{ticket['id']}", headers=boss.headers).json()
    assert after["status"]["key"] == "new"


def test_skipped_approver_loses_visibility(
    flows_seed, client, admin, make_user, requester, create_ticket, transition
):
    """Un aprobador cuya decisión quedó omitida deja de ver el ticket (y sus búsquedas)."""
    soc_agent = make_user("luis.soc", roles=("agent",), departments=(DEPARTMENT_SOC,))
    marta = make_user("marta.aprobadora", roles=("approver",))
    pedro = make_user("pedro.aprobador", roles=("approver",))
    transition_id = _transition_id("triage", "assigned")
    created = client.post(
        "/approvals/policies",
        json={
            "name": "CAB",
            "transition_id": transition_id,
            "ticket_type_keys": ["incident"],
            "steps": [{"name": "CAB", "approver_type": "role", "role": "approver", "scope": "global", "rule": "any"}],
        },
        headers=admin.headers,
    )
    assert created.status_code == 201, created.text
    ticket = create_ticket(requester, department=DEPARTMENT_SOC, title="Incidente confidencial SOC")
    assert transition(soc_agent, ticket["id"], "triage").status_code == 200
    response = transition(soc_agent, ticket["id"], "assigned")
    assert response.status_code == 409
    request_id = response.json()["detail"]["approval_request_id"]

    # Ambos ven el ticket mientras la etapa está activa.
    assert client.get(f"/tickets/{ticket['id']}", headers=pedro.headers).status_code == 200
    ok = client.post(f"/approvals/{request_id}/decide", json={"decision": "approve"}, headers=marta.headers)
    assert ok.status_code == 200 and ok.json()["status"] == "approved"

    # Marta decidió: conserva el acceso. Pedro quedó «skipped»: lo pierde.
    assert client.get(f"/tickets/{ticket['id']}", headers=marta.headers).status_code == 200
    assert client.get(f"/tickets/{ticket['id']}", headers=pedro.headers).status_code == 404
    search = client.get("/tickets", params={"q": "confidencial"}, headers=pedro.headers).json()
    assert search["total"] == 0
    assert client.get("/approvals", params={"scope": "mine"}, headers=pedro.headers).json()["total"] == 0


def test_department_scoped_step_does_not_fall_back_to_global_approvers(
    client, admin, agent, make_user, requester, create_ticket, transition
):
    """Sin aprobadores en el departamento se designa a los administradores, no a toda la empresa."""
    outsider = make_user("pedro.aprobador", roles=("approver",))
    transition_id = _transition_id("new", "triage")
    client.put(f"/approvals/transitions/{transition_id}", json={"requires_approval": True}, headers=admin.headers)
    ticket = create_ticket(requester)
    assert transition(agent, ticket["id"], "triage").status_code == 409
    request = client.get(f"/tickets/{ticket['id']}/approvals", headers=agent.headers).json()[0]
    approvers = [d["approver"]["id"] for d in request["steps"][0]["decisions"]]
    assert approvers == [admin.id]
    assert client.get(f"/tickets/{ticket['id']}", headers=outsider.headers).status_code == 404


def test_cancel_after_concurrent_approval_is_rejected(client, agent, lead, approver, change_ticket, transition):
    """``cancel_request`` recarga la solicitud: si ya se resolvió no la pisa."""
    from app.core.security import principal_from_dev
    from app.services.approvals import service

    request_id = _request_approval(transition, agent, change_ticket["id"])["approval_request_id"]
    with SessionLocal() as stale_session:
        stale = stale_session.get(ApprovalRequest, request_id)
        assert stale.status == "pending"  # copia «vieja» en memoria
        # Mientras tanto la solicitud se aprueba por completo en otra sesión.
        for persona in (lead, approver):
            done = client.post(f"/approvals/{request_id}/decide", json={"decision": "approve"}, headers=persona.headers)
            assert done.status_code == 200, done.text
        principal = principal_from_dev(stale_session, agent.username, "agent", None)
        with pytest.raises(Exception) as excinfo:
            service.cancel_request(stale_session, principal, stale, "tarde")
        assert getattr(excinfo.value, "status_code", None) == 409
        stale_session.rollback()
    with SessionLocal() as session:
        assert session.get(ApprovalRequest, request_id).status == "approved"
