"""Reportes: permisos, KPIs, tendencia, agrupaciones, antigüedad, SLA y exportación CSV."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import delete, select

from app.core.db import utcnow
from app.models.core import Priority, Ticket, WorkflowState
from app.services.reports import register_sla_report_provider
from app.services.reports.csv_export import sanitize_cell
from tests.conftest import DEPARTMENT_SOC

RANGE = {"from": "2026-11-01", "to": "2026-11-07"}


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def put_ticket(
    db,
    ticket_id: int,
    *,
    created: datetime,
    status: str = "new",
    resolved: datetime | None = None,
    closed: datetime | None = None,
    first_response: datetime | None = None,
    assignee_id: int | None = None,
    priority: str | None = None,
) -> None:
    ticket = db.get(Ticket, ticket_id)
    ticket.created_at = created
    ticket.status_id = db.scalar(select(WorkflowState.id).where(WorkflowState.key == status))
    ticket.resolved_at = resolved
    ticket.closed_at = closed
    ticket.first_response_at = first_response
    ticket.assignee_id = assignee_id
    if priority:
        ticket.priority_id = db.scalar(select(Priority.id).where(Priority.key == priority))


@pytest.fixture
def dataset(db, create_ticket, requester, agent, other_agent) -> dict[str, int]:
    """Escenario fijo (zona America/Guatemala, UTC-6) en noviembre de 2026."""
    t1 = create_ticket(requester, title="Caída de enlace")["id"]
    t2 = create_ticket(requester, title="Router sin gestión")["id"]
    t3 = create_ticket(requester, title='=HYPERLINK("http://evil.example","clic")')["id"]
    t4 = create_ticket(other_agent, department=DEPARTMENT_SOC, title="Alerta IDS")["id"]
    t5 = create_ticket(requester, title="Migración de VLAN")["id"]

    # T1: resuelto en 10 h, primera respuesta a las 2 h.
    put_ticket(
        db, t1, created=utc(2026, 11, 2, 15), status="resolved",
        resolved=utc(2026, 11, 3, 1), first_response=utc(2026, 11, 2, 17), assignee_id=agent.id,
    )
    # T2: creado 03:00 UTC del 3 = 21:00 del 2 en Guatemala; abierto, crítico.
    put_ticket(
        db, t2, created=utc(2026, 11, 3, 3), status="in_progress",
        first_response=utc(2026, 11, 3, 7), assignee_id=agent.id, priority="critical",
    )
    # T3: abierto sin asignar.
    put_ticket(db, t3, created=utc(2026, 11, 5, 12), status="new")
    # T4 (SOC): cerrado, resuelto en 24 h.
    put_ticket(
        db, t4, created=utc(2026, 11, 4, 12), status="closed",
        resolved=utc(2026, 11, 5, 12), closed=utc(2026, 11, 5, 12), assignee_id=other_agent.id,
    )
    # T5: creado en el periodo anterior, resuelto en el actual (168 h).
    put_ticket(db, t5, created=utc(2026, 10, 28, 12), status="resolved", resolved=utc(2026, 11, 4, 12))
    db.commit()
    return {"t1": t1, "t2": t2, "t3": t3, "t4": t4, "t5": t5}


# --------------------------------------------------------------------------- permisos


@pytest.mark.parametrize(
    "path",
    ["overview", "trend", "by-department", "by-assignee", "aging", "sla", "export.csv"],
)
def test_any_authenticated_user_can_read(client, requester, approver, path):
    # Fase 3: /reports/* abierto a cualquier usuario autenticado; el alcance lo da la visibilidad.
    for persona in (requester, approver):
        response = client.get(f"/reports/{path}", params=RANGE, headers=persona.headers)
        assert response.status_code == 200, (path, persona.username)


def test_requester_only_sees_own_tickets(client, requester, approver, other_requester, dataset):
    body = client.get("/reports/overview", params=RANGE, headers=requester.headers).json()
    assert body["created"] == 3  # T1, T2, T3 (todos de Carlos en el periodo)
    assert body["resolved"] == 2  # T1, T5
    assert body["backlog"] == 2
    for persona in (approver, other_requester):
        body = client.get("/reports/overview", params=RANGE, headers=persona.headers).json()
        assert (body["created"], body["resolved"], body["backlog"]) == (0, 0, 0)
    rows = _read_csv(client.get("/reports/export.csv", params=RANGE, headers=requester.headers))
    assert len(rows) - 1 == 3
    rows = _read_csv(client.get("/reports/export.csv", params=RANGE, headers=other_requester.headers))
    assert len(rows) - 1 == 0


def test_unauthenticated(client):
    assert client.get("/reports/overview").status_code == 401


def test_invalid_ranges(client, agent):
    response = client.get("/reports/overview", params={"from": "2026-11-10", "to": "2026-11-01"}, headers=agent.headers)
    assert response.status_code == 422
    response = client.get("/reports/overview", params={"from": "2020-01-01", "to": "2026-11-01"}, headers=agent.headers)
    assert response.status_code == 422
    response = client.get("/reports/overview", params={"from": "no-es-fecha"}, headers=agent.headers)
    assert response.status_code == 422


def test_default_range_is_last_30_days(client, agent):
    body = client.get("/reports/overview", headers=agent.headers).json()
    start = datetime.fromisoformat(body["filters"]["from"]).date()
    end = datetime.fromisoformat(body["filters"]["to"]).date()
    assert (end - start).days == 29
    assert body["filters"]["timezone"] == "America/Guatemala"


# --------------------------------------------------------------------------- overview


def test_overview_agent_only_sees_area(client, agent, dataset):
    body = client.get("/reports/overview", params=RANGE, headers=agent.headers).json()
    assert body["created"] == 3  # T1, T2, T3 (T4 es de SOC, T5 del periodo anterior)
    assert body["open"] == 2  # T2, T3
    assert body["resolved"] == 2  # T1, T5
    assert body["closed"] == 0
    assert body["mttr_hours"] == pytest.approx(89.0)  # (10 + 168) / 2
    assert body["mean_first_response_hours"] == pytest.approx(3.0)  # (2 + 4) / 2
    assert body["first_response_rate_pct"] == pytest.approx(66.7)
    assert body["backlog"] == 2
    assert body["backlog_unassigned"] == 1
    assert body["previous"] == {
        "from": "2026-10-25",
        "to": "2026-10-31",
        "created": 1,
        "resolved": 0,
        "closed": 0,
        "mttr_hours": None,
        "mean_first_response_hours": None,
    }


def test_overview_admin_sees_all_and_filters(client, admin, dataset, ids):
    body = client.get("/reports/overview", params=RANGE, headers=admin.headers).json()
    assert body["created"] == 4
    assert body["resolved"] == 3
    assert body["closed"] == 1
    assert body["mttr_hours"] == pytest.approx((10 + 168 + 24) / 3, abs=0.01)

    soc = ids["departments"][DEPARTMENT_SOC]
    body = client.get(
        "/reports/overview", params={**RANGE, "department_id": soc}, headers=admin.headers
    ).json()
    assert (body["created"], body["resolved"], body["backlog"]) == (1, 1, 0)

    team = ids["teams"][DEPARTMENT_SOC]
    body = client.get("/reports/overview", params={**RANGE, "team_id": team}, headers=admin.headers).json()
    assert body["created"] == 1


def test_other_agent_cannot_widen_scope_with_filters(client, other_agent, dataset, ids):
    telecom = ids["departments"]["Telecomunicaciones"]
    body = client.get(
        "/reports/overview", params={**RANGE, "department_id": telecom}, headers=other_agent.headers
    ).json()
    assert body["created"] == 0 and body["backlog"] == 0


# --------------------------------------------------------------------------- tendencia


def test_trend_daily_uses_business_timezone(client, agent, dataset):
    body = client.get("/reports/trend", params={**RANGE, "granularity": "day"}, headers=agent.headers).json()
    assert body["granularity"] == "day"
    points = {p["period"]: p for p in body["points"]}
    assert list(points) == [f"2026-11-0{d}" for d in range(1, 8)]
    assert points["2026-11-02"]["created"] == 2  # T1 y T2 (21:00 hora local)
    assert points["2026-11-03"]["created"] == 0
    assert points["2026-11-02"]["resolved"] == 1  # T1: 01:00 UTC del 3 = 19:00 del 2
    assert points["2026-11-04"]["resolved"] == 1  # T5
    assert points["2026-11-05"]["created"] == 1
    assert body["totals"] == {"created": 3, "resolved": 2}
    assert body["points"][-1]["net"] == 1


def test_trend_weekly(client, agent, dataset):
    body = client.get("/reports/trend", params={**RANGE, "granularity": "week"}, headers=agent.headers).json()
    assert [p["period"] for p in body["points"]] == ["2026-10-26", "2026-11-02"]
    assert body["points"][1]["created"] == 3
    auto = client.get(
        "/reports/trend", params={"from": "2026-01-01", "to": "2026-06-30"}, headers=agent.headers
    ).json()
    assert auto["granularity"] == "week"


# --------------------------------------------------------------------------- agrupaciones


def test_by_department(client, admin, dataset):
    items = client.get("/reports/by-department", params=RANGE, headers=admin.headers).json()["items"]
    by_name = {item["department"]: item for item in items}
    assert by_name["Telecomunicaciones"]["created"] == 3
    assert by_name["Telecomunicaciones"]["resolved"] == 2
    assert by_name["Telecomunicaciones"]["backlog"] == 2
    assert by_name[DEPARTMENT_SOC]["created"] == 1
    assert by_name[DEPARTMENT_SOC]["mttr_hours"] == pytest.approx(24.0)
    assert items[0]["department"] == "Telecomunicaciones"


def test_by_assignee(client, agent, dataset):
    body = client.get("/reports/by-assignee", params=RANGE, headers=agent.headers).json()
    rows = {item["assignee_id"]: item for item in body["items"]}
    assert rows[agent.id]["open"] == 1
    assert rows[agent.id]["open_critical"] == 1
    assert rows[agent.id]["resolved"] == 1
    assert rows[agent.id]["mttr_hours"] == pytest.approx(10.0)
    assert rows[None]["assignee"] == "Sin asignar"
    assert rows[None]["open"] == 1 and rows[None]["resolved"] == 1
    assert body["items"][-1]["assignee_id"] is None
    assert body["truncated"] is False


# --------------------------------------------------------------------------- antigüedad


def test_aging_buckets(client, agent, db, create_ticket, requester):
    now = utcnow()
    ages = {"new": timedelta(hours=2), "mid": timedelta(days=2), "old": timedelta(days=40)}
    ids_ = {name: create_ticket(requester)["id"] for name in ages}
    for name, age in ages.items():
        put_ticket(db, ids_[name], created=now - age, status="new", priority="high" if name == "old" else None)
    done = create_ticket(requester)["id"]
    put_ticket(db, done, created=now - timedelta(days=5), status="closed", resolved=now, closed=now)
    db.commit()

    body = client.get("/reports/aging", headers=agent.headers).json()
    buckets = {item["bucket"]: item for item in body["items"]}
    assert [item["bucket"] for item in body["items"]] == ["lt_1d", "1_3d", "3_7d", "7_14d", "14_30d", "gt_30d"]
    assert buckets["lt_1d"]["total"] == 1
    assert buckets["1_3d"]["total"] == 1
    assert buckets["3_7d"]["total"] == 0  # el cerrado no cuenta
    assert buckets["gt_30d"]["total"] == 1
    assert buckets["gt_30d"]["by_priority"]["high"] == 1
    assert body["total"] == 3
    assert body["oldest_age_hours"] == pytest.approx(960, abs=1)
    assert [p["key"] for p in body["priorities"]] == ["low", "medium", "high", "critical"]


# --------------------------------------------------------------------------- SLA


def test_sla_compliance_from_instances(client, admin, agent, dataset, db):
    sla_models = pytest.importorskip("app.models.sla")
    calendar = sla_models.BusinessCalendar(name="Reporte", timezone="America/Guatemala", windows=[])
    db.add(calendar)
    db.flush()
    policy = sla_models.SlaPolicy(name="Reporte", calendar_id=calendar.id, first_response_minutes=60, resolution_minutes=480)
    db.add(policy)
    db.flush()
    db.execute(delete(sla_models.SlaInstance))

    def instance(ticket_id: int, metric: str, status: str, started: datetime, **extra: Any) -> None:
        db.add(
            sla_models.SlaInstance(
                ticket_id=ticket_id, policy_id=policy.id, calendar_id=calendar.id, metric=metric,
                target_minutes=60, started_at=started, status=status, **extra,
            )
        )

    instance(dataset["t1"], "first_response", "met", utc(2026, 11, 2, 15))
    instance(dataset["t1"], "resolution", "met", utc(2026, 11, 2, 15))
    instance(dataset["t2"], "first_response", "breached", utc(2026, 11, 3, 3))
    instance(dataset["t2"], "resolution", "running", utc(2026, 11, 3, 3), warning_at=utc(2026, 11, 3, 4))
    instance(dataset["t4"], "resolution", "breached", utc(2026, 11, 4, 12))
    instance(dataset["t5"], "resolution", "met", utc(2026, 10, 28, 12))  # fuera del rango
    db.commit()

    body = client.get("/reports/sla", params=RANGE, headers=agent.headers).json()
    assert body["available"] is True
    metrics = {m["metric"]: m for m in body["metrics"]}
    assert metrics["first_response"]["met"] == 1
    assert metrics["first_response"]["breached"] == 1
    assert metrics["first_response"]["compliance_pct"] == 50.0
    assert metrics["resolution"]["total"] == 2  # T4 es de SOC: no visible para el agente
    assert metrics["resolution"]["compliance_pct"] == 100.0
    assert metrics["resolution"]["at_risk"] == 1
    critical = next(p for p in body["by_priority"] if p["priority"] == "critical")
    assert critical["metrics"]["first_response"]["breached"] == 1

    admin_body = client.get("/reports/sla", params=RANGE, headers=admin.headers).json()
    admin_metrics = {m["metric"]: m for m in admin_body["metrics"]}
    assert admin_metrics["resolution"]["compliance_pct"] == 50.0


def test_sla_provider_override(client, agent):
    calls: list[Any] = []

    def provider(session, filters):
        calls.append(filters.principal.user_id)
        return {"filters": filters.as_dict(), "available": True, "reason": None, "metrics": [], "by_priority": []}

    register_sla_report_provider(provider)
    try:
        body = client.get("/reports/sla", params=RANGE, headers=agent.headers).json()
    finally:
        register_sla_report_provider(None)
    assert body["available"] is True and calls == [agent.id]


# --------------------------------------------------------------------------- CSV


def _read_csv(response) -> list[list[str]]:
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:])))


def test_export_csv_visible_rows_and_formula_injection(client, agent, dataset):
    response = client.get("/reports/export.csv", params=RANGE, headers=agent.headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="tickets_20261101_20261107.csv"' in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"

    rows = _read_csv(response)
    header, data = rows[0], rows[1:]
    assert header[0] == "Clave" and "Horas a resolución" in header
    assert len(data) == 3  # T1, T2, T3: solo tickets visibles creados en el periodo
    titles = [row[1] for row in data]
    assert "'=HYPERLINK(\"http://evil.example\",\"clic\")" in titles
    assert not any(t.startswith("=") for t in titles)
    first = data[0]
    assert first[0] == f"ESD-{dataset['t1']}"
    created_col = header.index("Creado")
    assert first[created_col] == "2026-11-02 09:00"  # hora de Guatemala
    assert float(first[header.index("Horas a resolución")]) == pytest.approx(10.0)


def test_export_csv_admin_sees_all(client, admin, dataset):
    rows = _read_csv(client.get("/reports/export.csv", params=RANGE, headers=admin.headers))
    assert len(rows) - 1 == 4


@pytest.mark.parametrize(
    "value,expected",
    [
        ("=1+1", "'=1+1"),
        ("+57 5555", "'+57 5555"),
        ("-cmd", "'-cmd"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\t=1", "'\t=1"),
        ("  =1", "'  =1"),
        ("＝1", "'＝1"),
        ("normal", "normal"),
        ("", ""),
        (None, ""),
        (-5, -5),
        (3.5, 3.5),
        (True, "Sí"),
    ],
)
def test_sanitize_cell(value, expected):
    assert sanitize_cell(value) == expected
