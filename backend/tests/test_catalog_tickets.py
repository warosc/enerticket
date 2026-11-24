"""Catálogo de servicio — reglas del ticket: clasificación, proveedor/canal, solución
obligatoria, minutos hábiles de resolución, orden de trabajo y filtros."""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal, utcnow
from app.models.catalog import Provider, TicketCategory, TicketSubcategory
from app.models.core import Department, Ticket, TicketHistory
from app.models.sla import SlaInstance, SlaPolicy
from app.services import tickets as ticket_svc
from tests.conftest import DEPARTMENT_SOC, DEPARTMENT_TELECOM

GT = timezone(timedelta(hours=-6))


def _category(department_id: int, name: str, type_keys=(), subcategories=(), active=True) -> dict[str, Any]:
    with SessionLocal() as session:
        category = TicketCategory(
            department_id=department_id, name=name, type_keys=list(type_keys), active=active, sort_order=0
        )
        session.add(category)
        session.flush()
        subs = {}
        for sub_name in subcategories:
            sub = TicketSubcategory(category_id=category.id, name=sub_name, active=True, sort_order=0)
            session.add(sub)
            session.flush()
            subs[sub_name] = sub.id
        session.commit()
        return {"id": category.id, "subs": subs}


@pytest.fixture
def telecom(ids) -> int:
    return ids["departments"][DEPARTMENT_TELECOM]


@pytest.fixture
def catalog(ids, telecom) -> dict[str, Any]:
    """Telecomunicaciones: «Telefonía Móvil» (incidencias y peticiones) con subcategorías y
    «Enlaces» (solo incidencias). SOC: «Malware»."""
    mobile = _category(telecom, "Telefonía Móvil", ("incident", "service_request"), ("Equipo dañado", "Línea nueva"))
    links = _category(telecom, "Enlaces", ("incident",))
    malware = _category(ids["departments"][DEPARTMENT_SOC], "Malware")
    return {"mobile": mobile, "links": links, "malware": malware}


def _post(client, persona, telecom, **payload):
    body = {"title": "Ticket de prueba", "department_id": telecom, **payload}
    return client.post("/tickets", json=body, headers=persona.headers)


# --------------------------------------------------------------------------- clasificación


