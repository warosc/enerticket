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
