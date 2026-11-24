"""Detección periódica de advertencias y vencimientos (tarea Celery ``sla.check_deadlines``)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.core.db import SessionLocal, utcnow
from app.models.core import TicketHistory
from app.services.sla.deadlines import check_deadlines
from tests.test_sla_support import current, sla_seed  # noqa: F401


def _types(events):
    return [(e["type"], e["data"].get("metric")) for e in events if e["type"].startswith("sla.")]


def test_task_is_registered_in_beat_schedule():
    from app.tasks import celery_app
    import app.tasks.sla as sla_tasks

    assert "sla.check_deadlines" in celery_app.tasks
    assert celery_app.conf.beat_schedule["sla-check-deadlines"]["task"] == "sla.check_deadlines"
    assert sla_tasks.beat_schedule["sla-check-deadlines"]["schedule"] == 60.0


def test_warning_and_breach_are_emitted_once(client, sla_seed, requester, create_ticket, published_events):
    ticket = create_ticket(requester, priority_key="critical")  # 24x7: 15 min / 4 h, aviso al 75 %
    tid = ticket["id"]
    start = current(tid, "first_response").started_at
    published_events.clear()

    assert check_deadlines(start + timedelta(minutes=5)) == {"breached": 0, "warned": 0, "errors": 0}

    counts = check_deadlines(start + timedelta(minutes=12))
    assert counts == {"breached": 0, "warned": 1, "errors": 0}
    assert _types(published_events) == [("sla.warning", "first_response")]
    warning = published_events[-1]
    assert warning["ticket_id"] == tid and warning["actor_id"] is None
    assert warning["data"]["warning_threshold_pct"] == 75
    assert 0 < warning["data"]["remaining_business_seconds"] <= 3 * 60 + 1

    published_events.clear()
    counts = check_deadlines(start + timedelta(minutes=16))
    assert counts["breached"] == 1 and counts["warned"] == 0
    assert _types(published_events) == [("sla.breached", "first_response")]
    fr = current(tid, "first_response")
    assert fr.status == "breached" and fr.breached_at == fr.target_at

    # Idempotente: otra ejecución en el mismo instante no emite nada.
    published_events.clear()
    assert check_deadlines(start + timedelta(minutes=16)) == {"breached": 0, "warned": 0, "errors": 0}
    assert published_events == []

    # Resolución: 4 h; si ambos umbrales vencieron a la vez solo se emite el vencimiento.
    counts = check_deadlines(start + timedelta(hours=5))
    assert counts == {"breached": 1, "warned": 0, "errors": 0}
    assert _types(published_events) == [("sla.breached", "resolution")]
    assert current(tid, "resolution").warned_at is not None

    with SessionLocal() as session:
        history = session.scalars(
            select(TicketHistory.event_type).where(TicketHistory.ticket_id == tid).order_by(TicketHistory.id)
        ).all()
    assert history.count("sla.breached") == 2 and history.count("sla.warning") == 1


def test_paused_and_finished_instances_are_not_breached(
    client, sla_seed, requester, agent, create_ticket, transition, published_events
):
    paused_ticket = create_ticket(requester, priority_key="critical")
    transition(agent, paused_ticket["id"], "in_progress")  # cumple primera respuesta
    transition(agent, paused_ticket["id"], "pending")  # pausa la resolución

    resolved_ticket = create_ticket(requester, priority_key="critical")
    transition(agent, resolved_ticket["id"], "in_progress")
    transition(agent, resolved_ticket["id"], "resolved")
    published_events.clear()

    counts = check_deadlines(utcnow() + timedelta(days=2))
    assert counts == {"breached": 0, "warned": 0, "errors": 0}
    assert published_events == []
    assert current(paused_ticket["id"], "resolution").status == "paused"
    assert current(resolved_ticket["id"], "resolution").status == "met"


def test_resumed_instance_gets_new_deadline(client, sla_seed, requester, agent, create_ticket, transition):
    ticket = create_ticket(requester, priority_key="critical")
    tid = ticket["id"]
    transition(agent, tid, "in_progress")
    transition(agent, tid, "pending")
    transition(agent, tid, "in_progress")
    res = current(tid, "resolution")
    assert res.status == "running"
    assert check_deadlines(res.target_at - timedelta(seconds=1))["breached"] == 0
    assert check_deadlines(res.target_at)["breached"] == 1
