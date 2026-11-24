"""Ciclo de vida del SLA disparado por eventos de tickets: inicio, primera respuesta, pausas,
resolución, reapertura, cambio de prioridad y enriquecedor de TicketOut."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update

from app.core.db import SessionLocal, utcnow
from app.models.sla import SlaInstance, SlaPolicy
from app.services.sla.calendar import add_business_minutes
from app.services.sla.engine import SpecCache
from tests.conftest import DEPARTMENT_SOC
from tests.test_sla_support import current, instances_for, parse_ts, shift_instance, sla_seed  # noqa: F401


def _sla(client, persona, ticket_id):
    response = client.get(f"/tickets/{ticket_id}", headers=persona.headers)
    assert response.status_code == 200, response.text
    return response.json()["sla"]


def test_ticket_created_starts_instances_with_policy(client, sla_seed, requester, create_ticket):
    ticket = create_ticket(requester, priority_key="critical")
    assert ticket["sla"]["policy"]["name"] == "Crítica 24x7"
    first = ticket["sla"]["first_response"]
    resolution = ticket["sla"]["resolution"]
    assert first["status"] == "running" and resolution["status"] == "running"
    assert first["breached"] is False and first["target_minutes"] == 15
    assert 900 - 10 <= first["remaining_business_seconds"] <= 900
    assert resolution["target_minutes"] == 240

    fr = current(ticket["id"], "first_response")
    assert fr.target_at == fr.started_at + timedelta(minutes=15)
    assert fr.warning_at == fr.started_at + timedelta(minutes=15 * 0.75)


def test_business_calendar_policy_uses_business_time(client, sla_seed, requester, create_ticket):
    ticket = create_ticket(requester, priority_key="medium")
    assert ticket["sla"]["policy"]["name"] == "Media - horario hábil"
    fr = current(ticket["id"], "first_response")
    with SessionLocal() as session:
        spec = SpecCache(session).get(fr.calendar_id)
    assert fr.target_at == add_business_minutes(spec, fr.started_at, 120)
    res = current(ticket["id"], "resolution")
    assert res.target_at == add_business_minutes(spec, res.started_at, 24 * 60)


def test_public_agent_comment_meets_first_response(client, sla_seed, requester, agent, create_ticket):
    ticket = create_ticket(requester, priority_key="high")
    # Comentario del propio solicitante y comentario interno: no cuentan como respuesta.
    client.post(f"/tickets/{ticket['id']}/comments", json={"body": "¿Novedades?", "visibility": "public"}, headers=requester.headers)
    client.post(f"/tickets/{ticket['id']}/comments", json={"body": "Nota", "visibility": "internal"}, headers=agent.headers)
    assert _sla(client, agent, ticket["id"])["first_response"]["status"] == "running"

    response = client.post(
        f"/tickets/{ticket['id']}/comments", json={"body": "Lo estamos revisando", "visibility": "public"},
        headers=agent.headers,
    )
    assert response.status_code == 201, response.text
    sla = _sla(client, agent, ticket["id"])
    assert sla["first_response"]["status"] == "met"
    assert sla["first_response"]["open"] is False
    assert sla["resolution"]["status"] == "running"


def test_pause_resume_resolve_and_reopen(client, sla_seed, requester, agent, create_ticket, transition):
    ticket = create_ticket(requester, priority_key="critical")
    tid = ticket["id"]
    assert transition(agent, tid, "in_progress").status_code == 200
    sla = _sla(client, agent, tid)
    assert sla["first_response"]["status"] == "met"  # primera transición de un agente

    assert transition(agent, tid, "pending").status_code == 200
    sla = _sla(client, agent, tid)["resolution"]
    assert sla["status"] == "paused" and sla["paused"] is True and sla["target_at"] is None
    frozen = sla["remaining_business_seconds"]

    # Simula que la pausa duró 2 h (24x7: 2 h hábiles).
    res = current(tid, "resolution")
    with SessionLocal() as session:
        session.execute(
            update(SlaInstance)
            .where(SlaInstance.id == res.id)
            .values(started_at=res.started_at - timedelta(hours=3), paused_at=res.paused_at - timedelta(hours=2))
        )
        session.commit()

    assert transition(agent, tid, "in_progress").status_code == 200
    res = current(tid, "resolution")
    assert res.status == "running" and res.paused_at is None
    assert 7200 <= res.paused_business_seconds <= 7210
    assert res.target_at == res.started_at + timedelta(minutes=240, seconds=res.paused_business_seconds)
    remaining = _sla(client, agent, tid)["resolution"]["remaining_business_seconds"]
    # 3 h desde el inicio, 2 h en pausa → ~1 h consumida (menos lo ya consumido antes de pausar).
    assert abs(remaining - (frozen - 3600)) <= 15

    assert transition(agent, tid, "resolved").status_code == 200
    res = current(tid, "resolution")
    assert res.status == "met" and res.stopped_at is not None
    assert _sla(client, agent, tid)["resolution"]["open"] is False

    assert transition(agent, tid, "in_progress").status_code == 200  # reabrir
    reopened = current(tid, "resolution")
    assert reopened.id == res.id
    assert reopened.status == "running" and reopened.stopped_at is None
    assert reopened.paused_business_seconds >= res.paused_business_seconds

    detail = client.get(f"/tickets/{tid}/sla", headers=agent.headers).json()
    types = [e["type"] for e in detail["events"] if e["metric"] == "resolution"]
    assert types[:5] == ["started", "paused", "resumed", "met", "reopened"]


def test_resolve_after_target_marks_breach_and_emits_event(
    client, sla_seed, requester, agent, create_ticket, transition, published_events
):
    ticket = create_ticket(requester, priority_key="critical")
    tid = ticket["id"]
    for metric in ("first_response", "resolution"):
        shift_instance(current(tid, metric).id, -timedelta(hours=5))
    published_events.clear()

    assert transition(agent, tid, "in_progress").status_code == 200
    fr = current(tid, "first_response")
    assert fr.status == "breached" and fr.breached_at == fr.target_at
    breached = [e for e in published_events if e["type"] == "sla.breached"]
    assert [e["data"]["metric"] for e in breached] == ["first_response"]
    assert breached[0]["ticket_id"] == tid and breached[0]["data"]["policy_name"] == "Crítica 24x7"

    assert transition(agent, tid, "resolved").status_code == 200
    res = current(tid, "resolution")
    assert res.status == "breached" and res.stopped_at is not None
    sla = _sla(client, agent, tid)
    assert sla["resolution"]["breached"] is True
    assert sla["resolution"]["remaining_business_seconds"] < 0


def test_priority_change_reevaluates_policy(client, sla_seed, requester, agent, create_ticket):
    ticket = create_ticket(requester, priority_key="medium")
    tid = ticket["id"]
    original = current(tid, "resolution")

    response = client.patch(f"/tickets/{tid}", json={"priority_key": "critical"}, headers=agent.headers)
    assert response.status_code == 200, response.text
    assert response.json()["sla"]["policy"]["name"] == "Crítica 24x7"

    rows = instances_for(tid)
    cancelled = [r for r in rows if r.status == "cancelled"]
    assert {r.metric for r in cancelled} == {"first_response", "resolution"}
    new_res = current(tid, "resolution")
    assert new_res.id != original.id
    assert new_res.started_at == original.started_at  # el reloj no se reinicia
    assert new_res.target_minutes == 240

    # Cambiar otro campo (título) no recalcula.
    client.patch(f"/tickets/{tid}", json={"title": "Nuevo título"}, headers=agent.headers)
    assert len(instances_for(tid)) == len(rows)


def test_priority_change_keeps_met_first_response(client, sla_seed, requester, agent, create_ticket, transition):
    ticket = create_ticket(requester, priority_key="low")
    tid = ticket["id"]
    assert transition(agent, tid, "triage").status_code == 200
    met = current(tid, "first_response")
    assert met.status == "met"

    client.patch(f"/tickets/{tid}", json={"priority_key": "high"}, headers=agent.headers)
    assert current(tid, "first_response").id == met.id
    assert current(tid, "resolution").target_minutes == 8 * 60


def test_priority_change_while_paused_keeps_pause(client, sla_seed, requester, agent, create_ticket, transition):
    ticket = create_ticket(requester, priority_key="medium")
    tid = ticket["id"]
    transition(agent, tid, "in_progress")
    transition(agent, tid, "pending")
    paused = current(tid, "resolution")
    assert paused.status == "paused"

    client.patch(f"/tickets/{tid}", json={"priority_key": "critical"}, headers=agent.headers)
    new = current(tid, "resolution")
    assert new.id != paused.id and new.status == "paused"
    assert new.paused_at == paused.paused_at

    transition(agent, tid, "in_progress")
    assert current(tid, "resolution").status == "running"


def test_requester_closing_without_response_cancels_first_response(
    client, sla_seed, requester, create_ticket, transition
):
    ticket = create_ticket(requester, priority_key="high")
    tid = ticket["id"]
    assert transition(requester, tid, "closed").status_code == 200
    rows = {r.metric: r for r in instances_for(tid)}
    assert rows["first_response"].status == "cancelled"
    assert rows["resolution"].status == "met"
    assert _sla(client, requester, tid)["first_response"] is None


def test_no_matching_policy_means_no_sla(client, sla_seed, requester, create_ticket):
    with SessionLocal() as session:
        session.execute(update(SlaPolicy).values(active=False))
        session.commit()
    ticket = create_ticket(requester, priority_key="high")
    assert ticket["sla"] is None
    assert instances_for(ticket["id"]) == []


def test_policy_precedence_and_department_conditions(client, sla_seed, requester, create_ticket, ids):
    with SessionLocal() as session:
        calendar_id = session.scalar(select(SlaPolicy.calendar_id).where(SlaPolicy.name == "Alta - horario hábil"))
        now = utcnow()
        session.add(
            SlaPolicy(
                name="SOC alta",
                description="",
                calendar_id=calendar_id,
                priority_keys=["high"],
                type_keys=[],
                department_ids=[ids["departments"][DEPARTMENT_SOC]],
                precedence=5,
                first_response_minutes=10,
                resolution_minutes=60,
                warning_threshold_pct=50,
                pause_categories=["waiting"],
                pause_states=[],
                active=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    soc = create_ticket(requester, department=DEPARTMENT_SOC, priority_key="high")
    telecom = create_ticket(requester, priority_key="high")
    assert soc["sla"]["policy"]["name"] == "SOC alta"
    assert soc["sla"]["first_response"]["warning_threshold_pct"] == 50
    assert telecom["sla"]["policy"]["name"] == "Alta - horario hábil"


def test_ticket_list_is_enriched(client, sla_seed, requester, create_ticket):
    create_ticket(requester, priority_key="critical")
    create_ticket(requester, priority_key="low")
    response = client.get("/tickets", headers=requester.headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    for item in items:
        assert set(item["sla"]) >= {"policy", "first_response", "resolution"}
        for metric in ("first_response", "resolution"):
            info = item["sla"][metric]
            assert {"status", "target_at", "remaining_business_seconds", "breached"} <= set(info)
            assert parse_ts(info["target_at"]) is not None
