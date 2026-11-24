"""Transiciones de workflow: validación, permisos, guards y marcas de tiempo."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select

from app.core.events import guard
from app.models.core import TicketHistory
from tests.conftest import DEPARTMENT_TELECOM


def test_valid_transition_sets_status_and_first_response(agent, requester, create_ticket, transition):
    ticket = create_ticket(requester)
    assert ticket["status"]["key"] == "new"
    assert ticket["first_response_at"] is None

    response = transition(agent, ticket["id"], "triage")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == {"id": body["status"]["id"], "key": "triage", "name": "En triaje", "category": "todo"}
    assert body["first_response_at"] is not None


def test_transition_not_in_workflow_is_rejected(agent, requester, create_ticket, transition):
    ticket = create_ticket(requester)
    response = transition(agent, ticket["id"], "resolved")
    assert response.status_code == 409
    assert "no permitida" in response.json()["detail"]
    assert transition(agent, ticket["id"], "desconocido").status_code == 422


def test_resolution_close_and_reopen_timestamps(agent, requester, create_ticket, transition):
    ticket = create_ticket(requester)
    for state in ("in_progress", "pending", "in_progress"):
        assert transition(agent, ticket["id"], state).status_code == 200

    resolved = transition(agent, ticket["id"], "resolved").json()
    assert resolved["resolved_at"] is not None and resolved["closed_at"] is None

    reopened = transition(requester, ticket["id"], "in_progress")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["resolved_at"] is None

    transition(agent, ticket["id"], "resolved")
    closed = transition(requester, ticket["id"], "closed").json()
    assert closed["status"]["key"] == "closed"
    assert closed["resolved_at"] is not None and closed["closed_at"] is not None
    # closed es final: no hay transiciones de salida.
    assert transition(agent, ticket["id"], "in_progress").status_code == 409


def test_requester_permissions_on_transitions(
    agent, requester, other_requester, create_ticket, transition
):
    ticket = create_ticket(requester)
    # El solicitante no puede mover el trabajo (solo cerrar/reabrir).
    assert transition(requester, ticket["id"], "triage").status_code == 403
    # Otro solicitante no ve el ticket.
    assert transition(other_requester, ticket["id"], "closed").status_code == 404
    # Puede cerrar (cancelar) su propio ticket.
    response = transition(requester, ticket["id"], "closed")
    assert response.status_code == 200
    # Cerrar como solicitante no cuenta como primera respuesta.
    assert response.json()["first_response_at"] is None


def test_agent_of_other_area_cannot_transition(other_agent, requester, create_ticket, transition):
    ticket = create_ticket(requester, DEPARTMENT_TELECOM)
    assert transition(other_agent, ticket["id"], "triage").status_code == 404


def test_guard_blocks_transition(db, agent, requester, create_ticket, transition, published_events):
    ticket = create_ticket(requester)
    received: list[dict] = []

    @guard("ticket.transition")
    def require_approval_for_resolution(session, ticket_obj, actor, data):
        received.append(dict(data, actor=actor.username, ticket_id=ticket_obj.id))
        if data["to_state"] == "in_progress":
            raise HTTPException(409, detail="Requiere aprobación")

    published_events.clear()
    response = transition(agent, ticket["id"], "in_progress")
    assert response.status_code == 409
    assert response.json()["detail"] == "Requiere aprobación"
    assert published_events == []

    detail = transition(agent, ticket["id"], "triage")
    assert detail.status_code == 200
    assert detail.json()["status"]["key"] == "triage"

    first = received[0]
    assert first["actor"] == "ana.telecom" and first["ticket_id"] == ticket["id"]
    assert first["from_state"] == "new" and first["to_state"] == "in_progress"
    assert first["from_category"] == "todo" and first["to_category"] == "in_progress"
    assert first["requires_approval"] is False and isinstance(first["transition_id"], int)

    transitions = db.scalars(
        select(TicketHistory).where(
            TicketHistory.ticket_id == ticket["id"], TicketHistory.event_type == "ticket.transitioned"
        )
    ).all()
    assert [(h.before_json["status"], h.after_json["status"]) for h in transitions] == [("new", "triage")]


def test_transition_with_comment_adds_public_comment(client, agent, requester, create_ticket, transition):
    ticket = create_ticket(requester)
    response = transition(agent, ticket["id"], "in_progress", comment="Revisando el enlace")
    assert response.status_code == 200
    comments = client.get(f"/tickets/{ticket['id']}/comments", headers=requester.headers).json()
    assert [(c["body"], c["visibility"]) for c in comments] == [("Revisando el enlace", "public")]
