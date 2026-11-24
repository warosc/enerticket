"""Catálogo de servicio — identidad: alias del AD, departamento/VIP en el login, /me y
visibilidad de solo lectura por gerencia."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.catalog import normalize_ad_value, resolve_department_alias
from app.core.db import SessionLocal
from app.models.catalog import DepartmentAlias, Gerencia
from app.models.core import Department, User
from tests.conftest import DEPARTMENT_SOC, DEPARTMENT_TELECOM, auth_headers


# --------------------------------------------------------------------------- normalización


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("07 COMERCIAL", "COMERCIAL"),
        ("Comercial", "COMERCIAL"),
        ("  comercial  ", "COMERCIAL"),
        ("0903 Gerencia   Financiera", "GERENCIA FINANCIERA"),
        ("Tecnología", "TECNOLOGIA"),
        ("12-Seguridad Informática", "SEGURIDAD INFORMATICA"),
        ("Ñandú\tAdministración", "NANDU ADMINISTRACION"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_ad_value(raw, expected):
    assert normalize_ad_value(raw) == expected


def test_prefixed_and_plain_values_share_alias():
    assert normalize_ad_value("07 COMERCIAL") == normalize_ad_value("Comercial")
    assert normalize_ad_value("Seguridad Informática") == normalize_ad_value("SEGURIDAD INFORMATICA")


def _make_department(name: str, *, gerencia: str | None = None, external_group: str | None = None) -> int:
    with SessionLocal() as session:
        gerencia_id = None
        if gerencia:
            g = session.scalar(select(Gerencia).where(Gerencia.name == gerencia))
            if g is None:
                g = Gerencia(name=gerencia, external_group=external_group, active=True)
                session.add(g)
                session.flush()
            gerencia_id = g.id
        department = Department(name=name, serves_tickets=False, active=True, gerencia_id=gerencia_id)
        session.add(department)
        session.commit()
        return department.id


def test_resolve_alias_creates_pending_once():
    with SessionLocal() as session:
        assert resolve_department_alias(session, "07 Contabilidad") is None
        assert resolve_department_alias(session, "CONTABILIDAD") is None
        session.commit()
        aliases = session.scalars(select(DepartmentAlias)).all()
    assert [(a.normalized_value, a.raw_value, a.department_id) for a in aliases] == [
        ("CONTABILIDAD", "07 Contabilidad", None)
    ]


def test_resolve_alias_matches_department_by_name():
    department_id = _make_department("Comercial")
    with SessionLocal() as session:
        department = resolve_department_alias(session, "07 COMERCIAL")
        session.commit()
        assert department is not None and department.id == department_id


# --------------------------------------------------------------------------- login (JIT)


def _headers(username: str, *, department: str | None = None, groups: str | None = None, roles="requester"):
    headers = auth_headers(username, roles, groups)
    if department is not None:
        headers["X-Dev-Department"] = department
    return headers


def test_login_with_unknown_department_creates_pending_alias(client, admin, published_events):
    response = client.get("/me", headers=_headers("pedro.ventas", department="07 VENTAS"))
    assert response.status_code == 200
    body = response.json()
    assert body["department"] is None and body["gerencia"] is None
    pending = client.get("/admin/department-aliases?pending=true", headers=admin.headers).json()
    assert [a["normalized_value"] for a in pending] == ["VENTAS"]
    assert pending[0]["raw_value"] == "07 VENTAS" and pending[0]["pending"] is True
    events = [e for e in published_events if e["type"] == "catalog.alias_pending"]
    assert len(events) == 1 and events[0]["data"]["normalized_value"] == "VENTAS"
    assert events[0]["requester_id"] is None and events[0]["ticket_id"] == 0

    # Segundo login con el mismo valor: no duplica el alias ni el aviso.
    client.get("/me", headers=_headers("pedro.ventas", department="Ventas"))
    assert len(client.get("/admin/department-aliases", headers=admin.headers).json()) == 1
    assert len([e for e in published_events if e["type"] == "catalog.alias_pending"]) == 1


def test_assigned_alias_sets_department_on_next_login(client, admin):
    department_id = _make_department("Ventas Corporativas", gerencia="Gerencia Comercial")
    client.get("/me", headers=_headers("pedro.ventas", department="07 VENTAS"))
    alias = client.get("/admin/department-aliases?pending=true", headers=admin.headers).json()[0]
    response = client.patch(
        f"/admin/department-aliases/{alias['id']}", json={"department_id": department_id}, headers=admin.headers
    )
    assert response.status_code == 200 and response.json()["pending"] is False
    me = client.get("/me", headers=_headers("pedro.ventas", department="Ventas")).json()
    assert me["department"] == {"id": department_id, "name": "Ventas Corporativas"}
    assert me["gerencia"]["name"] == "Gerencia Comercial"
    # Un login sin el claim no borra el departamento.
    me = client.get("/me", headers=_headers("pedro.ventas")).json()
    assert me["department"]["id"] == department_id


def test_vip_follows_group_membership(client, settings_vip_group):
    me = client.get("/me", headers=_headers("ceo", groups="GG-SD-VIP,Otro")).json()
    assert me["vip"] is True
    # Sin cabecera de grupos (claim ausente) no se toca.
    assert client.get("/me", headers=_headers("ceo")).json()["vip"] is True
    # Con grupos que no incluyen el VIP: deja de serlo (sin distinguir mayúsculas).
    assert client.get("/me", headers=_headers("ceo", groups="otro")).json()["vip"] is False
    assert client.get("/me", headers=_headers("ceo", groups="gg-sd-vip")).json()["vip"] is True


@pytest.fixture
def settings_vip_group(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "vip_group", "GG-SD-VIP")


def test_ticket_copies_requester_department_and_vip(client, ids, settings_vip_group):
    department_id = _make_department("Comercial", gerencia="Gerencia Comercial")
    headers = _headers("vip.user", department="07 COMERCIAL", groups="GG-SD-VIP")
    response = client.post(
        "/tickets",
        json={"title": "Sin correo", "department_id": ids["departments"][DEPARTMENT_TELECOM]},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    ticket = response.json()
    assert ticket["vip"] is True
    assert ticket["requester_department"] == {"id": department_id, "name": "Comercial"}
    assert ticket["gerencia"]["name"] == "Gerencia Comercial"
    assert ticket["channel"] == "portal"


def test_admin_profile_override(client, admin, requester, agent):
    department_id = _make_department("Finanzas")
    url = f"/admin/users/{requester.id}/profile"
    assert client.patch(url, json={"vip": True}, headers=agent.headers).status_code == 403
    response = client.patch(url, json={"vip": True, "department_id": department_id}, headers=admin.headers)
    assert response.status_code == 200, response.text
    assert response.json()["vip"] is True and response.json()["department"]["id"] == department_id
    assert client.patch(url, json={"department_id": 99999}, headers=admin.headers).status_code == 422
    me = client.get("/me", headers=requester.headers).json()
    assert me["vip"] is True and me["department"]["id"] == department_id
    users = client.get("/users?q=carlos", headers=agent.headers).json()
    assert users[0]["department"]["name"] == "Finanzas" and users[0]["vip"] is True


# --------------------------------------------------------------------------- gerencias


@pytest.fixture
def gerencia_setup(make_user):
    """Gerencia Comercial (grupo AD GG-Ger-Comercial) con el departamento solicitante Comercial,
    y Gerencia Financiera con Contabilidad."""
    comercial = _make_department("Comercial", gerencia="Gerencia Comercial", external_group="GG-Ger-Comercial")
    contabilidad = _make_department("Contabilidad", gerencia="Gerencia Financiera")
    seller = make_user("vendedor")
    accountant = make_user("contador")
    with SessionLocal() as session:
        session.get(User, seller.id).department_id = comercial
        session.get(User, accountant.id).department_id = contabilidad
        session.commit()
        gerencias = {g.name: g.id for g in session.scalars(select(Gerencia)).all()}
    return {"seller": seller, "accountant": accountant, "gerencias": gerencias}


def test_gerencia_viewer_by_ad_group_sees_only_their_gerencia(
    client, create_ticket, transition, gerencia_setup, make_user
):
    commercial_ticket = create_ticket(gerencia_setup["seller"])
    finance_ticket = create_ticket(gerencia_setup["accountant"])
    manager = make_user("gerente.comercial", groups="GG-Ger-Comercial")

    me = client.get("/me", headers=manager.headers).json()
    assert me["viewer_gerencia_ids"] == [gerencia_setup["gerencias"]["Gerencia Comercial"]]
    listing = client.get("/tickets", headers=manager.headers).json()
    assert [t["id"] for t in listing["items"]] == [commercial_ticket["id"]]
    assert client.get(f"/tickets/{commercial_ticket['id']}", headers=manager.headers).status_code == 200
    assert client.get(f"/tickets/{finance_ticket['id']}", headers=manager.headers).status_code == 404

    # Solo lectura: no edita ni transiciona.
    assert (
        client.patch(f"/tickets/{commercial_ticket['id']}", json={"title": "Otro"}, headers=manager.headers)
        .status_code == 403
    )
    assert transition(manager, commercial_ticket["id"], "in_progress").status_code == 403
    assert transition(manager, commercial_ticket["id"], "closed").status_code == 403


def test_gerencia_viewer_agent_cannot_edit_other_area(client, create_ticket, gerencia_setup, make_user, transition):
    ticket = create_ticket(gerencia_setup["seller"])  # atendido por Telecomunicaciones
    soc_manager = make_user(
        "jefe.soc", roles=("agent",), departments=(DEPARTMENT_SOC,), groups="GG-Ger-Comercial"
    )
    assert client.get(f"/tickets/{ticket['id']}", headers=soc_manager.headers).status_code == 200
    response = client.patch(f"/tickets/{ticket['id']}", json={"title": "Cambio"}, headers=soc_manager.headers)
    assert response.status_code == 403
    assert transition(soc_manager, ticket["id"], "in_progress").status_code == 403
    # Sin el grupo deja de verlo.
    soc_manager.headers["X-Dev-Groups"] = ""
    assert client.get(f"/tickets/{ticket['id']}", headers=soc_manager.headers).status_code == 404


def test_manual_gerencia_viewers(client, admin, create_ticket, gerencia_setup, make_user):
    ticket = create_ticket(gerencia_setup["accountant"])
    cfo = make_user("director.financiero")
    assert client.get(f"/tickets/{ticket['id']}", headers=cfo.headers).status_code == 404
    gerencia_id = gerencia_setup["gerencias"]["Gerencia Financiera"]
    url = f"/admin/gerencias/{gerencia_id}/viewers"
    assert client.put(url, json={"user_ids": [cfo.id]}, headers=cfo.headers).status_code == 403
    assert client.put(url, json={"user_ids": [99999]}, headers=admin.headers).status_code == 422
    response = client.put(url, json={"user_ids": [cfo.id]}, headers=admin.headers)
    assert response.status_code == 200 and [u["id"] for u in response.json()] == [cfo.id]
    assert client.get(f"/tickets/{ticket['id']}", headers=cfo.headers).status_code == 200
    assert client.get(url, headers=admin.headers).json()[0]["username"] == "director.financiero"

    # Gerencia desactivada: deja de dar acceso.
    client.patch(f"/admin/gerencias/{gerencia_id}", json={"active": False}, headers=admin.headers)
    assert client.get(f"/tickets/{ticket['id']}", headers=cfo.headers).status_code == 404
    client.patch(f"/admin/gerencias/{gerencia_id}", json={"active": True}, headers=admin.headers)
    assert client.put(url, json={"user_ids": []}, headers=admin.headers).json() == []
    assert client.get(f"/tickets/{ticket['id']}", headers=cfo.headers).status_code == 404


def test_internal_comments_stay_hidden_from_gerencia_viewers(client, agent, create_ticket, gerencia_setup, make_user):
    ticket = create_ticket(gerencia_setup["seller"])
    client.post(
        f"/tickets/{ticket['id']}/comments", json={"body": "Nota interna", "visibility": "internal"},
        headers=agent.headers,
    )
    manager = make_user("gerente.comercial", roles=("agent",), groups="GG-Ger-Comercial")
    comments = client.get(f"/tickets/{ticket['id']}/comments", headers=manager.headers).json()
    assert comments == []


def test_department_claim_from_token():
    from app.core.security import identity_from_claims

    assert identity_from_claims({"sub": "a", "department": " 07 COMERCIAL "}).department == "07 COMERCIAL"
    assert identity_from_claims({"sub": "a", "department": ["", "Ventas"]}).department == "Ventas"
    assert identity_from_claims({"sub": "a", "department": ""}).department is None
    assert identity_from_claims({"sub": "a"}).department is None
