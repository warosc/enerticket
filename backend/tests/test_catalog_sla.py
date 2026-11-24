"""Catálogo de servicio — SLA por categoría/subcategoría y VIP, especificidad y re-evaluación."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.catalog import TicketCategory, TicketSubcategory
from app.models.sla import BusinessCalendar, SlaPolicy
from app.services.sla.engine import policy_matches, policy_sort_key
from tests.conftest import DEPARTMENT_TELECOM


@pytest.fixture
def sla_seed():
    seed = importlib.import_module("app.seeds.10_sla")
    with SessionLocal() as session:
        seed.run(session)
        session.commit()
        return session.scalar(select(BusinessCalendar.id).where(BusinessCalendar.name == "Horario hábil Guatemala"))


@pytest.fixture
def mobile(ids) -> dict[str, Any]:
    with SessionLocal() as session:
        category = TicketCategory(
            department_id=ids["departments"][DEPARTMENT_TELECOM], name="Telefonía Móvil",
            type_keys=[], active=True, sort_order=0,
        )
        session.add(category)
        session.flush()
        broken = TicketSubcategory(category_id=category.id, name="Equipo dañado", active=True, sort_order=0)
        line = TicketSubcategory(category_id=category.id, name="Línea nueva", active=True, sort_order=1)
        session.add_all([broken, line])
        session.commit()
        return {"id": category.id, "broken": broken.id, "line": line.id}


def _policy(client, admin, calendar_id, name, **conditions) -> dict[str, Any]:
    body = {
        "name": name,
        "calendar_id": calendar_id,
        "resolution_minutes": conditions.pop("resolution_minutes", 600),
        "precedence": conditions.pop("precedence", 5),
        **conditions,
    }
    response = client.post("/sla/policies", json=body, headers=admin.headers)
    assert response.status_code == 201, response.text
    return response.json()


def _resolution_policy(client, persona, ticket_id) -> str | None:
    sla = client.get(f"/tickets/{ticket_id}/sla", headers=persona.headers).json()
    if not sla or not sla.get("resolution"):
        return None
    policies = {p["id"]: p["name"] for p in client.get("/sla/policies", headers=persona.headers).json()}
    return policies[sla["resolution"]["policy_id"]]


def test_policy_schema_roundtrip_and_validation(client, admin, sla_seed, mobile):
    created = _policy(
        client, admin, sla_seed, "ANS Móvil", category_ids=[mobile["id"]],
        subcategory_ids=[mobile["broken"]], vip_only=True,
    )
    assert created["category_ids"] == [mobile["id"]] and created["subcategory_ids"] == [mobile["broken"]]
    assert created["vip_only"] is True
    patched = client.patch(
        f"/sla/policies/{created['id']}", json={"vip_only": False, "category_ids": []}, headers=admin.headers
    ).json()
    assert patched["vip_only"] is False and patched["category_ids"] == []
    bad = client.post(
        "/sla/policies",
        json={"name": "Mala", "calendar_id": sla_seed, "resolution_minutes": 60, "subcategory_ids": [99999]},
        headers=admin.headers,
    )
    assert bad.status_code == 422 and "Subcategorías inexistentes" in bad.json()["detail"]


def test_subcategory_policy_beats_category_policy(client, admin, agent, requester, sla_seed, mobile, create_ticket):
    # Misma precedencia: gana la más específica aunque se haya creado después.
    _policy(client, admin, sla_seed, "ANS Móvil", category_ids=[mobile["id"]], resolution_minutes=480)
    _policy(client, admin, sla_seed, "ANS Equipo dañado", subcategory_ids=[mobile["broken"]], resolution_minutes=120)
    broken = create_ticket(requester, category_id=mobile["id"], subcategory_id=mobile["broken"])
    line = create_ticket(requester, category_id=mobile["id"], subcategory_id=mobile["line"])
    plain = create_ticket(requester)
    assert _resolution_policy(client, agent, broken["id"]) == "ANS Equipo dañado"
    assert _resolution_policy(client, agent, line["id"]) == "ANS Móvil"
    assert _resolution_policy(client, agent, plain["id"]) == "Media - horario hábil"
    names = [p["name"] for p in client.get("/sla/policies", headers=agent.headers).json()]
    assert names.index("ANS Equipo dañado") < names.index("ANS Móvil")


def test_vip_only_policy(client, admin, agent, make_user, sla_seed, create_ticket):
    _policy(client, admin, sla_seed, "VIP", vip_only=True, resolution_minutes=60)
    vip = make_user("gerente.general")
    with SessionLocal() as session:
        from app.models.core import User

        session.get(User, vip.id).vip = True
        session.commit()
    normal = make_user("empleado")
    assert _resolution_policy(client, agent, create_ticket(vip)["id"]) == "VIP"
    assert _resolution_policy(client, agent, create_ticket(normal)["id"]) == "Media - horario hábil"


def test_changing_category_reevaluates_sla(client, admin, agent, requester, sla_seed, mobile, create_ticket):
    _policy(client, admin, sla_seed, "ANS Equipo dañado", subcategory_ids=[mobile["broken"]], resolution_minutes=120)
    ticket = create_ticket(requester)
    assert _resolution_policy(client, agent, ticket["id"]) == "Media - horario hábil"
    response = client.patch(
        f"/tickets/{ticket['id']}",
        json={"category_id": mobile["id"], "subcategory_id": mobile["broken"]},
        headers=agent.headers,
    )
    assert response.status_code == 200, response.text
    assert _resolution_policy(client, agent, ticket["id"]) == "ANS Equipo dañado"
    assert response.json()["sla"]["resolution"]["target_minutes"] == 120
    # Quitar la clasificación vuelve a la política general.
    client.patch(f"/tickets/{ticket['id']}", json={"category_id": None}, headers=agent.headers)
    assert _resolution_policy(client, agent, ticket["id"]) == "Media - horario hábil"


def test_policy_matches_unit():
    policy = SlaPolicy(
        id=1, name="x", calendar_id=1, priority_keys=[], type_keys=[], department_ids=[3],
        category_ids=[10], subcategory_ids=[], vip_only=True, precedence=10,
    )
    common = {"priority_key": "low", "type_key": "incident", "department_id": 3}
    assert policy_matches(policy, **common, category_id=10, vip=True)
    assert not policy_matches(policy, **common, category_id=10, vip=False)
    assert not policy_matches(policy, **common, category_id=11, vip=True)
    assert not policy_matches(policy, **common, vip=True)
    general = SlaPolicy(id=2, precedence=10, category_ids=[], subcategory_ids=[])
    by_sub = SlaPolicy(id=3, precedence=10, category_ids=[], subcategory_ids=[5])
    earlier = SlaPolicy(id=4, precedence=1, category_ids=[], subcategory_ids=[])
    ordered = sorted([general, policy, by_sub, earlier], key=policy_sort_key)
    # Primero la más específica (subcategoría, categoría), luego precedencia dentro del nivel.
    assert [p.id for p in ordered] == [3, 1, 4, 2]


def test_category_policy_beats_lower_precedence_general_policy(client, admin, agent, requester, sla_seed, mobile, create_ticket):
    # Las políticas por prioridad sembradas tienen precedencia 10-40; una ANS de catálogo con 50 igual gana.
    _policy(client, admin, sla_seed, "ANS Móvil 50", category_ids=[mobile["id"]], resolution_minutes=480, precedence=50)
    ticket = create_ticket(requester, category_id=mobile["id"], subcategory_id=mobile["line"])
    assert _resolution_policy(client, agent, ticket["id"]) == "ANS Móvil 50"
