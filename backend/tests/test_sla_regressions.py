"""Regresiones del motor SLA detectadas en la revisión de producción.

- Re-evaluar (cambio de prioridad o ``POST /sla/recalculate``) no borra ni vuelve a notificar
  un incumplimiento.
- Una primera respuesta cancelada por cierre sin respuesta no se recrea al re-evaluar.
- Editar feriados vuelve a medir las pausas cerradas con el calendario nuevo.
- La tarea de vencimientos respeta el estado recargado de la instancia.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.db import SessionLocal, utcnow
from app.models.sla import SlaEvent, SlaInstance
from app.services.sla.calendar import business_seconds_between
from app.services.sla.deadlines import check_deadlines, process_deadlines
from app.services.sla.engine import SpecCache, cancel, current_instances, recompute_targets
from tests.test_sla_support import current, instances_for, shift_instance, sla_seed  # noqa: F401

GT = ZoneInfo("America/Guatemala")


def _breach_both(tid: int) -> tuple[int, int]:
    for metric in ("first_response", "resolution"):
        shift_instance(current(tid, metric).id, -timedelta(days=30))
    counts = check_deadlines(session_factory=SessionLocal)
    assert counts["breached"] >= 2
    fr, res = current(tid, "first_response"), current(tid, "resolution")
    assert fr.status == "breached" and res.status == "breached"
    return fr.id, res.id


def test_priority_change_keeps_breached_instances_and_does_not_renotify(
    client, sla_seed, requester, agent, admin, create_ticket, published_events
):
    ticket = create_ticket(requester, priority_key="high")
    tid = ticket["id"]
    fr_id, res_id = _breach_both(tid)
    published_events.clear()

    for priority in ("critical", "low"):
        response = client.patch(f"/tickets/{tid}", json={"priority_key": priority}, headers=agent.headers)
        assert response.status_code == 200, response.text
    assert client.post("/sla/recalculate", headers=admin.headers).status_code == 200

    assert [e for e in published_events if e["type"] == "sla.breached"] == []
    assert current(tid, "first_response").id == fr_id
    assert current(tid, "resolution").id == res_id
    assert all(r.status != "cancelled" for r in instances_for(tid))
    sla = client.get(f"/tickets/{tid}", headers=agent.headers).json()["sla"]
    assert sla["resolution"]["breached"] is True


def test_first_response_closed_without_response_is_not_recreated(
    client, sla_seed, requester, agent, create_ticket, published_events
):
    ticket = create_ticket(requester, priority_key="high")
    tid = ticket["id"]
    with SessionLocal() as session:
        fr = current_instances(session, tid)["first_response"]
        cancel(session, fr, utcnow(), reason="done_without_response")
        session.commit()
    published_events.clear()

    response = client.patch(f"/tickets/{tid}", json={"priority_key": "critical"}, headers=agent.headers)
    assert response.status_code == 200, response.text
    first_response_rows = [r for r in instances_for(tid) if r.metric == "first_response"]
    assert [r.status for r in first_response_rows] == ["cancelled"]
    assert [e for e in published_events if e["type"] == "sla.breached"] == []


def test_holiday_edit_remeasures_closed_pauses(client, sla_seed, requester, admin, create_ticket):
    ticket = create_ticket(requester, priority_key="medium")  # «Media - horario hábil»
    tid = ticket["id"]
    start = datetime(2031, 3, 3, 8, 0, tzinfo=GT)  # lunes
    pause_start = datetime(2031, 3, 4, 8, 0, tzinfo=GT)  # martes completo en pausa
    pause_end = datetime(2031, 3, 4, 17, 0, tzinfo=GT)
    with SessionLocal() as session:
        instance = current_instances(session, tid)["resolution"]
        spec = SpecCache(session).get(instance.calendar_id)
        instance.started_at = start
        instance.paused_business_seconds = business_seconds_between(spec, pause_start, pause_end)
        assert instance.paused_business_seconds == 9 * 3600
        session.add(
            SlaEvent(
                instance_id=instance.id,
                type="resumed",
                at=pause_end,
                data={"pause": [pause_start.isoformat(), pause_end.isoformat()]},
            )
        )
        recompute_targets(spec, instance)
        calendar_id = instance.calendar_id
        instance_id = instance.id
        before_target = instance.target_at
        session.commit()

    created = client.post(
        f"/sla/calendars/{calendar_id}/holidays",
        json={"date": "2031-03-04", "name": "Feriado de prueba"},
        headers=admin.headers,
    )
    assert created.status_code == 201, created.text
    with SessionLocal() as session:
        refreshed = session.get(SlaInstance, instance_id)
        assert refreshed.paused_business_seconds == 0  # el martes ya no es hábil
        # El martes estaba en pausa y ahora es feriado: la meta no cambia (jueves 14:00). Con
        # las 9 h de pausa «viejas» se habría corrido al viernes 14:00.
        assert refreshed.target_at == before_target == datetime(2031, 3, 6, 14, 0, tzinfo=GT)


def test_deadline_task_reloads_instance_state(client, sla_seed, requester, create_ticket, published_events):
    """Una instancia que otra transacción ya marcó/pausó no se vuelve a vencer con datos viejos."""
    ticket = create_ticket(requester, priority_key="critical")
    tid = ticket["id"]
    shift_instance(current(tid, "resolution").id, -timedelta(days=2))
    with SessionLocal() as session:
        stale = session.scalar(
            select(SlaInstance).where(SlaInstance.ticket_id == tid, SlaInstance.metric == "resolution")
        )
        assert stale.status == "running"  # copia en memoria
        with SessionLocal() as other:
            fresh = other.get(SlaInstance, stale.id)
            fresh.paused_at = utcnow()
            fresh.status = "paused"
            other.commit()
        published_events.clear()
        counts = process_deadlines(session)
        session.commit()
    assert current(tid, "resolution").status == "paused"
    breached_resolution = [
        e for e in published_events if e["type"] == "sla.breached" and e["data"]["metric"] == "resolution"
    ]
    assert breached_resolution == []
    assert counts["errors"] == 0
