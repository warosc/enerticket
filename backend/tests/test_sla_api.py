"""API SLA: permisos, CRUD de calendarios/feriados/políticas, detalle por ticket, resumen y riesgo."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.sla.deadlines import check_deadlines
from tests.conftest import DEPARTMENT_SOC
from tests.test_sla_support import current, shift_instance, sla_seed  # noqa: F401

BUSINESS_WINDOWS = [{"weekday": d, "start": "08:00", "end": "17:00"} for d in range(5)]


# --------------------------------------------------------------------------- permisos


@pytest.mark.parametrize("path", ["/sla/calendars", "/sla/policies", "/sla/at-risk"])
def test_catalog_requires_agent(client, sla_seed, requester, agent, path):
    assert client.get(path, headers=requester.headers).status_code == 403
    assert client.get(path, headers=agent.headers).status_code == 200


def test_mutations_require_admin(client, sla_seed, agent, admin):
    calendar_id = sla_seed["calendars"]["24x7"]
    payload = {"name": "Turno SOC", "windows": BUSINESS_WINDOWS}
    assert client.post("/sla/calendars", json=payload, headers=agent.headers).status_code == 403
    assert client.patch(f"/sla/calendars/{calendar_id}", json={"description": "x"}, headers=agent.headers).status_code == 403
    assert client.post("/sla/recalculate", headers=agent.headers).status_code == 403
    policy = {"name": "P", "calendar_id": calendar_id, "first_response_minutes": 10}
    assert client.post("/sla/policies", json=policy, headers=agent.headers).status_code == 403
    assert client.post("/sla/calendars", json=payload, headers=admin.headers).status_code == 201


def test_unauthenticated_is_rejected(client, sla_seed):
    assert client.get("/sla/policies").status_code == 401


# --------------------------------------------------------------------------- calendarios y feriados


def test_calendar_crud_and_validation(client, sla_seed, admin):
    response = client.post(
        "/sla/calendars",
        json={
            "name": "Turno extendido",
            "description": "L-S",
            "windows": [
                {"weekday": 5, "start": "08:00", "end": "12:00"},
                *[{"weekday": d, "start": "07:00", "end": "19:00"} for d in range(5)],
            ],
        },
        headers=admin.headers,
    )
    assert response.status_code == 201, response.text
    calendar = response.json()
    assert calendar["timezone"] == "America/Guatemala"
    assert calendar["weekly_business_minutes"] == 5 * 12 * 60 + 4 * 60
    assert calendar["windows"][0]["weekday"] == 0  # normalizado y ordenado
    assert calendar["is_24x7"] is False

    duplicate = client.post("/sla/calendars", json={"name": "turno extendido", "windows": BUSINESS_WINDOWS}, headers=admin.headers)
    assert duplicate.status_code == 409

    bad = client.post(
        "/sla/calendars",
        json={"name": "Malo", "windows": [{"weekday": 0, "start": "10:00", "end": "09:00"}]},
        headers=admin.headers,
    )
    assert bad.status_code == 422
    bad_tz = client.post("/sla/calendars", json={"name": "Malo", "timezone": "Mars/Base", "windows": BUSINESS_WINDOWS}, headers=admin.headers)
    assert bad_tz.status_code == 422

    patched = client.patch(
        f"/sla/calendars/{calendar['id']}",
        json={"windows": [{"weekday": d, "start": "00:00", "end": "24:00"} for d in range(7)]},
        headers=admin.headers,
    )
    assert patched.status_code == 200 and patched.json()["is_24x7"] is True

    assert client.delete(f"/sla/calendars/{calendar['id']}", headers=admin.headers).status_code == 204
    in_use = client.delete(f"/sla/calendars/{sla_seed['calendars']['24x7']}", headers=admin.headers)
    assert in_use.status_code == 409


def test_seeded_calendars_and_guatemala_holidays(client, sla_seed, agent):
    calendars = {c["name"]: c for c in client.get("/sla/calendars", headers=agent.headers).json()}
    assert calendars["24x7"]["is_24x7"] is True
    business = calendars["Horario hábil Guatemala"]
    assert business["weekly_business_minutes"] == 45 * 60 and business["holiday_count"] > 0
    assert business["policy_count"] == 3

    year = date.today().year
    holidays = client.get(f"/sla/calendars/{business['id']}/holidays", params={"year": year}, headers=agent.headers)
    assert holidays.status_code == 200
    days = {h["date"] for h in holidays.json()}
    assert f"{year}-09-15" in days and f"{year}-01-01" in days
    assert len(holidays.json()) >= 10  # incluye Semana Santa


def test_holiday_crud_and_import(client, sla_seed, admin):
    calendar = client.post("/sla/calendars", json={"name": "Sede", "windows": BUSINESS_WINDOWS}, headers=admin.headers).json()
    cid = calendar["id"]
    created = client.post(
        f"/sla/calendars/{cid}/holidays", json={"date": "2030-07-01", "name": "Aniversario", "recurring": True},
        headers=admin.headers,
    )
    assert created.status_code == 201
    duplicate = client.post(f"/sla/calendars/{cid}/holidays", json={"date": "2030-07-01", "name": "Otro"}, headers=admin.headers)
    assert duplicate.status_code == 409

    imported = client.post(f"/sla/calendars/{cid}/holidays/import", json={"years": [2030]}, headers=admin.headers)
    assert imported.status_code == 200 and imported.json()["created"] >= 10
    again = client.post(f"/sla/calendars/{cid}/holidays/import", json={"years": [2030]}, headers=admin.headers)
    assert again.json()["created"] == 0 and again.json()["skipped"] >= 10

    # Recurrente: aparece al filtrar cualquier año.
    listed = client.get(f"/sla/calendars/{cid}/holidays", params={"year": 2031}, headers=admin.headers).json()
    assert [h["name"] for h in listed] == ["Aniversario"]

    hid = created.json()["id"]
    patched = client.patch(f"/sla/holidays/{hid}", json={"name": "Aniversario Energuate"}, headers=admin.headers)
    assert patched.status_code == 200 and patched.json()["name"] == "Aniversario Energuate"
    assert client.delete(f"/sla/holidays/{hid}", headers=admin.headers).status_code == 204
    assert client.delete(f"/sla/holidays/{hid}", headers=admin.headers).status_code == 404

    bad_year = client.post(f"/sla/calendars/{cid}/holidays/import", json={"years": [1990]}, headers=admin.headers)
    assert bad_year.status_code == 422


def test_preview_computes_business_target(client, sla_seed, agent):
    cid = sla_seed["calendars"]["Horario hábil Guatemala"]
    # Viernes 27/11/2026 16:30 (UTC-6) + 60 min hábiles → lunes 30/11 08:30.
    response = client.get(
        f"/sla/calendars/{cid}/preview",
        params={"start": "2026-11-27T16:30:00-06:00", "minutes": 60},
        headers=agent.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["target_at"].startswith("2026-11-30T14:30:00")
    assert body["business_seconds"] == 3600


# --------------------------------------------------------------------------- políticas


def test_policy_crud_and_validation(client, sla_seed, admin, ids):
    cid = sla_seed["calendars"]["Horario hábil Guatemala"]
    payload = {
        "name": "Cambios estándar",
        "calendar_id": cid,
        "type_keys": ["change"],
        "department_ids": [ids["departments"][DEPARTMENT_SOC]],
        "precedence": 15,
        "resolution_minutes": 3 * 24 * 60,
        "pause_states": ["pending"],
    }
    created = client.post("/sla/policies", json=payload, headers=admin.headers)
    assert created.status_code == 201, created.text
    policy = created.json()
    assert policy["first_response_minutes"] is None and policy["pause_categories"] == ["waiting"]
    assert policy["calendar_name"] == "Horario hábil Guatemala"

    assert client.post("/sla/policies", json=payload, headers=admin.headers).status_code == 409
    for invalid in (
        {**payload, "name": "X1", "priority_keys": ["urgentísima"]},
        {**payload, "name": "X2", "pause_states": ["closed"]},
        {**payload, "name": "X3", "calendar_id": 9999},
        {"name": "X4", "calendar_id": cid},
        {**payload, "name": "X5", "warning_threshold_pct": 100},
        {**payload, "name": "X6", "pause_categories": ["done"]},
    ):
        assert client.post("/sla/policies", json=invalid, headers=admin.headers).status_code == 422, invalid

    pid = policy["id"]
    updated = client.patch(f"/sla/policies/{pid}", json={"active": False, "warning_threshold_pct": 60}, headers=admin.headers)
    assert updated.status_code == 200
    assert updated.json()["active"] is False and updated.json()["warning_threshold_pct"] == 60
    no_targets = client.patch(f"/sla/policies/{pid}", json={"resolution_minutes": None}, headers=admin.headers)
    assert no_targets.status_code == 422
    assert client.delete(f"/sla/policies/{pid}", headers=admin.headers).status_code == 204


def test_policy_in_use_cannot_be_deleted(client, sla_seed, admin, requester, create_ticket):
    create_ticket(requester, priority_key="critical")
    pid = sla_seed["policies"]["Crítica 24x7"]
    response = client.delete(f"/sla/policies/{pid}", headers=admin.headers)
    assert response.status_code == 409
    listed = {p["name"]: p for p in client.get("/sla/policies", headers=admin.headers).json()}
    assert listed["Crítica 24x7"]["instance_count"] == 2


def test_recalculate_applies_new_targets(client, sla_seed, admin, requester, create_ticket):
    ticket = create_ticket(requester, priority_key="critical")
    pid = sla_seed["policies"]["Crítica 24x7"]
    client.patch(f"/sla/policies/{pid}", json={"resolution_minutes": 120}, headers=admin.headers)
    assert current(ticket["id"], "resolution").target_minutes == 240  # no cambia hasta recalcular

    response = client.post("/sla/recalculate", headers=admin.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["tickets"] == 1 and body["created"] == 1 and body["cancelled"] == 1
    assert current(ticket["id"], "resolution").target_minutes == 120
    assert current(ticket["id"], "first_response").target_minutes == 15


# --------------------------------------------------------------------------- detalle por ticket


def test_ticket_sla_detail_respects_visibility(
    client, sla_seed, requester, other_requester, agent, other_agent, create_ticket
):
    ticket = create_ticket(requester, priority_key="high")
    path = f"/tickets/{ticket['id']}/sla"
    assert client.get(path, headers=other_requester.headers).status_code == 404
    assert client.get(path, headers=other_agent.headers).status_code == 404
    for persona in (requester, agent):
        response = client.get(path, headers=persona.headers)
        assert response.status_code == 200
        body = response.json()
        assert body["policy"]["name"] == "Alta - horario hábil"
        assert {i["metric"] for i in body["instances"]} == {"first_response", "resolution"}
        assert [e["type"] for e in body["events"]] == ["started", "started"]
    assert client.get("/tickets/999999/sla", headers=agent.headers).status_code == 404


# --------------------------------------------------------------------------- reportes


def test_summary_counts_and_visibility(
    client, sla_seed, requester, other_requester, agent, create_ticket, transition
):
    met = create_ticket(requester, priority_key="critical")
    transition(agent, met["id"], "in_progress")  # primera respuesta cumplida
    late = create_ticket(requester, priority_key="critical")
    other = create_ticket(other_requester, priority_key="critical")
    check_deadlines(current(late["id"], "first_response").started_at + timedelta(minutes=20))
    # El ticket "other" también vence la primera respuesta.

    body = client.get("/sla/summary", headers=agent.headers).json()
    fr = body["totals"]["first_response"]
    assert fr["met"] == 1 and fr["breached"] == 2 and fr["total"] == 3
    assert fr["compliance_pct"] == pytest.approx(33.3, abs=0.1)
    assert body["by_policy"][0]["policy_name"] == "Crítica 24x7"
    assert body["by_department"][0]["department_name"] == "Telecomunicaciones"

    own = client.get("/sla/summary", headers=other_requester.headers).json()
    assert own["totals"]["first_response"]["total"] == 1
    assert own["totals"]["first_response"]["breached"] == 1
    assert other["id"]

    empty = client.get("/sla/summary", params={"date_from": "2020-01-01", "date_to": "2020-01-31"}, headers=agent.headers)
    assert empty.json()["totals"]["resolution"]["total"] == 0
    assert empty.json()["totals"]["resolution"]["compliance_pct"] is None
    inverted = client.get("/sla/summary", params={"date_from": "2026-02-01", "date_to": "2026-01-01"}, headers=agent.headers)
    assert inverted.status_code == 422


def test_at_risk_lists_visible_breached_first(
    client, sla_seed, requester, agent, other_agent, create_ticket, transition
):
    breached = create_ticket(requester, priority_key="critical")
    soc = create_ticket(requester, department=DEPARTMENT_SOC, priority_key="critical")
    fresh = create_ticket(requester, priority_key="low")
    for ticket in (breached, soc):
        shift_instance(current(ticket["id"], "first_response").id, -timedelta(minutes=16))
    assert check_deadlines()["breached"] == 2

    body = client.get("/sla/at-risk", headers=agent.headers).json()
    keys = [(item["ticket"]["id"], item["metric"]) for item in body["items"]]
    assert (breached["id"], "first_response") in keys
    assert all(item["ticket"]["id"] != soc["id"] for item in body["items"])  # otra área
    assert all(item["ticket"]["id"] != fresh["id"] for item in body["items"])  # sin riesgo
    assert body["items"][0]["status"] == "breached"
    assert body["items"][0]["remaining_business_seconds"] < 0
    assert body["counts"]["breached"] >= 1
    assert "sla" in body["items"][0]["ticket"]

    soc_view = client.get("/sla/at-risk", headers=other_agent.headers).json()
    assert {item["ticket"]["id"] for item in soc_view["items"]} == {soc["id"]}

    mine = client.get("/sla/at-risk", params={"mine": True}, headers=agent.headers).json()
    assert mine["items"] == []
