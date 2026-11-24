"""Visibilidad y edición de tickets por área, rol y extensiones de visibilidad."""

from __future__ import annotations

from sqlalchemy import select

from app.core.permissions import register_visibility_extension
from app.models.core import Team, TeamMember, Ticket
from tests.conftest import DEPARTMENT_SOC, DEPARTMENT_TELECOM, auth_headers


def _keys(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {item["key"] for item in response.json()["items"]}


def test_visibility_by_area_and_role(
    client, admin, agent, other_agent, requester, other_requester, create_ticket
):
    telecom = create_ticket(requester, DEPARTMENT_TELECOM, title="Enlace caído")
    soc = create_ticket(other_requester, DEPARTMENT_SOC, title="Alerta de phishing")
    own_by_agent = create_ticket(agent, DEPARTMENT_SOC, title="Consulta del agente al SOC")

    assert _keys(client.get("/tickets", headers=admin.headers)) == {
        telecom["key"], soc["key"], own_by_agent["key"]
    }
    # Agente de Telecom: tickets de su área + los creados por él (aunque sean de otra área).
    assert _keys(client.get("/tickets", headers=agent.headers)) == {
        telecom["key"], own_by_agent["key"]
    }
    # Agente del SOC: todo lo de su área.
    assert _keys(client.get("/tickets", headers=other_agent.headers)) == {
        soc["key"], own_by_agent["key"]
    }
    # Solicitantes: solo los propios.
    assert _keys(client.get("/tickets", headers=requester.headers)) == {telecom["key"]}
    assert _keys(client.get("/tickets", headers=other_requester.headers)) == {soc["key"]}


def test_invisible_ticket_returns_404(client, requester, other_requester, other_agent, create_ticket):
    ticket = create_ticket(requester, DEPARTMENT_TELECOM)
    for persona in (other_requester, other_agent):
        assert client.get(f"/tickets/{ticket['id']}", headers=persona.headers).status_code == 404
        assert (
            client.get(f"/tickets/{ticket['id']}/comments", headers=persona.headers).status_code
            == 404
        )
        assert (
            client.get(f"/tickets/{ticket['id']}/history", headers=persona.headers).status_code
            == 404
        )
    assert client.get("/tickets/99999", headers=requester.headers).status_code == 404


def test_patch_requires_agent_and_area(client, agent, other_agent, requester, create_ticket):
    telecom = create_ticket(requester, DEPARTMENT_TELECOM)
    # El solicitante no puede editar (rol agent requerido).
    response = client.patch(f"/tickets/{telecom['id']}", json={"title": "Nuevo"}, headers=requester.headers)
    assert response.status_code == 403
    # Agente de otra área: ni siquiera lo ve.
    response = client.patch(f"/tickets/{telecom['id']}", json={"title": "Nuevo"}, headers=other_agent.headers)
    assert response.status_code == 404
    # Agente del área: puede editar.
    response = client.patch(
        f"/tickets/{telecom['id']}",
        json={"title": "Enlace MPLS caído", "priority_key": "high"},
        headers=agent.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Enlace MPLS caído"
    assert response.json()["priority"]["key"] == "high"


def test_agent_sees_but_cannot_edit_own_ticket_in_other_area(client, agent, create_ticket):
    ticket = create_ticket(agent, DEPARTMENT_SOC)
    assert client.get(f"/tickets/{ticket['id']}", headers=agent.headers).status_code == 200
    response = client.patch(f"/tickets/{ticket['id']}", json={"title": "Cambio"}, headers=agent.headers)
    assert response.status_code == 403


def test_assignee_from_other_area_can_view_and_edit(client, admin, other_agent, requester, create_ticket):
    ticket = create_ticket(requester, DEPARTMENT_TELECOM)
    assert client.get(f"/tickets/{ticket['id']}", headers=other_agent.headers).status_code == 404
    response = client.patch(
        f"/tickets/{ticket['id']}", json={"assignee_id": other_agent.id}, headers=admin.headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["assignee"]["id"] == other_agent.id
    assert client.get(f"/tickets/{ticket['id']}", headers=other_agent.headers).status_code == 200
    response = client.patch(
        f"/tickets/{ticket['id']}", json={"description": "Revisado por SOC"}, headers=other_agent.headers
    )
    assert response.status_code == 200


def test_visibility_extension_grants_access(client, approver, requester, create_ticket):
    ticket = create_ticket(requester, DEPARTMENT_TELECOM)
    assert client.get(f"/tickets/{ticket['id']}", headers=approver.headers).status_code == 404

    @register_visibility_extension
    def approver_extension(principal):
        if "approver" not in principal.roles:
            return None
        return Ticket.id.in_(select(Ticket.id).where(Ticket.id == ticket["id"]))

    assert client.get(f"/tickets/{ticket['id']}", headers=approver.headers).status_code == 200
    assert _keys(client.get("/tickets", headers=approver.headers)) == {ticket["key"]}
    # El solicitante sin rol approver no gana visibilidad adicional.
    assert _keys(client.get("/tickets", headers=requester.headers)) == {ticket["key"]}


def test_groups_claim_syncs_team_membership(client, db, ids, requester, create_ticket):
    ticket = create_ticket(requester, DEPARTMENT_SOC)
    soc_team = db.get(Team, ids["teams"][DEPARTMENT_SOC])
    group = soc_team.external_group
    assert group, "El seed debe asignar external_group a los equipos"

    with_group = auth_headers("nuevo.agente", "agent", group.lower())  # sin distinguir mayúsculas
    me = client.get("/me", headers=with_group).json()
    assert [t["id"] for t in me["teams"]] == [soc_team.id]
    assert me["department_ids"] == [ids["departments"][DEPARTMENT_SOC]]
    assert client.get(f"/tickets/{ticket['id']}", headers=with_group).status_code == 200

    without_group = auth_headers("nuevo.agente", "agent", "")
    assert client.get("/me", headers=without_group).json()["teams"] == []
    assert client.get(f"/tickets/{ticket['id']}", headers=without_group).status_code == 404

    # Sin header de grupos no se sincroniza (se conserva la pertenencia actual).
    client.get("/me", headers=with_group)
    assert len(client.get("/me", headers=auth_headers("nuevo.agente", "agent")).json()["teams"]) == 1


def test_group_sync_keeps_manual_memberships(client, db, make_user):
    persona = make_user("manual.agente", roles=("agent",), departments=(DEPARTMENT_TELECOM,))
    team = db.scalar(select(Team).where(Team.name == f"Equipo {DEPARTMENT_TELECOM}"))
    team.external_group = None  # equipo administrado manualmente
    db.commit()
    response = client.get("/me", headers=auth_headers("manual.agente", "agent", "OTRO-GRUPO"))
    assert [t["id"] for t in response.json()["teams"]] == [team.id]
    db.expire_all()
    assert db.get(TeamMember, (team.id, persona.id)) is not None


def test_users_endpoint_requires_agent_and_filters_by_role(client, admin, agent, requester, approver):
    assert client.get("/users", headers=requester.headers).status_code == 403
    response = client.get("/users", params={"role": "agent"}, headers=agent.headers)
    assert response.status_code == 200
    usernames = {u["username"] for u in response.json()}
    assert usernames == {"ana.telecom"}
    response = client.get("/users", params={"q": "marta"}, headers=admin.headers)
    assert [u["username"] for u in response.json()] == ["marta.aprobadora"]


def test_list_filters_and_pagination(client, agent, requester, create_ticket, transition):
    first = create_ticket(requester, title="Router sin energía", priority_key="critical")
    second = create_ticket(requester, title="Cambio de puerto", priority_key="low")
    third = create_ticket(agent, title="Inventario de enlaces", priority_key="medium")
    assert transition(agent, second["id"], "triage").status_code == 200

    def keys(**params):
        return _keys(client.get("/tickets", params=params, headers=agent.headers))

    assert keys(status="triage") == {second["key"]}
    assert keys(status="new,triage") == {first["key"], second["key"], third["key"]}
    assert keys(priority="critical") == {first["key"]}
    assert keys(mine=True) == {third["key"]}
    assert keys(q="router") == {first["key"]}
    assert keys(q=second["key"]) == {second["key"]}
    assert keys(category="todo") == {first["key"], second["key"], third["key"]}
    assert keys(unassigned=True) == {first["key"], second["key"], third["key"]}

    page = client.get("/tickets", params={"page": 2, "page_size": 2}, headers=agent.headers).json()
    assert page["total"] == 3 and page["page"] == 2 and page["page_size"] == 2
    assert len(page["items"]) == 1
    ordered = client.get("/tickets", params={"sort": "-priority"}, headers=agent.headers).json()
    assert [t["priority"]["key"] for t in ordered["items"]] == ["critical", "medium", "low"]
