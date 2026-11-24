"""Generador de reportes: alcance por rol, lista blanca, límites, cálculos, periodos y .xlsx."""

from __future__ import annotations

import io
from datetime import date, datetime, timezone
from typing import Any

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.catalog import Gerencia, GerenciaViewer, TicketCategory, TicketSubcategory
from app.models.core import Department, Ticket, TicketType, User, WorkflowState
from app.models.sla import BusinessCalendar, SlaInstance, SlaPolicy
from app.services.reports.builder import ReportDefinition, resolve_period
from tests.conftest import DEPARTMENT_SOC, DEPARTMENT_TELECOM

NOV = {"period": "custom", "date_from": "2026-11-01", "date_to": "2026-11-07"}
ALL_METRICS = [
    "count", "resolution_hours_avg", "resolution_hours_sum", "resolution_hours_median",
    "resolution_hours_max", "sla_target_hours", "sla_compliance_pct", "sla_breached_count",
]
INJECTION_TITLE = '=HYPERLINK("http://evil.example","clic")'


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def definition(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "Prueba",
        "date_field": "created_at",
        **NOV,
        "sections": [{"title": "Por categoría", "rows": ["category"], "metrics": ["count"]}],
    }
    body.update(overrides)
    return body


def run(client, persona, body: dict[str, Any]):
    return client.post("/reports/builder/run", json=body, headers=persona.headers)