def test_create_with_category_and_subcategory(client, requester, telecom, catalog):
    sub_id = catalog["mobile"]["subs"]["Equipo dañado"]
    response = _post(client, requester, telecom, category_id=catalog["mobile"]["id"], subcategory_id=sub_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["category"] == {"id": catalog["mobile"]["id"], "name": "Telefonía Móvil"}
    assert body["subcategory"] == {"id": sub_id, "name": "Equipo dañado"}
    # Una subcategoría sola toma su categoría.
    response = _post(client, requester, telecom, subcategory_id=sub_id)
    assert response.status_code == 201 and response.json()["category"]["id"] == catalog["mobile"]["id"]


@pytest.mark.parametrize(
    "payload_builder, message",
    [
        (lambda c: {"category_id": c["malware"]["id"]}, "no pertenece al departamento"),
        (lambda c: {"category_id": c["links"]["id"], "type_key": "service_request"}, "no admite"),
        (lambda c: {"category_id": c["links"]["id"], "subcategory_id": c["mobile"]["subs"]["Línea nueva"]},
         "no pertenece a la categoría"),
        (lambda c: {"category_id": 99999}, "no existe"),
        (lambda c: {"subcategory_id": 99999}, "no existe"),
        (lambda c: {"type_key": "change"}, "no está disponible"),
        (lambda c: {"type_key": "desconocido"}, "desconocido"),
    ],
)
def test_classification_validations(client, requester, telecom, catalog, payload_builder, message):
    response = _post(client, requester, telecom, **payload_builder(catalog))
    assert response.status_code == 422, response.text
    assert message in response.json()["detail"]


def test_inactive_category_is_rejected(client, requester, telecom, catalog):
    with SessionLocal() as session:
        session.get(TicketCategory, catalog["links"]["id"]).active = False
        session.commit()
    response = _post(client, requester, telecom, category_id=catalog["links"]["id"])
    assert response.status_code == 422 and "inactiva" in response.json()["detail"]


def test_department_must_serve_tickets(client, requester):
    with SessionLocal() as session:
        department = Department(name="Comercial", serves_tickets=False, active=True)
        session.add(department)
        session.commit()
        department_id = department.id
    response = client.post(
        "/tickets", json={"title": "Prueba", "department_id": department_id}, headers=requester.headers
    )
    assert response.status_code == 422 and "no atiende tickets" in response.json()["detail"]


def test_channel_and_provider_only_for_agents(client, requester, agent, telecom, ids):
    with SessionLocal() as session:
        claro = Provider(name="Claro", department_ids=[telecom], active=True)
        tigo = Provider(name="Tigo", department_ids=[ids["departments"][DEPARTMENT_SOC]], active=True)
        old = Provider(name="Antiguo", department_ids=[], active=False)
        session.add_all([claro, tigo, old])
        session.commit()
        provider_ids = {"claro": claro.id, "tigo": tigo.id, "old": old.id}
    # El solicitante no fija canal ni proveedor (se ignoran).
    body = _post(client, requester, telecom, channel="phone", provider_id=provider_ids["claro"]).json()
    assert body["channel"] == "portal" and body["provider"] is None
    body = _post(client, agent, telecom, channel="phone", provider_id=provider_ids["claro"]).json()
    assert body["channel"] == "phone" and body["provider"] == {"id": provider_ids["claro"], "name": "Claro"}
    response = _post(client, agent, telecom, provider_id=provider_ids["tigo"])
    assert response.status_code == 422 and "no atiende el departamento" in response.json()["detail"]
    assert _post(client, agent, telecom, provider_id=provider_ids["old"]).status_code == 422
    assert _post(client, agent, telecom, channel="fax").status_code == 422


def test_agent_patch_classification_records_history(client, agent, requester, telecom, catalog, create_ticket):
    ticket = create_ticket(requester)
    sub_id = catalog["mobile"]["subs"]["Línea nueva"]
    response = client.patch(
        f"/tickets/{ticket['id']}",
        json={"category_id": catalog["mobile"]["id"], "subcategory_id": sub_id, "channel": "email"},
        headers=agent.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["subcategory"]["id"] == sub_id and response.json()["channel"] == "email"
    # Cambiar la categoría retira la subcategoría que ya no le corresponde.
    response = client.patch(
        f"/tickets/{ticket['id']}", json={"category_id": catalog["links"]["id"]}, headers=agent.headers
    )
    assert response.json()["category"]["id"] == catalog["links"]["id"] and response.json()["subcategory"] is None
    bad = client.patch(
        f"/tickets/{ticket['id']}", json={"category_id": catalog["malware"]["id"]}, headers=agent.headers
    )
    assert bad.status_code == 422
    with SessionLocal() as session:
        changes = [
            h.after_json
            for h in session.scalars(
                select(TicketHistory).where(
                    TicketHistory.ticket_id == ticket["id"], TicketHistory.event_type == "ticket.updated"
                )
            ).all()
        ]
    assert changes[0]["category_id"] == catalog["mobile"]["id"] and changes[0]["channel"] == "email"
    # Cambiar de departamento retira la categoría del departamento anterior.
    response = client.patch(
        f"/tickets/{ticket['id']}",
        json={"department_id": catalog_department(catalog["malware"]["id"])},
        headers=agent.headers,
    )
    assert response.status_code == 200 and response.json()["category"] is None


def catalog_department(category_id: int) -> int:
    with SessionLocal() as session:
        return session.get(TicketCategory, category_id).department_id


def test_requester_cannot_patch_classification(client, requester, create_ticket, catalog):
    ticket = create_ticket(requester)
    response = client.patch(
        f"/tickets/{ticket['id']}", json={"category_id": catalog["mobile"]["id"]}, headers=requester.headers
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------- resolver


def test_resolution_requires_solution(client, agent, requester, create_ticket, transition):
    ticket = create_ticket(requester)
    assert transition(agent, ticket["id"], "in_progress").status_code == 200
    response = transition(agent, ticket["id"], "resolved", resolution_note=None)
    assert response.status_code == 422
    assert response.json()["detail"] == "Indique la solución antes de resolver"
    response = transition(agent, ticket["id"], "resolved", resolution_note="   ")
    assert response.status_code == 422
    response = transition(agent, ticket["id"], "resolved", resolution_note="Se reinició el enlace")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resolution_note"] == "Se reinició el enlace"
    assert body["resolved_by"] == {"id": agent.id, "display_name": "Ana Telecom"}
    assert body["business_resolution_minutes"] is not None
    # Cerrar un ticket resuelto no vuelve a pedir la solución (ya está guardada).
    assert transition(requester, ticket["id"], "closed", resolution_note=None).status_code == 200


def test_saved_solution_is_enough_and_reopen_clears_resolution(client, agent, requester, create_ticket, transition):
    ticket = create_ticket(requester)
    transition(agent, ticket["id"], "in_progress")
    patch = client.patch(
        f"/tickets/{ticket['id']}", json={"resolution_note": "Cambio de tarjeta SIM"}, headers=agent.headers
    )
    assert patch.status_code == 200
    resolved = transition(agent, ticket["id"], "resolved", resolution_note=None)
    assert resolved.status_code == 200 and resolved.json()["resolution_note"] == "Cambio de tarjeta SIM"
    # Un ticket resuelto no puede quedar sin solución.
    cleared = client.patch(f"/tickets/{ticket['id']}", json={"resolution_note": ""}, headers=agent.headers)
    assert cleared.status_code == 422
    reopened = transition(agent, ticket["id"], "in_progress").json()
    assert reopened["resolved_by"] is None and reopened["business_resolution_minutes"] is None
    assert reopened["resolved_at"] is None
    assert reopened["resolution_note"] == "Cambio de tarjeta SIM"


def test_category_required_when_department_has_catalog(client, agent, requester, create_ticket, transition, catalog):
    ticket = create_ticket(requester)
    transition(agent, ticket["id"], "in_progress")
    response = transition(agent, ticket["id"], "resolved")
    assert response.status_code == 422 and response.json()["detail"].startswith("Clasifique el ticket")
    client.patch(f"/tickets/{ticket['id']}", json={"category_id": catalog["mobile"]["id"]}, headers=agent.headers)
    assert transition(agent, ticket["id"], "resolved").status_code == 200


def test_requester_can_close_own_ticket_with_solution_without_category(
    client, requester, create_ticket, transition, catalog
):
    ticket = create_ticket(requester)
    assert transition(requester, ticket["id"], "closed", resolution_note=None).status_code == 422
    response = transition(requester, ticket["id"], "closed", resolution_note="Ya no es necesario")
    assert response.status_code == 200, response.text
    assert response.json()["resolved_by"]["id"] == requester.id


def test_no_category_required_without_active_catalog(client, agent, requester, create_ticket, transition, catalog):
    with SessionLocal() as session:
        for category in session.scalars(select(TicketCategory)).all():
            category.active = False
        session.commit()
    ticket = create_ticket(requester)
    transition(agent, ticket["id"], "in_progress")
    assert transition(agent, ticket["id"], "resolved").status_code == 200


# --------------------------------------------------------------------------- minutos hábiles


def test_business_minutes_skip_weekend_and_holiday(client, agent, requester, create_ticket, transition, monkeypatch):
    """Sin políticas SLA: calendario hábil estándar (L-V 08-17, feriados de Guatemala).

    Viernes 11/09/2026 16:00 → miércoles 16/09/2026 10:00 (el martes 15 es feriado):
    60 (viernes) + 540 (lunes) + 0 (martes feriado) + 120 (miércoles) = 720 minutos.
    """
    ticket = create_ticket(requester)
    with SessionLocal() as session:
        session.get(Ticket, ticket["id"]).created_at = datetime(2026, 9, 11, 16, 0, tzinfo=GT)
        session.commit()
    monkeypatch.setattr(ticket_svc, "utcnow", lambda: datetime(2026, 9, 16, 10, 0, tzinfo=GT))
    transition(agent, ticket["id"], "in_progress")
    response = transition(agent, ticket["id"], "resolved")
    assert response.status_code == 200, response.text
    assert response.json()["business_resolution_minutes"] == 720


def test_business_minutes_use_default_calendar_from_db(client, agent, requester, create_ticket, transition, monkeypatch):
    """Con el seed SLA el calendario «Horario hábil Guatemala» es el de referencia; un ticket
    de prioridad baja sin política aplicable usa ese calendario."""
    with SessionLocal() as session:
        importlib.import_module("app.seeds.10_sla").run(session)
        session.commit()
        for policy in session.scalars(select(SlaPolicy)).all():
            policy.active = False
        session.commit()
    ticket = create_ticket(requester)
    with SessionLocal() as session:
        # Lunes 07/09/2026 07:00 → lunes 09:30: cuenta desde las 08:00 = 90 minutos.
        session.get(Ticket, ticket["id"]).created_at = datetime(2026, 9, 7, 7, 0, tzinfo=GT)
        session.commit()
    monkeypatch.setattr(ticket_svc, "utcnow", lambda: datetime(2026, 9, 7, 9, 30, tzinfo=GT))
    transition(agent, ticket["id"], "in_progress")
    assert transition(agent, ticket["id"], "resolved").json()["business_resolution_minutes"] == 90


def test_business_minutes_from_sla_instance_discount_pauses(client, agent, requester, create_ticket, transition):
    """Con instancia SLA de resolución se usan sus segundos hábiles (descuenta pausas)."""
    seed = importlib.import_module("app.seeds.10_sla")
    with SessionLocal() as session:
        seed.run(session)
        session.commit()
    ticket = create_ticket(requester, priority_key="critical")  # «Crítica 24x7»
    start = utcnow() - timedelta(hours=3)
    with SessionLocal() as session:
        session.get(Ticket, ticket["id"]).created_at = start
        instance = session.scalar(
            select(SlaInstance).where(SlaInstance.ticket_id == ticket["id"], SlaInstance.metric == "resolution")
        )
        instance.started_at = start
        instance.paused_business_seconds = 3600  # una hora en espera (pausa ya cerrada)
        session.commit()
    transition(agent, ticket["id"], "in_progress")
    body = transition(agent, ticket["id"], "resolved").json()
    assert body["business_resolution_minutes"] == 120


# --------------------------------------------------------------------------- orden de trabajo


def test_work_order_creation_and_parent_resolution(
    client, agent, requester, other_agent, create_ticket, transition, published_events, catalog
):
    work = _category(catalog_department(catalog["links"]["id"]), "Instalaciones", ("work_order",))
    parent = create_ticket(requester, priority_key="high", category_id=catalog["mobile"]["id"])
    url = f"/tickets/{parent['id']}/work-order"
    assert client.post(url, json={}, headers=requester.headers).status_code == 403
    assert client.post(url, json={}, headers=other_agent.headers).status_code == 404
    # La categoría debe admitir órdenes de trabajo.
    bad = client.post(url, json={"category_id": catalog["links"]["id"]}, headers=agent.headers)
    assert bad.status_code == 422
    published_events.clear()

    response = client.post(
        url, json={"category_id": work["id"], "resolve_parent": True}, headers=agent.headers
    )
    assert response.status_code == 201, response.text
    child = response.json()
    assert child["type"]["key"] == "work_order" and child["parent_ticket_id"] == parent["id"]
    assert child["requester"]["id"] == requester.id
    assert child["department"]["id"] == parent["department"]["id"]
    assert child["priority"]["key"] == "high" and child["category"]["id"] == work["id"]
    assert child["title"] == f"Orden de trabajo: {parent['title']}"

    updated_parent = client.get(f"/tickets/{parent['id']}", headers=agent.headers).json()
    assert updated_parent["child_ticket_ids"] == [child["id"]]
    assert updated_parent["status"]["category"] == "done"
    assert updated_parent["resolution_note"] == f"Trasladado a orden de trabajo #{child['id']}"
    types = [(e["type"], e["ticket_id"]) for e in published_events if e["type"].startswith("ticket.")]
    assert ("ticket.created", child["id"]) in types
    assert ("ticket.updated", parent["id"]) in types
    assert ("ticket.transitioned", parent["id"]) in types
    # El solicitante ve la orden de trabajo (es suya).
    assert client.get(f"/tickets/{child['id']}", headers=requester.headers).status_code == 200


def test_work_order_without_resolving_parent(client, agent, requester, create_ticket):
    parent = create_ticket(requester)
    response = client.post(
        f"/tickets/{parent['id']}/work-order",
        json={"title": "Instalar antena", "description": "Torre norte"},
        headers=agent.headers,
    )
    assert response.status_code == 201
    assert response.json()["title"] == "Instalar antena"
    parent_now = client.get(f"/tickets/{parent['id']}", headers=agent.headers).json()
    assert parent_now["status"]["key"] == "new" and len(parent_now["child_ticket_ids"]) == 1


def test_work_order_failed_parent_resolution_rolls_back(client, agent, requester, create_ticket, catalog):
    """Si el original no se puede resolver (falta categoría), no se crea la orden."""
    parent = create_ticket(requester)
    response = client.post(
        f"/tickets/{parent['id']}/work-order", json={"resolve_parent": True}, headers=agent.headers
    )
    assert response.status_code == 422
    with SessionLocal() as session:
        assert session.scalar(select(Ticket).where(Ticket.parent_ticket_id == parent["id"])) is None


# --------------------------------------------------------------------------- filtros


def test_list_filters_by_category_and_type(client, agent, requester, create_ticket, catalog):
    mobile = create_ticket(requester, category_id=catalog["mobile"]["id"])
    request = create_ticket(requester, type_key="service_request")
    plain = create_ticket(requester)
    by_category = client.get(f"/tickets?category_id={catalog['mobile']['id']}", headers=agent.headers).json()
    assert [t["id"] for t in by_category["items"]] == [mobile["id"]]
    by_type = client.get("/tickets?type_key=service_request", headers=agent.headers).json()
    assert [t["id"] for t in by_type["items"]] == [request["id"]]
    both = client.get("/tickets?type_key=incident,service_request", headers=agent.headers).json()
    assert {t["id"] for t in both["items"]} == {mobile["id"], request["id"], plain["id"]}
