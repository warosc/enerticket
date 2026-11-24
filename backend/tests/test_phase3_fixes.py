"""Regresiones de la revisión de la Fase 3: categoría exigida según el tipo, solución oficial,
primera respuesta, departamento del AD pendiente, alias aplicados al instante, envíos
programados (cuenta no vigente y reintentos) y detalle recortado del Excel."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal, utcnow
from app.models.catalog import DepartmentAlias, Gerencia, TicketCategory
from app.models.core import Department, Ticket, User
from app.models.reports import ReportTemplate
from app.services.notifications.email import set_smtp_factory
from app.services.reports import builder as builder_mod
from app.services.reports import templates as templates_mod
from app.services.reports import xlsx_export
from app.services.reports.templates import RETRY_DELAYS_MINUTES, run_due_templates
from tests.conftest import DEPARTMENT_SOC, DEPARTMENT_TELECOM, auth_headers
from tests.test_reports_builder import definition, report_data  # noqa: F401  (fixture compartida)

DEFINITION = {
    "title": "Mis tickets",
    "period": "custom",
    "date_from": "2026-11-01",
    "date_to": "2026-11-07",
    "sections": [{"title": "Por tipo", "rows": ["type"], "metrics": ["count"]}],
}
SCHEDULE = {"enabled": True, "frequency": "monthly", "day": 1, "hour": 7, "recipients": ["jefe@energuate.com"]}


def _category(department_id: int, name: str, type_keys: tuple[str, ...]) -> int:
    with SessionLocal() as session:
        category = TicketCategory(
            department_id=department_id, name=name, type_keys=list(type_keys), active=True, sort_order=0
        )
        session.add(category)
        session.commit()
        return category.id


def _ticket(ticket_id: int) -> Ticket:
    with SessionLocal() as session:
        ticket = session.get(Ticket, ticket_id)
        session.expunge(ticket)
        return ticket


# --------------------------------------------------------------------------- categoría según el tipo


def test_resolve_without_category_when_no_category_admits_type(
    client, agent, requester, create_ticket, transition, ids
):
    """Telecomunicaciones solo tiene categorías de incidencias/peticiones: una orden de trabajo
    no se puede clasificar y, por lo tanto, se resuelve sin categoría."""
    telecom = ids["departments"][DEPARTMENT_TELECOM]
    category_id = _category(telecom, "Telefonía Móvil", ("incident", "service_request"))
    parent = create_ticket(requester, category_id=category_id)
    response = client.post(f"/tickets/{parent['id']}/work-order", json={}, headers=agent.headers)
    assert response.status_code == 201, response.text
    work_order = response.json()
    assert work_order["category"] is None
    # No se puede clasificar: ninguna categoría admite órdenes de trabajo...
    patch = client.patch(
        f"/tickets/{work_order['id']}", json={"category_id": category_id}, headers=agent.headers
    )
    assert patch.status_code == 422 and "no admite" in patch.json()["detail"]
    # ...así que resolverla no la exige.
    assert transition(agent, work_order["id"], "in_progress").status_code == 200
    resolved = transition(agent, work_order["id"], "resolved")
    assert resolved.status_code == 200, resolved.text

    # Una incidencia del mismo departamento sin categoría sigue exigiéndola.
    incident = create_ticket(requester)
    transition(agent, incident["id"], "in_progress")
    blocked = transition(agent, incident["id"], "resolved")
    assert blocked.status_code == 422 and "Clasifique" in blocked.json()["detail"]


def test_work_order_resolve_parent_ignores_categories_of_other_types(
    client, agent, requester, create_ticket, ids
):
    """El SAVEPOINT de la orden de trabajo resuelve el original si ninguna categoría admite su tipo."""
    telecom = ids["departments"][DEPARTMENT_TELECOM]
    _category(telecom, "Instalaciones", ("work_order",))
    parent = create_ticket(requester)  # incidencia: ninguna categoría la admite
    response = client.post(
        f"/tickets/{parent['id']}/work-order", json={"resolve_parent": True}, headers=agent.headers
    )
    assert response.status_code == 201, response.text
    assert client.get(f"/tickets/{parent['id']}", headers=agent.headers).json()["status"]["category"] == "done"


# --------------------------------------------------------------------------- solución oficial


def test_requester_closing_resolved_ticket_cannot_rewrite_solution(
    client, agent, requester, create_ticket, transition
):
    ticket = create_ticket(requester)
    transition(agent, ticket["id"], "in_progress")
    assert transition(agent, ticket["id"], "resolved", resolution_note="Se cambió el router").status_code == 200
    closed = transition(requester, ticket["id"], "closed", resolution_note="No se resolvió nada")
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert body["status"]["key"] == "closed"
    assert body["resolution_note"] == "Se cambió el router"
    assert body["resolved_by"]["id"] == agent.id


def test_agent_can_update_solution_when_closing(client, agent, requester, create_ticket, transition):
    ticket = create_ticket(requester)
    transition(agent, ticket["id"], "in_progress")
    transition(agent, ticket["id"], "resolved", resolution_note="Primera versión")
    closed = transition(agent, ticket["id"], "closed", resolution_note="Versión final")
    assert closed.status_code == 200 and closed.json()["resolution_note"] == "Versión final"


def test_requester_closing_open_ticket_still_gives_solution(client, requester, create_ticket, transition):
    ticket = create_ticket(requester)
    closed = transition(requester, ticket["id"], "closed", resolution_note="Ya no hace falta")
    assert closed.status_code == 200, closed.text
    assert closed.json()["resolution_note"] == "Ya no hace falta"


# --------------------------------------------------------------------------- primera respuesta


@pytest.fixture
def commercial_requester(make_user):
    with SessionLocal() as session:
        gerencia = Gerencia(name="Gerencia Comercial", external_group="GG-Ger-Comercial", active=True)
        session.add(gerencia)
        session.flush()
        department = Department(name="Comercial", gerencia_id=gerencia.id, serves_tickets=False, active=True)
        session.add(department)
        session.commit()
        department_id = department.id
    seller = make_user("vendedor")
    with SessionLocal() as session:
        session.get(User, seller.id).department_id = department_id
        session.commit()
    return seller


def test_gerencia_viewer_agent_comment_is_not_first_response(
    client, agent, create_ticket, commercial_requester, make_user
):
    ticket = create_ticket(commercial_requester)  # atendido por Telecomunicaciones
    soc_viewer = make_user(
        "jefe.soc", roles=("agent",), departments=(DEPARTMENT_SOC,), groups="GG-Ger-Comercial"
    )
    response = client.post(
        f"/tickets/{ticket['id']}/comments", json={"body": "Lo revisamos", "visibility": "public"},
        headers=soc_viewer.headers,
    )
    assert response.status_code == 201, response.text
    assert _ticket(ticket["id"]).first_response_at is None
    # El área que atiende sí cumple la primera respuesta.
    client.post(
        f"/tickets/{ticket['id']}/comments", json={"body": "En proceso", "visibility": "public"},
        headers=agent.headers,
    )
    assert _ticket(ticket["id"]).first_response_at is not None


# --------------------------------------------------------------------------- departamento del AD


def _headers(username: str, department: str | None = None) -> dict[str, str]:
    headers = auth_headers(username, "requester")
    if department is not None:
        headers["X-Dev-Department"] = department
    return headers


def _department(name: str, gerencia: str | None = None) -> int:
    with SessionLocal() as session:
        gerencia_id = None
        if gerencia:
            g = Gerencia(name=gerencia, active=True)
            session.add(g)
            session.flush()
            gerencia_id = g.id
        department = Department(name=name, serves_tickets=False, active=True, gerencia_id=gerencia_id)
        session.add(department)
        session.commit()
        return department.id


def test_new_pending_ad_value_clears_previous_department(client, ids):
    comercial = _department("Comercial", "Gerencia Comercial")
    me = client.get("/me", headers=_headers("pedro", "07 COMERCIAL")).json()
    assert me["department"]["id"] == comercial
    # Cambia en el AD a un valor nuevo (pendiente): deja de pertenecer a Comercial.
    me = client.get("/me", headers=_headers("pedro", "PROYECTOS ESPECIALES")).json()
    assert me["department"] is None and me["gerencia"] is None
    ticket = client.post(
        "/tickets",
        json={"title": "Sin acceso", "department_id": ids["departments"][DEPARTMENT_TELECOM]},
        headers=_headers("pedro", "PROYECTOS ESPECIALES"),
    ).json()
    assert ticket["requester_department"] is None


def test_manual_override_survives_unchanged_pending_value(client, admin):
    finanzas = _department("Finanzas")
    client.get("/me", headers=_headers("pedro", "VALOR RARO"))
    with SessionLocal() as session:
        user_id = session.scalar(select(User.id).where(User.username == "pedro"))
    response = client.patch(
        f"/admin/users/{user_id}/profile", json={"department_id": finanzas}, headers=admin.headers
    )
    assert response.status_code == 200
    # El valor del AD no cambió: se respeta el ajuste manual.
    me = client.get("/me", headers=_headers("pedro", "Valor raro")).json()
    assert me["department"]["id"] == finanzas


def test_alias_assignment_applies_now_and_backfills_tickets(client, admin, agent, ids, make_user):
    ventas = _department("Ventas", "Gerencia Comercial")
    headers = _headers("pedro", "07 VENTAS CORPORATIVAS")
    ticket = client.post(
        "/tickets",
        json={"title": "Impresora", "department_id": ids["departments"][DEPARTMENT_TELECOM]},
        headers=headers,
    ).json()
    assert ticket["requester_department"] is None
    other = client.post(
        "/tickets",
        json={"title": "Otro", "department_id": ids["departments"][DEPARTMENT_TELECOM]},
        headers=_headers("ana.otra", "CONTABILIDAD GENERAL"),
    ).json()
    with SessionLocal() as session:
        alias_id = session.scalar(
            select(DepartmentAlias.id).where(DepartmentAlias.normalized_value == "VENTAS CORPORATIVAS")
        )
        user_id = session.scalar(select(User.id).where(User.username == "pedro"))

    response = client.patch(
        f"/admin/department-aliases/{alias_id}", json={"department_id": ventas}, headers=admin.headers
    )
    assert response.status_code == 200, response.text
    with SessionLocal() as session:
        # Sin esperar al siguiente login.
        assert session.get(User, user_id).department_id == ventas
        assert session.get(Ticket, ticket["id"]).requester_department_id == ventas
        assert session.get(Ticket, other["id"]).requester_department_id is None

    # Retirar el departamento del alias lo retira de sus usuarios (los tickets conservan su foto).
    response = client.patch(
        f"/admin/department-aliases/{alias_id}", json={"department_id": None}, headers=admin.headers
    )
    assert response.status_code == 200
    with SessionLocal() as session:
        assert session.get(User, user_id).department_id is None
        assert session.get(Ticket, ticket["id"]).requester_department_id == ventas


# --------------------------------------------------------------------------- envíos programados


class FailingSmtp:
    fail = True
    sent: list[Any] = []

    def login(self, *_: Any) -> None:  # pragma: no cover
        pass

    def send_message(self, message: Any) -> None:
        if FailingSmtp.fail:
            raise OSError("servidor de correo no disponible")
        FailingSmtp.sent.append(message)

    def quit(self) -> None:
        pass


@pytest.fixture
def smtp(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_user", "")
    FailingSmtp.fail = False
    FailingSmtp.sent = []
    set_smtp_factory(FailingSmtp)
    yield FailingSmtp
    set_smtp_factory(None)


# Reloj fijo para los reintentos: lejos del siguiente periodo (1 de diciembre, 07:00 GT).
FIXED_NOW = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)


def _due_template(client, persona, due_at: datetime | None = None) -> int:
    response = client.post(
        "/reports/templates",
        json={"name": "Programada", "definition": DEFINITION, "schedule": SCHEDULE},
        headers=persona.headers,
    )
    assert response.status_code == 201, response.text
    with SessionLocal() as session:
        row = session.get(ReportTemplate, response.json()["id"])
        row.next_run_at = (due_at or utcnow()) - timedelta(minutes=1)
        session.commit()
        return row.id


def test_scheduled_report_skips_stale_owner(client, admin, smtp, monkeypatch):
    monkeypatch.setattr(settings, "account_stale_days", 45)
    template_id = _due_template(client, admin)
    with SessionLocal() as session:
        session.get(User, admin.id).last_login_at = utcnow() - timedelta(days=90)
        session.commit()
    assert run_due_templates() == {"owner_stale": 1}
    assert smtp.sent == []
    with SessionLocal() as session:
        row = session.get(ReportTemplate, template_id)
        assert row.last_status == "owner_stale" and row.next_run_at is not None


def test_scheduled_report_retries_transient_errors(client, admin, smtp):
    now = FIXED_NOW
    template_id = _due_template(client, admin, now)
    smtp.fail = True
    with SessionLocal() as session:
        regular_next = templates_mod.next_run_at(session.get(ReportTemplate, template_id).schedule, now)

    for attempt, delay in enumerate(RETRY_DELAYS_MINUTES, start=1):
        assert run_due_templates(now) == {"retrying": 1}
        with SessionLocal() as session:
            row = session.get(ReportTemplate, template_id)
            assert row.last_status == "retrying" and row.retry_count == attempt
            assert row.next_run_at == now + timedelta(minutes=delay)
        now = row.next_run_at

    # Reintentos agotados: queda en error hasta el siguiente periodo.
    assert run_due_templates(now) == {"error": 1}
    with SessionLocal() as session:
        row = session.get(ReportTemplate, template_id)
        assert row.last_status == "error" and row.retry_count == 0
        assert row.next_run_at == regular_next


def test_scheduled_report_retry_succeeds(client, admin, smtp):
    now = FIXED_NOW
    template_id = _due_template(client, admin, now)
    smtp.fail = True
    assert run_due_templates(now) == {"retrying": 1}
    smtp.fail = False
    later = now + timedelta(minutes=RETRY_DELAYS_MINUTES[0])
    assert run_due_templates(later) == {"sent": 1}
    assert len(smtp.sent) == 1
    with SessionLocal() as session:
        row = session.get(ReportTemplate, template_id)
        assert row.last_status == "sent" and row.retry_count == 0
        assert row.next_run_at > later + timedelta(days=1)


# --------------------------------------------------------------------------- detalle recortado


def test_detail_sheet_warns_when_truncated(client, admin, report_data, monkeypatch):  # noqa: F811
    for module in (builder_mod, templates_mod, xlsx_export):
        monkeypatch.setattr(module, "MAX_DETAIL_ROWS", 3)
    body = {"definition": definition(), "include_detail": True}
    response = client.post("/reports/builder/export.xlsx", json=body, headers=admin.headers)
    assert response.status_code == 200, response.text
    workbook = load_workbook(io.BytesIO(response.content))
    rows = list(workbook["Detalle"].iter_rows(values_only=True))
    assert len(rows) - 1 == 3
    note = rows[0][-1]
    assert "Detalle recortado" in note and "3 de 5" in note
    section_rows = list(workbook[workbook.sheetnames[0]].iter_rows(values_only=True))
    assert section_rows[4][0] == "Aviso:" and "Detalle recortado" in section_rows[4][1]


def test_detail_sheet_without_truncation_has_no_warning(client, admin, report_data):  # noqa: F811
    body = {"definition": definition(), "include_detail": True}
    workbook = load_workbook(
        io.BytesIO(client.post("/reports/builder/export.xlsx", json=body, headers=admin.headers).content)
    )
    header = next(workbook["Detalle"].iter_rows(values_only=True))
    assert not any("recortado" in str(value or "") for value in header)