def section_of(response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return response.json()["sections"][0]


def rows_by_label(section: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {" / ".join(k["label"] for k in row["keys"]): row for row in section["rows"]}


# --------------------------------------------------------------------------- escenario


@pytest.fixture
def report_data(db, ids, create_ticket, requester, other_requester, agent, other_agent) -> dict[str, Any]:
    """Escenario fijo (America/Guatemala, UTC-6), noviembre de 2026.

    | T  | depto   | tipo    | categoría       | creado (UTC)  | resuelto (UTC) | h hábiles | SLA res.        |
    |----|---------|---------|-----------------|---------------|----------------|-----------|-----------------|
    | T1 | Telecom | incid.  | Enlaces / Fibra | 02-nov 15:00  | 03-nov 01:00   | 2         | met, 480 min    |
    | T2 | Telecom | incid.  | Enlaces         | 03-nov 03:00* | (abierto)      | —         | running, 480    |
    | T3 | Telecom | petición| Telefonía (VIP) | 05-nov 12:00  | 06-nov 12:00   | 10        | breached, 240   |
    | T4 | SOC     | incid.  | Alertas         | 04-nov 12:00  | 05-nov 12:00   | 5         | met, 600        |
    | T5 | Telecom | incid.  | Telefonía       | 20-oct 12:00  | 04-nov 12:00   | 15        | —               |
    | T6 | Telecom | incid.  | Enlaces         | 06-nov 12:00  | 06-nov 18:00   | 6         | met, 480        |

    (*) 21:00 del 2 de noviembre en Guatemala. Solicitantes: T1, T2, T6 Carlos (Comercial,
    Gerencia Comercial); T3, T4, T5 Sofía (Finanzas, Gerencia Financiera).
    """
    telecom = ids["departments"][DEPARTMENT_TELECOM]
    soc = ids["departments"][DEPARTMENT_SOC]
    comercial_g = Gerencia(name="Gerencia Comercial", active=True)
    finanzas_g = Gerencia(name="Gerencia Financiera", active=True)
    db.add_all([comercial_g, finanzas_g])
    db.flush()
    comercial = Department(name="Comercial", gerencia_id=comercial_g.id, serves_tickets=False, active=True)
    finanzas = Department(name="Finanzas", gerencia_id=finanzas_g.id, serves_tickets=False, active=True)
    db.add_all([comercial, finanzas])
    db.flush()
    enlaces = TicketCategory(department_id=telecom, name="Enlaces", type_keys=[], active=True, sort_order=0)
    telefonia = TicketCategory(department_id=telecom, name="Telefonía", type_keys=[], active=True, sort_order=1)
    alertas = TicketCategory(department_id=soc, name="Alertas", type_keys=[], active=True, sort_order=0)
    db.add_all([enlaces, telefonia, alertas])
    db.flush()
    fibra = TicketSubcategory(category_id=enlaces.id, name="Fibra", active=True, sort_order=0)
    db.add(fibra)
    db.get(User, requester.id).department_id = comercial.id
    db.get(User, other_requester.id).department_id = finanzas.id
    db.commit()

    t = {
        "t1": create_ticket(requester, title="Caída de enlace")["id"],
        "t2": create_ticket(requester, title="Enlace intermitente")["id"],
        "t3": create_ticket(other_requester, title=INJECTION_TITLE, type_key="service_request")["id"],
        "t4": create_ticket(other_requester, department=DEPARTMENT_SOC, title="Alerta IDS")["id"],
        "t5": create_ticket(other_requester, title="Línea sin tono")["id"],
        "t6": create_ticket(requester, title="Cambio de puerto")["id"],
    }
    states = {s.key: s.id for s in db.scalars(select(WorkflowState)).all()}

    def put(key: str, *, created, resolved=None, minutes=None, category=None, subcategory=None,
            status="resolved", assignee=None, vip=False, note=None) -> None:
        ticket = db.get(Ticket, t[key])
        ticket.created_at = created
        ticket.resolved_at = resolved
        ticket.business_resolution_minutes = minutes
        ticket.category_id = category
        ticket.subcategory_id = subcategory
        ticket.status_id = states[status]
        ticket.assignee_id = assignee
        ticket.resolved_by_id = assignee if resolved else None
        ticket.vip = vip
        ticket.resolution_note = note

    put("t1", created=utc(2026, 11, 2, 15), resolved=utc(2026, 11, 3, 1), minutes=120,
        category=enlaces.id, subcategory=fibra.id, assignee=agent.id, note="Se reinició el equipo")
    put("t2", created=utc(2026, 11, 3, 3), category=enlaces.id, status="in_progress", assignee=agent.id)
    put("t3", created=utc(2026, 11, 5, 12), resolved=utc(2026, 11, 6, 12), minutes=600,
        category=telefonia.id, vip=True, note="@SUM(A1)")
    put("t4", created=utc(2026, 11, 4, 12), resolved=utc(2026, 11, 5, 12), minutes=300,
        category=alertas.id, assignee=other_agent.id)
    put("t5", created=utc(2026, 10, 20, 12), resolved=utc(2026, 11, 4, 12), minutes=900, category=telefonia.id)
    put("t6", created=utc(2026, 11, 6, 12), resolved=utc(2026, 11, 6, 18), minutes=360,
        category=enlaces.id, assignee=agent.id)
    db.flush()

    calendar = BusinessCalendar(name="Reporte", timezone="America/Guatemala", windows=[])
    db.add(calendar)
    db.flush()
    policy = SlaPolicy(name="Reporte", calendar_id=calendar.id, resolution_minutes=480)
    db.add(policy)
    db.flush()

    def instance(key: str, status: str, target: int) -> None:
        ticket = db.get(Ticket, t[key])
        db.add(
            SlaInstance(
                ticket_id=ticket.id, policy_id=policy.id, calendar_id=calendar.id, metric="resolution",
                target_minutes=target, started_at=ticket.created_at, status=status,
            )
        )

    # Una instancia cancelada (re-evaluación) no cuenta: la vigente es la última no cancelada.
    instance("t1", "cancelled", 60)
    instance("t1", "met", 480)
    instance("t2", "running", 480)
    instance("t3", "breached", 240)
    instance("t4", "met", 600)
    instance("t6", "met", 480)
    db.commit()
    return {
        **t,
        "categories": {"enlaces": enlaces.id, "telefonia": telefonia.id, "alertas": alertas.id},
        "fibra": fibra.id,
        "gerencias": {"comercial": comercial_g.id, "finanzas": finanzas_g.id},
        "departments": {"comercial": comercial.id, "finanzas": finanzas.id, "telecom": telecom, "soc": soc},
    }


@pytest.fixture
def gerencia_viewer(make_user, report_data):
    persona = make_user("gerente.comercial")
    with SessionLocal() as session:
        session.add(GerenciaViewer(gerencia_id=report_data["gerencias"]["comercial"], user_id=persona.id))
        session.commit()
    return persona


# --------------------------------------------------------------------------- acceso y alcance


def test_unauthenticated(client):
    assert client.get("/reports/builder/meta").status_code == 401
    assert client.post("/reports/builder/run", json=definition()).status_code == 401


def test_meta_lists_whitelist_and_scope(client, admin, agent, requester, gerencia_viewer):
    body = client.get("/reports/builder/meta", headers=admin.headers).json()
    assert {d["key"] for d in body["dimensions"]} >= {"category", "gerencia", "month", "resolved_by"}
    assert next(d for d in body["dimensions"] if d["key"] == "month")["kind"] == "date"
    units = {m["key"]: m["unit"] for m in body["metrics"]}
    assert units["count"] == "count" and units["resolution_hours_avg"] == "hours"
    assert units["sla_compliance_pct"] == "pct"
    assert [f["key"] for f in body["date_fields"]] == ["created_at", "resolved_at"]
    assert body["scope"] == {"level": "all", "label": "Todos los tickets"}

    agent_scope = client.get("/reports/builder/meta", headers=agent.headers).json()["scope"]
    assert agent_scope["level"] == "areas" and "Telecomunicaciones" in agent_scope["label"]
    assert client.get("/reports/builder/meta", headers=requester.headers).json()["scope"]["level"] == "own"
    viewer_scope = client.get("/reports/builder/meta", headers=gerencia_viewer.headers).json()["scope"]
    assert viewer_scope["level"] == "areas" and "Gerencia Comercial" in viewer_scope["label"]


@pytest.mark.usefixtures("report_data")
@pytest.mark.parametrize(
    "persona_name,expected",
    [
        ("admin", 5),  # T1, T2, T3, T4, T6
        ("agent", 4),  # Telecom: T1, T2, T3, T6
        ("other_agent", 1),  # SOC: T4
        ("requester", 3),  # Carlos: T1, T2, T6
        ("other_requester", 2),  # Sofía: T3, T4 (T5 fuera del rango)
        ("approver", 0),
    ],
)
def test_scope_by_role(client, request, persona_name, expected):
    persona = request.getfixturevalue(persona_name)
    section = section_of(run(client, persona, definition()))
    assert section["grand_total"]["count"] == expected
    assert section["ticket_count"] == expected


def test_gerencia_viewer_sees_requested_by_gerencia(client, gerencia_viewer, report_data):
    body = definition(sections=[{"rows": ["requester_department"], "metrics": ["count"]}])
    section = section_of(run(client, gerencia_viewer, body))
    assert section["grand_total"]["count"] == 3
    assert list(rows_by_label(section)) == ["Comercial"]


def test_filters_never_widen_scope(client, other_agent, requester, report_data):
    telecom = report_data["departments"]["telecom"]
    body = definition(filters={"department_ids": [telecom]})
    assert section_of(run(client, other_agent, body))["grand_total"]["count"] == 0
    body = definition(filters={"requester_department_ids": [report_data["departments"]["finanzas"]]})
    assert section_of(run(client, requester, body))["grand_total"]["count"] == 0
    # Un filtro de sección tampoco amplía.
    body = definition(sections=[{"rows": ["type"], "metrics": ["count"], "filters": {"department_ids": [telecom]}}])
    assert section_of(run(client, other_agent, body))["grand_total"]["count"] == 0


def test_filters_reduce(client, admin, report_data):
    cats = report_data["categories"]
    cases = [
        ({"category_ids": [cats["enlaces"]]}, 3),
        ({"subcategory_ids": [report_data["fibra"]]}, 1),
        ({"type_keys": ["service_request"]}, 1),
        ({"gerencia_ids": [report_data["gerencias"]["finanzas"]]}, 2),
        ({"vip": True}, 1),
        ({"vip": False}, 4),
        ({"channels": ["phone"]}, 0),
        ({"priority_keys": ["medium"]}, 5),
        ({"department_ids": [report_data["departments"]["soc"]]}, 1),
    ]
    for filters, expected in cases:
        section = section_of(run(client, admin, definition(filters=filters)))
        assert section["grand_total"]["count"] == expected, filters


# --------------------------------------------------------------------------- lista blanca y límites


@pytest.mark.parametrize(
    "mutation",
    [
        {"sections": [{"rows": ["password"], "metrics": ["count"]}]},
        {"sections": [{"rows": ["category"], "metrics": ["drop_table"]}]},
        {"sections": [{"rows": ["category"], "columns": ["title"], "metrics": ["count"]}]},
        {"sections": [{"rows": ["category"], "metrics": ["count"], "sql": "1=1"}]},
        {"filters": {"department_ids": [1], "requester_id": 3}},
        {"date_field": "updated_at"},
        {"period": "forever"},
        {"unexpected": True},
    ],
)
def test_unknown_keys_rejected(client, agent, mutation):
    assert run(client, agent, definition(**mutation)).status_code == 422


@pytest.mark.parametrize(
    "mutation",
    [
        {"sections": []},
        {"sections": [{"rows": ["type"], "metrics": ["count"]}] * 11},
        {"sections": [{"rows": ["type", "category", "priority"], "metrics": ["count"]}]},
        {"sections": [{"rows": [], "metrics": ["count"]}]},
        {"sections": [{"rows": ["type"], "columns": ["month", "year"], "metrics": ["count"]}]},
        {"sections": [{"rows": ["type"], "metrics": []}]},
        {"sections": [{"rows": ["type"], "metrics": ALL_METRICS + ["open_count"]}]},
        {"sections": [{"rows": ["type", "type"], "metrics": ["count"]}]},
        {"sections": [{"rows": ["type"], "columns": ["type"], "metrics": ["count"]}]},
        {"sections": [{"rows": ["type"], "metrics": ["count", "count"]}]},
        {"date_from": "2025-01-01", "date_to": "2026-01-02"},  # 367 días
        {"date_from": "2026-11-10", "date_to": "2026-11-01"},
        {"date_from": None},
    ],
)
def test_limits(client, agent, mutation):
    assert run(client, agent, definition(**mutation)).status_code == 422


def test_max_range_accepted(client, agent):
    body = definition(date_from="2025-01-01", date_to="2026-01-01")  # 366 días
    assert run(client, agent, body).status_code == 200


# --------------------------------------------------------------------------- cálculos


def test_metrics_and_totals_recomputed_over_tickets(client, admin, report_data):
    body = definition(sections=[
        {"rows": ["category"], "metrics": ALL_METRICS},
        {"rows": ["category"], "metrics": ["count", "open_count"]},
    ])
    response = run(client, admin, body)
    section = section_of(response)
    open_section = response.json()["sections"][1]
    assert rows_by_label(open_section)["Enlaces"]["total"] == {"count": 3, "open_count": 1}
    assert open_section["grand_total"]["open_count"] == 1
    rows = rows_by_label(section)
    assert list(rows) == ["Alertas", "Enlaces", "Telefonía"]
    enlaces = rows["Enlaces"]["total"]
    assert enlaces == {
        "count": 3,
        "resolution_hours_avg": 4.0,  # (2 + 6) / 2; T2 sigue abierto
        "resolution_hours_sum": 8.0,
        "resolution_hours_median": 4.0,
        "resolution_hours_max": 6.0,
        "sla_target_hours": 8.0,
        "sla_compliance_pct": 100.0,
        "sla_breached_count": 0,
    }
    assert rows["Telefonía"]["total"]["sla_compliance_pct"] == 0.0
    assert rows["Telefonía"]["total"]["sla_breached_count"] == 1
    assert rows["Enlaces"]["cells"]["_all"] == enlaces
    assert section["columns"] == [{"key": "_all", "label": "Total"}]

    grand = section["grand_total"]
    assert grand["count"] == 5
    # Promedio sobre tickets [2, 6, 10, 5] (no el promedio de promedios 6.33).
    assert grand["resolution_hours_avg"] == 5.75
    assert grand["resolution_hours_median"] == 5.5
    assert grand["resolution_hours_sum"] == 23.0
    assert grand["resolution_hours_max"] == 10.0
    assert grand["sla_target_hours"] == 7.6  # (480·3 + 240 + 600) / 5 / 60
    assert grand["sla_compliance_pct"] == 75.0  # 3 cumplidas / 4 evaluadas
    assert grand["sla_breached_count"] == 1
    assert section["totals"]["_all"] == grand


def test_two_row_dims_and_none_labels(client, admin, report_data):
    body = definition(sections=[{"rows": ["category", "assignee"], "metrics": ["count"]}])
    rows = rows_by_label(section_of(run(client, admin, body)))
    assert rows["Enlaces / Ana Telecom"]["total"]["count"] == 3
    assert rows["Telefonía / Sin asignar"]["total"]["count"] == 1
    assert rows["Alertas / Luis Soc"]["total"]["count"] == 1


def test_columns_by_month_resolved_at(client, admin, report_data):
    body = definition(
        date_field="resolved_at", date_from="2026-10-01", date_to="2026-11-30",
        sections=[{"rows": ["type"], "columns": ["month"], "metrics": ["count", "resolution_hours_avg"]}],
    )
    section = section_of(run(client, admin, body))
    assert section["column_dim"] == "month"
    assert section["columns"] == [{"key": "2026-10", "label": "Oct 2026"}, {"key": "2026-11", "label": "Nov 2026"}]
    rows = rows_by_label(section)
    incident = rows["Incidencia"]
    # Octubre sin datos → valores nulos.
    assert incident["cells"]["2026-10"] == {"count": None, "resolution_hours_avg": None}
    assert incident["cells"]["2026-11"]["count"] == 4  # T1, T4, T5, T6 resueltos en noviembre
    assert incident["cells"]["2026-11"]["resolution_hours_avg"] == 7.0  # (2 + 5 + 15 + 6) / 4
    assert section["totals"]["2026-11"]["count"] == 5
    assert section["totals"]["2026-10"]["count"] is None
    assert section["grand_total"]["count"] == 5


def test_day_rows_use_business_timezone(client, agent, report_data):
    body = definition(sections=[{"rows": ["day"], "metrics": ["count"]}])
    section = section_of(run(client, agent, body))
    keys = [row["keys"][0]["key"] for row in section["rows"]]
    assert keys == [f"2026-11-0{d}" for d in range(1, 8)]  # serie completa
    rows = {row["keys"][0]["key"]: row for row in section["rows"]}
    assert rows["2026-11-02"]["total"]["count"] == 2  # T1 y T2 (21:00 hora local)
    assert rows["2026-11-03"]["total"]["count"] is None
    assert rows["2026-11-02"]["keys"][0]["label"] == "02/11/2026"


def test_gerencia_vip_channel_dims(client, admin, report_data):
    body = definition(sections=[
        {"rows": ["gerencia", "requester_department"], "columns": ["type"], "metrics": ["count"]},
        {"rows": ["vip"], "metrics": ["count"]},
        {"rows": ["channel"], "columns": ["week"], "metrics": ["count"]},
    ])
    response = run(client, admin, body)
    assert response.status_code == 200
    first, vip, channel = response.json()["sections"]
    rows = rows_by_label(first)
    assert rows["Gerencia Comercial / Comercial"]["total"]["count"] == 3
    assert rows["Gerencia Financiera / Finanzas"]["cells"]
    assert {c["label"] for c in first["columns"]} == {"Incidencia", "Petición"}
    assert rows_by_label(vip)["Sí"]["total"]["count"] == 1
    assert rows_by_label(channel)["Portal"]["total"]["count"] == 5
    assert channel["columns"][0]["label"].startswith("Semana del")


def test_empty_result(client, admin):
    body = definition(sections=[{"rows": ["category"], "metrics": ["count", "resolution_hours_avg"]}])
    section = section_of(run(client, admin, body))
    assert section["rows"] == []
    assert section["grand_total"] == {"count": 0, "resolution_hours_avg": None}


# --------------------------------------------------------------------------- periodos


@pytest.mark.parametrize(
    "period,now,expected",
    [
        ("this_month", utc(2026, 11, 24, 15), (date(2026, 11, 1), date(2026, 11, 24))),
        ("last_month", utc(2026, 11, 24, 15), (date(2026, 10, 1), date(2026, 10, 31))),
        ("last_30_days", utc(2026, 11, 24, 15), (date(2026, 10, 26), date(2026, 11, 24))),
        ("this_year", utc(2026, 11, 24, 15), (date(2026, 1, 1), date(2026, 11, 24))),
        ("last_year", utc(2026, 11, 24, 15), (date(2025, 1, 1), date(2025, 12, 31))),
        # 03:00 UTC del 1 de diciembre = 21:00 del 30 de noviembre en Guatemala.
        ("this_month", utc(2026, 12, 1, 3), (date(2026, 11, 1), date(2026, 11, 30))),
        ("last_month", utc(2026, 1, 15, 12), (date(2025, 12, 1), date(2025, 12, 31))),
    ],
)
def test_resolve_period(period, now, expected):
    model = ReportDefinition.model_validate(definition(period=period, date_from=None, date_to=None))
    assert resolve_period(model, now) == expected


def test_period_ignores_custom_dates(client, admin):
    body = definition(period="last_year", date_from="2026-11-01", date_to="2026-11-07")
    result = run(client, admin, body).json()
    assert result["date_from"].endswith("-01-01") and result["date_to"].endswith("-12-31")
    assert result["scope"]["level"] == "all"


# --------------------------------------------------------------------------- exportación .xlsx


def _workbook(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert ".xlsx" in response.headers["content-disposition"]
    return load_workbook(io.BytesIO(response.content))


def _all_values(workbook) -> list[Any]:
    values = []
    for ws in workbook.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                assert cell.data_type != "f", (ws.title, cell.coordinate, cell.value)
                values.append(cell.value)
    return values


def test_export_xlsx_sections_detail_and_no_formulas(client, admin, report_data):
    body = {
        "definition": definition(
            title="=cmd|' /C calc'!A0",
            sections=[
                {"title": "Por categoría", "rows": ["category"], "columns": ["month"],
                 "metrics": ["count", "resolution_hours_avg", "sla_compliance_pct"]},
                {"title": "Tipo: [detalle]?", "rows": ["type"], "metrics": ["count"], "totals": False},
            ],
        ),
        "include_detail": True,
    }
    workbook = _workbook(client.post("/reports/builder/export.xlsx", json=body, headers=admin.headers))
    assert workbook.sheetnames == ["Por categoría", "Tipo detalle", "Detalle"]

    values = _all_values(workbook)
    texts = [v for v in values if isinstance(v, str)]
    assert not any(t.lstrip().startswith(("=", "+", "-", "@")) for t in texts)
    assert "'=cmd|' /C calc'!A0" in texts

    sheet = workbook["Por categoría"]
    assert sheet["A1"].font.bold
    assert sheet.freeze_panes == "B9"  # títulos de columnas y filas inmovilizados
    assert workbook["Detalle"].freeze_panes == "B2"
    assert workbook["Detalle"].auto_filter.ref == "A1:V6"
    rows = list(sheet.iter_rows(values_only=True))
    header = next(r for r in rows if r and r[0] == "Categoría")
    assert "Cantidad de tickets" in header and "Cumplimiento SLA (%)" in header
    enlaces = next(r for r in rows if r and r[0] == "Enlaces")
    assert enlaces[1] == 3  # noviembre: cantidad
    assert enlaces[2] == 4.0  # promedio de horas hábiles
    assert enlaces[3] == 1.0  # 100 % como fracción
    total = next(r for r in rows if r and r[0] == "Total")
    assert total[1] == 5 and total[2] == 5.75 and total[3] == 0.75
    hours_cell = next(cell for row in sheet.iter_rows() for cell in row if cell.value == 5.75)
    assert hours_cell.number_format == "0.00"

    detail = workbook["Detalle"]
    detail_rows = list(detail.iter_rows(values_only=True))
    assert detail_rows[0][0] == "Número" and "Solución" in detail_rows[0]
    assert len(detail_rows) - 1 == 5
    header = detail_rows[0]
    titles = [r[header.index("Título")] for r in detail_rows[1:]]
    assert "'" + INJECTION_TITLE in titles
    notes = [r[header.index("Solución")] for r in detail_rows[1:]]
    assert "'@SUM(A1)" in notes
    first = detail_rows[1]
    assert first[header.index("Fecha de solicitud")] == datetime(2026, 11, 2, 9, 0)  # hora local
    assert first[header.index("Horas hábiles de resolución")] == 2.0
    assert first[header.index("Cumplió SLA")] == "Sí"
    assert first[header.index("Gerencia del solicitante")] == "Gerencia Comercial"
    assert "Sin datos" not in " ".join(str(v) for v in _all_values(workbook) if v)


def test_export_xlsx_scope_and_columns(client, requester, report_data):
    body = {
        "definition": definition(),
        "include_detail": True,
        "detail_columns": ["number", "title", "team"],
    }
    workbook = _workbook(client.post("/reports/builder/export.xlsx", json=body, headers=requester.headers))
    rows = list(workbook["Detalle"].iter_rows(values_only=True))
    assert rows[0] == ("Número", "Título", "Equipo")
    assert len(rows) - 1 == 3  # solo los de Carlos

    bad = {**body, "detail_columns": ["number", "password_hash"]}
    assert client.post("/reports/builder/export.xlsx", json=bad, headers=requester.headers).status_code == 422


def test_export_xlsx_without_detail(client, agent):
    body = {"definition": definition(), "include_detail": False}
    workbook = _workbook(client.post("/reports/builder/export.xlsx", json=body, headers=agent.headers))
    assert workbook.sheetnames == ["Por categoría"]
    assert any(v and "Sin datos" in str(v) for v in _all_values(workbook))


def test_type_names_seeded():
    with SessionLocal() as session:
        names = dict(session.execute(select(TicketType.key, TicketType.name)).all())
    assert names["incident"] == "Incidencia" and names["service_request"] == "Petición"
