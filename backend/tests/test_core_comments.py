"""Comentarios públicos/internos, primera respuesta e historial filtrado."""

from __future__ import annotations


def _comment(client, persona, ticket_id, body, visibility="public"):
    return client.post(
        f"/tickets/{ticket_id}/comments",
        json={"body": body, "visibility": visibility},
        headers=persona.headers,
    )


def test_internal_comments_hidden_from_requester(client, agent, requester, create_ticket):
    ticket = create_ticket(requester)
    assert _comment(client, agent, ticket["id"], "Nota interna del NOC", "internal").status_code == 201
    assert _comment(client, agent, ticket["id"], "Estamos revisando").status_code == 201

    as_agent = client.get(f"/tickets/{ticket['id']}/comments", headers=agent.headers).json()
    assert [c["visibility"] for c in as_agent] == ["internal", "public"]
    as_requester = client.get(f"/tickets/{ticket['id']}/comments", headers=requester.headers).json()
    assert [c["body"] for c in as_requester] == ["Estamos revisando"]
    assert as_requester[0]["author"]["display_name"] == "Ana Telecom"


def test_requester_cannot_create_internal_comment(client, requester, create_ticket):
    ticket = create_ticket(requester)
    response = _comment(client, requester, ticket["id"], "Quiero ocultarlo", "internal")
    assert response.status_code == 403
    assert _comment(client, requester, ticket["id"], "Sigue sin servicio").status_code == 201


def test_invalid_visibility_is_rejected(client, agent, requester, create_ticket):
    ticket = create_ticket(requester)
    assert _comment(client, agent, ticket["id"], "x", "secreta").status_code == 422
    assert _comment(client, agent, ticket["id"], "   ").status_code == 422


def test_other_requester_cannot_comment(client, requester, other_requester, create_ticket):
    ticket = create_ticket(requester)
    assert _comment(client, other_requester, ticket["id"], "Hola").status_code == 404


def test_first_response_rules(client, agent, requester, create_ticket):
    ticket = create_ticket(requester)

    def first_response():
        return client.get(f"/tickets/{ticket['id']}", headers=agent.headers).json()["first_response_at"]

    _comment(client, requester, ticket["id"], "¿Alguna novedad?")
    assert first_response() is None
    _comment(client, agent, ticket["id"], "Nota interna", "internal")
    assert first_response() is None
    _comment(client, agent, ticket["id"], "Ya lo estamos atendiendo")
    stamped = first_response()
    assert stamped is not None
    _comment(client, agent, ticket["id"], "Segundo mensaje")
    assert first_response() == stamped


def test_agent_commenting_own_ticket_is_not_first_response(client, agent, create_ticket):
    ticket = create_ticket(agent)
    _comment(client, agent, ticket["id"], "Agrego detalles")
    detail = client.get(f"/tickets/{ticket['id']}", headers=agent.headers).json()
    assert detail["first_response_at"] is None


def test_history_hides_internal_comment_events(client, agent, requester, create_ticket):
    ticket = create_ticket(requester)
    _comment(client, agent, ticket["id"], "Nota interna", "internal")
    _comment(client, agent, ticket["id"], "Respuesta pública")

    agent_history = client.get(f"/tickets/{ticket['id']}/history", headers=agent.headers).json()
    assert [h["event_type"] for h in agent_history] == [
        "ticket.created",
        "ticket.commented",
        "ticket.commented",
    ]
    requester_history = client.get(f"/tickets/{ticket['id']}/history", headers=requester.headers).json()
    assert [h["event_type"] for h in requester_history] == ["ticket.created", "ticket.commented"]
    assert requester_history[1]["after"]["visibility"] == "public"
    assert requester_history[1]["actor"]["id"] == agent.id


def test_internal_notes_require_working_the_ticket(client, agent, other_agent, admin, create_ticket):
    """Un agente que ve el ticket solo por ser su solicitante no lee notas internas de otra área."""
    ticket = create_ticket(agent, department="Ciberseguridad / SOC", title="Correo sospechoso")
    secret = "NOTA INTERNA: investigar a ana"
    assert _comment(client, other_agent, ticket["id"], secret, "internal").status_code == 201
    assert _comment(client, other_agent, ticket["id"], "Lo revisamos").status_code == 201

    as_requester_agent = client.get(f"/tickets/{ticket['id']}/comments", headers=agent.headers)
    assert as_requester_agent.status_code == 200
    assert [c["body"] for c in as_requester_agent.json()] == ["Lo revisamos"]
    history = client.get(f"/tickets/{ticket['id']}/history", headers=agent.headers).json()
    assert all((h["after"] or {}).get("visibility") != "internal" for h in history)
    # Tampoco puede escribir notas internas en un ticket que no atiende.
    assert _comment(client, agent, ticket["id"], "Nota", "internal").status_code == 403

    soc = client.get(f"/tickets/{ticket['id']}/comments", headers=other_agent.headers).json()
    assert [c["body"] for c in soc] == [secret, "Lo revisamos"]
    as_admin = client.get(f"/tickets/{ticket['id']}/comments", headers=admin.headers).json()
    assert len(as_admin) == 2


def test_assigned_agent_from_other_area_sees_internal_notes(
    client, agent, other_agent, admin, requester, create_ticket
):
    ticket = create_ticket(requester, department="Ciberseguridad / SOC")
    _comment(client, other_agent, ticket["id"], "Contexto interno", "internal")
    before = client.get(f"/tickets/{ticket['id']}/comments", headers=agent.headers)
    assert before.status_code == 404  # ni siquiera ve el ticket
    patched = client.patch(
        f"/tickets/{ticket['id']}", json={"assignee_id": agent.id}, headers=admin.headers
    )
    assert patched.status_code == 200, patched.text
    after = client.get(f"/tickets/{ticket['id']}/comments", headers=agent.headers).json()
    assert [c["body"] for c in after] == ["Contexto interno"]
