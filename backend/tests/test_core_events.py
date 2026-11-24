"""Bus de eventos: historial, suscriptores, publicación tras commit, enriquecedores y Redis caído."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core import events
from app.core.config import settings
from app.core.events import emit, register_ticket_enricher, subscribe
from app.models.core import Department, Ticket, TicketHistory
from tests.conftest import DEPARTMENT_TELECOM

PAYLOAD_KEYS = {
    "type",
    "ticket_id",
    "ticket_key",
    "actor_id",
    "data",
    "ts",
    "department_id",
    "team_id",
    "requester_id",
    "assignee_id",
}


def test_ticket_created_history_and_payload(db, ids, requester, create_ticket, published_events):
    ticket = create_ticket(requester, priority_key="high")
    assert [p["type"] for p in published_events] == ["ticket.created"]
    payload = published_events[0]
    assert set(payload) == PAYLOAD_KEYS
    assert payload["ticket_id"] == ticket["id"]
    assert payload["ticket_key"] == ticket["key"] == f"ESD-{ticket['id']}"
    assert payload["actor_id"] == requester.id
    assert payload["requester_id"] == requester.id
    assert payload["assignee_id"] is None
    assert payload["department_id"] == ids["departments"][DEPARTMENT_TELECOM]
    assert payload["team_id"] == ids["teams"][DEPARTMENT_TELECOM]
    assert payload["data"]["after"]["priority"] == "high"

    history = db.scalars(select(TicketHistory).where(TicketHistory.ticket_id == ticket["id"])).all()
    assert [(h.event_type, h.actor_id) for h in history] == [("ticket.created", requester.id)]
    assert history[0].before_json is None
    assert history[0].after_json["status"] == "new"


def test_patch_emits_updated_and_assigned(client, agent, requester, create_ticket, published_events):
    ticket = create_ticket(requester)
    published_events.clear()
    response = client.patch(
        f"/tickets/{ticket['id']}",
        json={"assignee_id": agent.id, "priority_key": "critical"},
        headers=agent.headers,
    )
    assert response.status_code == 200, response.text
    assert [p["type"] for p in published_events] == ["ticket.updated", "ticket.assigned"]
    updated, assigned = published_events
    assert updated["data"]["changes"] == {
        "priority": {"from": "medium", "to": "critical"},
        "assignee_id": {"from": None, "to": agent.id},
    }
    assert assigned["assignee_id"] == agent.id
    assert assigned["data"]["to_assignee_id"] == agent.id

    # Un PATCH sin cambios reales no emite eventos.
    published_events.clear()
    client.patch(f"/tickets/{ticket['id']}", json={"priority_key": "critical"}, headers=agent.headers)
    assert published_events == []


def test_transition_and_comment_events(client, agent, requester, create_ticket, transition, published_events):
    ticket = create_ticket(requester)
    published_events.clear()
    transition(agent, ticket["id"], "in_progress", comment="En camino")
    # Otros módulos (p.ej. notificaciones) pueden publicar eventos propios: solo se comparan los ticket.*.
    ticket_events = [p for p in published_events if p["type"].startswith("ticket.")]
    assert [p["type"] for p in ticket_events] == ["ticket.transitioned", "ticket.commented"]
    moved, commented = ticket_events
    assert moved["data"]["from_state"] == "new" and moved["data"]["to_state"] == "in_progress"
    assert moved["data"]["from_category"] == "todo" and moved["data"]["to_category"] == "in_progress"
    assert commented["data"]["visibility"] == "public"
    assert commented["data"]["author_is_agent"] is True
    assert isinstance(commented["data"]["comment_id"], int)


def test_subscribers_run_in_same_transaction(client, requester, create_ticket, ids):
    calls: list[tuple] = []

    @subscribe("ticket.created")
    def with_event(session, event):
        # Mismo session/transacción: el ticket ya es visible sin commit.
        assert session.get(Ticket, event.ticket.id) is event.ticket
        calls.append(("event", event.type, event.ticket.id, event.history_id is not None))

    @subscribe("ticket.created")
    def with_args(session, ticket, actor_id, data):
        calls.append(("args", ticket.id, actor_id, data["after"]["status"]))

    @subscribe("*")
    def wildcard(session, event):
        calls.append(("wildcard", event.type))

    ticket = create_ticket(requester)
    assert calls == [
        ("event", "ticket.created", ticket["id"], True),
        ("args", ticket["id"], requester.id, "new"),
        ("wildcard", "ticket.created"),
    ]


def test_invalid_subscriber_signature_rejected():
    with pytest.raises(TypeError):
        subscribe("ticket.created")(lambda session: None)


def test_publish_only_after_commit(db, requester, create_ticket, published_events):
    ticket_id = create_ticket(requester)["id"]
    published_events.clear()

    ticket = db.get(Ticket, ticket_id)
    emit(db, "custom.rolled_back", ticket, None, {"x": 1})
    assert published_events == []
    db.rollback()
    assert published_events == []

    ticket = db.get(Ticket, ticket_id)
    with db.begin_nested():
        emit(db, "custom.nested", ticket, None, {"x": 2})
    assert published_events == []  # liberar el SAVEPOINT no publica
    db.commit()
    assert [p["type"] for p in published_events] == ["custom.nested"]
    assert db.scalar(
        select(func.count()).select_from(TicketHistory).where(TicketHistory.event_type == "custom.rolled_back")
    ) == 0


def test_failing_subscriber_is_isolated(monkeypatch, db, requester, create_ticket, published_events):
    monkeypatch.setattr(settings, "events_raise_errors", False)

    @subscribe("ticket.created")
    def broken(session, event):
        session.add(Department(name="Creado por handler roto"))
        session.flush()
        emit(session, "custom.from_broken", event.ticket, None, {})
        raise RuntimeError("fallo simulado")

    ticket = create_ticket(requester)
    assert db.get(Ticket, ticket["id"]) is not None
    assert db.scalar(select(Department).where(Department.name == "Creado por handler roto")) is None
    assert [p["type"] for p in published_events] == ["ticket.created"]


def test_failing_subscriber_propagates_in_strict_mode(client, requester, ids):
    @subscribe("ticket.created")
    def broken(session, event):
        raise RuntimeError("fallo estricto")

    with pytest.raises(RuntimeError, match="fallo estricto"):
        client.post(
            "/tickets",
            json={"title": "Prueba estricta", "department_id": ids["departments"][DEPARTMENT_TELECOM]},
            headers=requester.headers,
        )


def test_ticket_enricher_adds_fields(monkeypatch, client, agent, requester, create_ticket):
    ticket = create_ticket(requester)

    @register_ticket_enricher
    def sla_like(session, tickets):
        return {t.id: {"sla": {"state": "on_track", "ticket": t.key}} for t in tickets}

    detail = client.get(f"/tickets/{ticket['id']}", headers=agent.headers).json()
    assert detail["sla"] == {"state": "on_track", "ticket": ticket["key"]}
    listing = client.get("/tickets", headers=agent.headers).json()
    assert listing["items"][0]["sla"]["state"] == "on_track"

    monkeypatch.setattr(settings, "events_raise_errors", False)

    @register_ticket_enricher
    def broken(session, tickets):
        raise RuntimeError("enriquecedor roto")

    detail = client.get(f"/tickets/{ticket['id']}", headers=agent.headers)
    assert detail.status_code == 200
    assert detail.json()["sla"]["state"] == "on_track"


def test_redis_down_is_tolerated(monkeypatch, caplog):
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    monkeypatch.setattr(events, "_redis_client", None)
    monkeypatch.setattr(events, "_redis_down_until", 0.0)
    events.set_publisher(None)
    with caplog.at_level("WARNING", logger="app.core.events"):
        events._redis_publish(settings.events_channel, "{}")  # no lanza
        assert events._redis_down_until > 0
        events.publish_payload({"type": "x"})  # en cola, el hilo lo descarta sin error
        assert events.flush_publications(timeout=5)
    assert any("No se pudo publicar en Redis" in r.message for r in caplog.records)
    monkeypatch.setattr(events, "_redis_client", None)
