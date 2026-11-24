"""Pruebas unitarias del módulo realtime (sin WebSocket real ni Redis)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import pytest

from app.core.security import AuthError, Principal
from app.services.realtime.auth import close_code_for, origin_allowed
from app.services.realtime.connection import (
    CLOSE_SESSION_EXPIRED,
    CLOSE_TRY_AGAIN_LATER,
    ClientConnection,
    truncate_reason,
)
from app.services.realtime.filtering import HIDDEN_EVENT, EventFilter, targeted_recipient
from app.services.realtime.hub import RealtimeHub
from app.services.realtime.log_redaction import TokenRedactingFilter, redact
from app.services.realtime.sources import InMemoryBroker


def principal(user_id: int = 10, roles: set[str] | None = None, teams=(), departments=()) -> Principal:
    return Principal(
        user_id=user_id,
        sub=f"dev:u{user_id}",
        username=f"u{user_id}",
        display_name=f"Usuario {user_id}",
        email=None,
        roles=roles or {"requester"},
        team_ids=set(teams),
        department_ids=set(departments),
    )


def payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "type": "ticket.updated",
        "ticket_id": 5,
        "ticket_key": "ESD-5",
        "actor_id": 1,
        "data": {},
        "ts": "2026-01-01T00:00:00+00:00",
        "department_id": 1,
        "team_id": 11,
        "requester_id": 20,
        "assignee_id": None,
    }
    base.update(overrides)
    return base


def decide(event_filter: EventFilter, event: dict[str, Any]) -> dict[str, Any] | None:
    return asyncio.run(event_filter.decide(event))


# --------------------------------------------------------------------------- filtro


def test_agent_sees_area_events_but_not_others():
    agent = EventFilter(principal(roles={"agent"}, teams=[11], departments=[1]), has_extensions=False)
    assert decide(agent, payload()) is not None
    assert decide(agent, payload(department_id=2, team_id=22)) is None


def test_admin_sees_everything_including_internal_comments():
    admin = EventFilter(principal(roles={"admin"}), has_extensions=False)
    internal = payload(type="ticket.commented", department_id=99, team_id=None,
                       data={"visibility": "internal", "comment_id": 1})
    assert decide(admin, internal) is internal


def test_internal_comment_hidden_from_requester_owner():
    owner = EventFilter(principal(user_id=20), has_extensions=False)
    internal = payload(type="ticket.commented", data={"visibility": "internal"})
    public = payload(type="ticket.commented", data={"visibility": "public"})
    assert decide(owner, internal) is None
    assert decide(owner, public) is public


def test_internal_comment_only_for_agents_working_the_ticket():
    internal = payload(type="ticket.commented", data={"visibility": "internal"}, requester_id=10)
    # Agente de otra área que es el solicitante: ve el ticket pero no la nota interna.
    requester_agent = EventFilter(principal(user_id=10, roles={"agent"}, teams=[33], departments=[3]),
                                  has_extensions=False)
    assert decide(requester_agent, internal) is None
    area_agent = EventFilter(principal(user_id=12, roles={"agent"}, departments=[1]), has_extensions=False)
    assert decide(area_agent, internal) is internal
    assigned = payload(type="ticket.commented", data={"visibility": "internal"}, assignee_id=13)
    assigned_agent = EventFilter(principal(user_id=13, roles={"agent"}, departments=[3]), has_extensions=False)
    assert decide(assigned_agent, assigned) is assigned


def test_personal_notifications_only_for_recipient():
    event = payload(type="notification.created", data={"user_id": 7}, requester_id=None,
                    department_id=None, team_id=None)
    assert targeted_recipient(event) == 7
    assert decide(EventFilter(principal(user_id=7), has_extensions=False), event) is event
    # Ni un admin recibe notificaciones personales de otros.
    assert decide(EventFilter(principal(user_id=8, roles={"admin"}), has_extensions=False), event) is None


def test_hidden_notice_when_scope_changes_away():
    agent = EventFilter(principal(roles={"agent"}, teams=[11], departments=[1]), has_extensions=False)
    moved = payload(
        department_id=2,
        team_id=22,
        data={"before": {"department_id": 1, "team_id": 11, "title": "Viejo"},
              "after": {"department_id": 2, "team_id": 22, "title": "Nuevo secreto"}},
    )
    result = decide(agent, moved)
    assert result is not None and result["type"] == HIDDEN_EVENT
    assert "Nuevo secreto" not in json.dumps(result)
    assert result["department_id"] is None
    # Si nunca lo vio, no recibe nada.
    stranger = EventFilter(principal(roles={"agent"}, teams=[33], departments=[3]), has_extensions=False)
    assert decide(stranger, moved) is None


def test_invalid_payloads_are_ignored():
    event_filter = EventFilter(principal(roles={"admin"}), has_extensions=False)
    assert decide(event_filter, {"ticket_id": 1}) is None
    assert decide(event_filter, "texto") is None  # type: ignore[arg-type]


def test_db_check_is_cached_and_bypassed_for_approval_events():
    calls: list[str] = []
    answers = {"value": False}
    now = {"t": 0.0}

    async def db_check(_principal, event):
        calls.append(event["type"])
        return answers["value"]

    approver = EventFilter(
        principal(user_id=30, roles={"approver"}),
        db_check=db_check,
        has_extensions=True,
        cache_seconds=30,
        negative_cache_seconds=5,
        clock=lambda: now["t"],
    )
    assert decide(approver, payload()) is None
    assert decide(approver, payload()) is None  # caché negativa
    assert calls == ["ticket.updated"]

    # Un evento de aprobación ignora la caché (la visibilidad acaba de cambiar).
    answers["value"] = True
    assert decide(approver, payload(type="approval.requested")) is not None
    assert len(calls) == 2
    # El resultado positivo queda cacheado 30 s.
    assert decide(approver, payload()) is not None
    assert len(calls) == 2
    now["t"] = 31.0
    answers["value"] = False
    assert decide(approver, payload()) is None
    assert len(calls) == 3


def test_db_check_errors_do_not_leak_events():
    async def failing(_principal, _event):
        raise RuntimeError("BD caída")

    event_filter = EventFilter(principal(user_id=30, roles={"approver"}), db_check=failing, has_extensions=True)
    assert decide(event_filter, payload()) is None


def test_principal_without_applicable_extensions_skips_db():
    async def db_check(_principal, _event):  # pragma: no cover - no debe llamarse
        raise AssertionError("no debía consultar la BD")

    event_filter = EventFilter(principal(user_id=40), db_check=db_check, has_extensions=False)
    assert decide(event_filter, payload()) is None


# --------------------------------------------------------------------------- conexión


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed: tuple[int, str | None] | None = None
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def receive(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason)


def make_connection(ws: FakeWebSocket, **kwargs: Any) -> ClientConnection:
    who = principal(roles={"admin"})
    return ClientConnection(ws, who, EventFilter(who, has_extensions=False), **kwargs)


def test_slow_consumer_is_closed_with_try_again_later():
    async def scenario() -> FakeWebSocket:
        ws = FakeWebSocket()
        connection = make_connection(ws, queue_size=2, ping_interval=0)
        for index in range(3):
            event = payload(ticket_id=index)
            connection.offer_event(event, json.dumps(event))
        assert connection.closing
        await asyncio.wait_for(connection.run(), timeout=2)
        return ws

    ws = asyncio.run(scenario())
    assert ws.closed is not None and ws.closed[0] == CLOSE_TRY_AGAIN_LATER
    assert ws.sent == []  # se cierra antes de entregar: el cliente recargará al reconectar


def test_connection_closes_at_expiry_and_delivers_in_order():
    async def scenario() -> FakeWebSocket:
        loop = asyncio.get_running_loop()
        ws = FakeWebSocket()
        connection = make_connection(ws, expires_at=time.time() + 0.2, ping_interval=0)
        for index in range(3):
            event = payload(ticket_id=index)
            connection.offer_event(event, json.dumps(event))
        loop.call_later(0.05, ws.incoming.put_nowait, {"type": "websocket.receive", "text": "ping"})
        await asyncio.wait_for(connection.run(), timeout=3)
        return ws

    ws = asyncio.run(scenario())
    assert [m.get("ticket_id") for m in ws.sent[:3]] == [0, 1, 2]
    assert any(m["type"] == "pong" for m in ws.sent)
    assert ws.closed is not None and ws.closed[0] == CLOSE_SESSION_EXPIRED


def test_client_disconnect_ends_connection_without_close_frame():
    async def scenario() -> FakeWebSocket:
        ws = FakeWebSocket()
        connection = make_connection(ws, ping_interval=0)
        ws.incoming.put_nowait({"type": "websocket.disconnect", "code": 1001})
        await asyncio.wait_for(connection.run(), timeout=2)
        return ws

    assert asyncio.run(scenario()).closed is None


# --------------------------------------------------------------------------- hub


def test_hub_fans_out_once_per_message_and_ignores_garbage():
    async def scenario() -> tuple[list[FakeWebSocket], int, int]:
        broker = InMemoryBroker()
        hub = RealtimeHub(source_factory=lambda: broker.source("c"), backoff_initial=0.01)
        sockets = [FakeWebSocket() for _ in range(3)]
        connections = [make_connection(ws, ping_interval=0) for ws in sockets]
        for connection in connections:
            await hub.register(connection)
        assert await hub.wait_subscribed(1)
        assert broker.subscriber_count("c") == 1
        garbage = hub.dispatch("no es json")
        delivered = hub.dispatch(json.dumps(payload()))
        runners = [asyncio.create_task(c.run()) for c in connections]
        await asyncio.sleep(0.05)
        for ws in sockets:
            ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(asyncio.gather(*runners), timeout=2)
        for connection in connections:
            await hub.unregister(connection)
        assert broker.subscriber_count("c") == 0
        return sockets, garbage, delivered

    sockets, garbage, delivered = asyncio.run(scenario())
    assert garbage == 0
    assert delivered == 3
    assert all(len(ws.sent) == 1 and ws.sent[0]["ticket_id"] == 5 for ws in sockets)


def test_hub_reconnects_with_backoff_and_broadcasts_resync():
    attempts: list[int] = []

    class FlakySource:
        def __init__(self, broker: InMemoryBroker) -> None:
            self.inner = broker.source("c")

        async def stream(self, on_subscribed):
            attempts.append(1)
            if len(attempts) <= 2:
                raise ConnectionError("Redis caído")
            async for message in self.inner.stream(on_subscribed):
                yield message

    async def scenario() -> FakeWebSocket:
        broker = InMemoryBroker()
        hub = RealtimeHub(source_factory=lambda: FlakySource(broker), backoff_initial=0.01, backoff_max=0.02)
        ws = FakeWebSocket()
        connection = make_connection(ws, ping_interval=0)
        await hub.register(connection)
        assert await hub.wait_subscribed(2)
        assert hub.stats()["failures"] == 2
        broker.publish("c", json.dumps(payload()))
        runner = asyncio.create_task(connection.run())
        await asyncio.sleep(0.05)
        ws.incoming.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(runner, timeout=2)
        await hub.unregister(connection)
        return ws

    ws = asyncio.run(scenario())
    assert len(attempts) == 3
    assert [m["type"] for m in ws.sent] == ["resync", "ticket.updated"]


def test_backoff_is_exponential_capped_and_jittered():
    hub = RealtimeHub(backoff_initial=0.5, backoff_max=30, jitter=lambda: 1.0)
    assert [hub._backoff(n) for n in range(4)] == [0.5, 1.0, 2.0, 4.0]
    assert hub._backoff(20) == 30
    low = RealtimeHub(backoff_initial=0.5, backoff_max=30, jitter=lambda: 0.0)
    assert low._backoff(3) == 2.0


def test_hub_shutdown_closes_connections_with_going_away():
    async def scenario() -> FakeWebSocket:
        hub = RealtimeHub(source_factory=lambda: InMemoryBroker().source("c"))
        ws = FakeWebSocket()
        connection = make_connection(ws, ping_interval=0)
        runner = asyncio.create_task(hub.serve(connection))
        await asyncio.sleep(0.02)
        await hub.shutdown()
        await asyncio.wait_for(runner, timeout=2)
        return ws

    ws = asyncio.run(scenario())
    assert ws.closed is not None and ws.closed[0] == 1001


# --------------------------------------------------------------------------- utilidades


@pytest.mark.parametrize(
    ("origin", "allowed", "expected"),
    [
        (None, ["http://localhost:3000"], True),
        ("http://localhost:3000", ["http://localhost:3000"], True),
        ("http://LOCALHOST:3000", ["http://localhost:3000/"], True),
        ("https://sd.energuate.com:443", ["https://sd.energuate.com"], True),
        ("http://localhost:3001", ["http://localhost:3000"], False),
        ("https://localhost:3000", ["http://localhost:3000"], False),
        ("null", ["http://localhost:3000"], False),
        ("https://evil.example", ["*"], True),
    ],
)
def test_origin_allowed(origin, allowed, expected):
    assert origin_allowed(origin, allowed) is expected


def test_close_codes_for_auth_errors():
    assert close_code_for(AuthError("x")) == 4401
    assert close_code_for(AuthError("x", 403)) == 4403
    assert close_code_for(AuthError("x", 503)) == 1013


def test_truncate_reason_respects_utf8_limit():
    reason = truncate_reason("á" * 200)
    assert len(reason.encode("utf-8")) <= 120


def test_tokens_are_redacted_from_uvicorn_logs():
    assert redact("/ws?token=abc.def.ghi&x=1") == "/ws?token=***&x=1"
    assert redact("/ws?dev_user=ana") == "/ws?dev_user=ana"
    record = logging.LogRecord(
        "uvicorn.error", logging.INFO, __file__, 1,
        '%s - "WebSocket %s" [accepted]', ("127.0.0.1:5000", "/ws?token=eyJhbGciOi.secret.sig"), None,
    )
    assert TokenRedactingFilter().filter(record)
    assert "secret" not in record.getMessage()
    assert "token=***" in record.getMessage()
    import app.routers.ws  # noqa: F401  (instala el filtro al cargarse)

    assert any(isinstance(f, TokenRedactingFilter) for f in logging.getLogger("uvicorn.error").filters)


# --------------------------------------------------------------------------- lotes de visibilidad


def test_visibility_checks_are_batched_in_one_thread_job():
    from app.services.realtime.visibility import BatchedVisibilityChecker

    calls: list[list[int]] = []

    def check_many(items):
        calls.append([p.user_id for p, _ in items])
        return [p.user_id % 2 == 0 for p, _ in items]

    async def scenario() -> list[bool]:
        checker = BatchedVisibilityChecker(check_many, max_threads=1)
        return await asyncio.gather(*(checker(principal(user_id=i), payload()) for i in range(1, 6)))

    results = asyncio.run(scenario())
    assert results == [False, True, False, True, False]
    assert calls == [[1, 2, 3, 4, 5]]


def test_visibility_batch_errors_reach_every_caller():
    from app.services.realtime.visibility import BatchedVisibilityChecker

    def check_many(items):
        raise RuntimeError("BD caída")

    async def scenario() -> list[Any]:
        checker = BatchedVisibilityChecker(check_many)
        return await asyncio.gather(
            *(checker(principal(user_id=i), payload()) for i in range(3)), return_exceptions=True
        )

    results = asyncio.run(scenario())
    assert all(isinstance(r, RuntimeError) for r in results)
