"""WebSocket /ws: autenticación, Origin, filtrado por permisos, comentarios internos,
fan-out, avisos de pérdida de acceso, ping/pong, expiración y resincronización.

Los eventos viajan por el camino real: API → emit → commit → publicador → broker en
memoria (en lugar de Redis) → hub → filtro por conexión → WebSocket.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import anyio
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.websockets import WebSocketDisconnect

from app.core import events, permissions, security
from app.core.config import settings
from app.core.permissions import register_visibility_extension
from app.models.core import Ticket
from app.services.realtime.config import realtime_settings
from app.services.realtime.hub import RealtimeHub, get_hub, set_hub
from app.services.realtime.sources import InMemoryBroker
from tests.conftest import DEPARTMENT_SOC, DEPARTMENT_TELECOM

ISSUER = "http://localhost:8081/realms/energuate"
AUDIENCE = "servicedesk-api"


# --------------------------------------------------------------------------- utilidades


@pytest.fixture
def broker(client, published_events):
    """Hub con broker en memoria (en lugar de Redis) conectado al bus de eventos.

    - Los eventos de cada petición se publican en el broker cuando la respuesta HTTP ya
      llegó, como ocurre en producción (publicación en segundo plano tras el commit). Así
      las consultas de visibilidad del hub no compiten con la petición por la única
      conexión SQLite en memoria de las pruebas.
    - Se aíslan las extensiones de visibilidad de otros módulos: cada prueba registra las
      que necesita (``isolate_registries`` las restaura al terminar).
    """
    memory = InMemoryBroker()
    hub = RealtimeHub(
        source_factory=lambda: memory.source(settings.events_channel),
        backoff_initial=0.01,
        backoff_max=0.05,
    )
    set_hub(hub)
    permissions._visibility_extensions.clear()
    pending: list[tuple[str, str]] = []

    def publisher(channel: str, message: str) -> None:
        published_events.append(json.loads(message))
        pending.append((channel, message))

    def flush(_response) -> None:
        while pending:
            memory.publish(*pending.pop(0))

    events.set_publisher(publisher)
    client.event_hooks["response"].append(flush)
    yield memory
    set_hub(None)


def ws_url(persona=None, **params: str) -> str:
    query: dict[str, str] = {}
    if persona is not None:
        query["dev_user"] = persona.username
        query["dev_roles"] = ",".join(persona.roles)
    query.update(params)
    return "/ws?" + urlencode(query) if query else "/ws"


async def _receive(ws, timeout: float) -> dict[str, Any]:
    with anyio.fail_after(timeout):
        return await ws._send_rx.receive()


def recv(ws, timeout: float = 5.0) -> dict[str, Any]:
    """Siguiente mensaje JSON (falla si no llega en ``timeout``; lanza si se cerró)."""
    message = ws.portal.call(_receive, ws, timeout)
    ws._raise_on_close(message)
    return json.loads(message["text"])


# Otros módulos (flows, sla) emiten sus propios eventos al reaccionar a los de tickets;
# estas pruebas se centran en los eventos de tickets y de control del canal.
RELEVANT_PREFIXES = ("ticket.", "resync")


def recv_event(ws, timeout: float = 5.0) -> dict[str, Any]:
    """Siguiente evento de ticket o ``resync`` (omite ping/pong y eventos de otros módulos)."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        message = recv(ws, remaining)
        if str(message["type"]).startswith(RELEVANT_PREFIXES):
            return message


def assert_silent(ws, timeout: float = 0.4) -> None:
    try:
        unexpected = recv_event(ws, timeout)
    except TimeoutError:
        return
    pytest.fail(f"Mensaje inesperado: {unexpected}")


def expect_ready(ws) -> dict[str, Any]:
    ready = recv(ws)
    assert ready["type"] == "ready", ready
    assert ready["subscribed"] is True
    return ready


def recv_close(ws, timeout: float = 5.0) -> WebSocketDisconnect:
    with pytest.raises(WebSocketDisconnect) as info:
        while True:
            recv(ws, timeout)
    return info.value


# --------------------------------------------------------------------------- autenticación


def test_rejects_connection_without_identity(client, broker):
    with client.websocket_connect("/ws") as ws:
        closed = recv_close(ws)
    assert closed.code == 4401
    assert "X-Dev-User" in closed.reason


def test_dev_params_rejected_in_oidc_mode(client, broker, monkeypatch, agent):
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    with client.websocket_connect(ws_url(agent)) as ws:
        closed = recv_close(ws)
    assert closed.code == 4401


def test_invalid_token_rejected(client, broker, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    with client.websocket_connect("/ws?token=no-es-un-jwt") as ws:
        closed = recv_close(ws)
    assert closed.code == 4401
    assert closed.reason == "Token inválido"


def test_foreign_origin_rejected_before_handshake(client, broker, agent):
    with pytest.raises(WebSocketDisconnect) as info:
        with client.websocket_connect(ws_url(agent), headers={"origin": "https://evil.example"}):
            pass
    assert info.value.code == 1008


def test_configured_origin_accepted(client, broker, agent, monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", "http://localhost:3000,https://servicedesk.energuate.com")
    with client.websocket_connect(
        ws_url(agent), headers={"origin": "https://SERVICEDESK.energuate.com:443"}
    ) as ws:
        expect_ready(ws)


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oidc(monkeypatch, rsa_key):
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_audience", AUDIENCE)

    class FakeJwksClient:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=rsa_key.public_key())

    monkeypatch.setattr(security, "get_jwks_client", lambda: FakeJwksClient())


def make_token(rsa_key, lifetime: int) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": [AUDIENCE],
        "sub": "7d3c2b1a-0000-1111-2222-333344445555",
        "iat": now,
        "exp": now + lifetime,
        "preferred_username": "ws.agente",
        "name": "Agente WS",
        "realm_access": {"roles": ["agent"]},
    }
    return jwt.encode(claims, rsa_key, algorithm="RS256", headers={"kid": "test"})


def test_valid_token_connects_and_closes_when_token_expires(client, broker, oidc, rsa_key):
    token = make_token(rsa_key, lifetime=2)
    started = time.time()
    with client.websocket_connect("/ws?" + urlencode({"token": token})) as ws:
        ready = expect_ready(ws)
        assert ready["expires_at"] == pytest.approx(started + 2, abs=1.5)
        closed = recv_close(ws, timeout=6)
    assert closed.code == 4000
    assert time.time() - started >= 0.5


def test_wrong_signature_rejected(client, broker, oidc):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with client.websocket_connect("/ws?" + urlencode({"token": make_token(other_key, 300)})) as ws:
        closed = recv_close(ws)
    assert closed.code == 4401


# --------------------------------------------------------------------------- filtrado


def test_events_are_filtered_by_area(client, broker, agent, other_agent, requester, create_ticket):
    with client.websocket_connect(ws_url(agent)) as telecom_ws, client.websocket_connect(
        ws_url(other_agent)
    ) as soc_ws:
        expect_ready(telecom_ws)
        expect_ready(soc_ws)

        telecom_ticket = create_ticket(requester, department=DEPARTMENT_TELECOM, title="Enlace caído")
        received = recv_event(telecom_ws)
        assert received["type"] == "ticket.created"
        assert received["ticket_id"] == telecom_ticket["id"]
        assert received["ticket_key"] == telecom_ticket["key"]
        assert set(received) >= {
            "actor_id", "data", "ts", "department_id", "team_id", "requester_id", "assignee_id",
        }

        soc_ticket = create_ticket(requester, department=DEPARTMENT_SOC, title="Alerta de malware")
        # El primer evento que recibe SOC es el suyo: el de Telecom fue filtrado.
        first_soc = recv_event(soc_ws)
        assert first_soc["ticket_id"] == soc_ticket["id"]
        assert_silent(telecom_ws)


def test_requester_only_receives_own_tickets(
    client, broker, requester, other_requester, create_ticket, agent
):
    own = create_ticket(requester)
    foreign = create_ticket(other_requester)
    with client.websocket_connect(ws_url(requester)) as ws:
        expect_ready(ws)
        client.post(
            f"/tickets/{foreign['id']}/comments",
            json={"body": "Revisando", "visibility": "public"},
            headers=agent.headers,
        )
        client.post(
            f"/tickets/{own['id']}/comments",
            json={"body": "Revisando el suyo", "visibility": "public"},
            headers=agent.headers,
        )
        received = recv_event(ws)
        assert received["ticket_id"] == own["id"]
        assert received["type"] == "ticket.commented"


def test_internal_comments_hidden_from_non_agents(client, broker, agent, requester, create_ticket):
    ticket = create_ticket(requester)
    with client.websocket_connect(ws_url(requester)) as requester_ws, client.websocket_connect(
        ws_url(agent)
    ) as agent_ws:
        expect_ready(requester_ws)
        expect_ready(agent_ws)
        internal = client.post(
            f"/tickets/{ticket['id']}/comments",
            json={"body": "Nota interna: proveedor con falla", "visibility": "internal"},
            headers=agent.headers,
        )
        assert internal.status_code == 201, internal.text
        public = client.post(
            f"/tickets/{ticket['id']}/comments",
            json={"body": "Estamos trabajando en su caso", "visibility": "public"},
            headers=agent.headers,
        )
        assert public.status_code == 201, public.text

        agent_first = recv_event(agent_ws)
        assert agent_first["data"]["visibility"] == "internal"
        assert recv_event(agent_ws)["data"]["visibility"] == "public"

        requester_first = recv_event(requester_ws)
        assert requester_first["type"] == "ticket.commented"
        assert requester_first["data"]["visibility"] == "public"
        assert_silent(requester_ws)


def test_fan_out_from_single_listener(client, broker, agent, admin, requester, create_ticket):
    with client.websocket_connect(ws_url(agent)) as tab1, client.websocket_connect(
        ws_url(agent)
    ) as tab2, client.websocket_connect(ws_url(admin)) as admin_ws:
        for ws in (tab1, tab2, admin_ws):
            expect_ready(ws)
        assert broker.subscriber_count(settings.events_channel) == 1
        assert get_hub().stats()["connections"] == 3

        ticket = create_ticket(requester)
        for ws in (tab1, tab2, admin_ws):
            event = recv_event(ws)
            assert event["type"] == "ticket.created"
            assert event["ticket_id"] == ticket["id"]
    # Sin conexiones, el listener se detiene.
    deadline = time.time() + 2
    while broker.subscriber_count(settings.events_channel) and time.time() < deadline:
        time.sleep(0.02)
    assert broker.subscriber_count(settings.events_channel) == 0


def test_visibility_extensions_are_checked_with_database(
    client, broker, approver, make_user, agent, requester, create_ticket
):
    ticket = create_ticket(requester)
    other_approver = make_user("pedro.aprobador", roles=("approver",))

    @register_visibility_extension
    def approver_sees_ticket(principal):
        if principal.username != approver.username:
            return None
        return Ticket.id.in_([ticket["id"]])

    with client.websocket_connect(ws_url(approver)) as approver_ws, client.websocket_connect(
        ws_url(other_approver)
    ) as other_ws:
        expect_ready(approver_ws)
        expect_ready(other_ws)
        response = client.post(
            f"/tickets/{ticket['id']}/comments",
            json={"body": "Se requiere su aprobación", "visibility": "public"},
            headers=agent.headers,
        )
        assert response.status_code == 201
        received = recv_event(approver_ws)
        assert received["ticket_id"] == ticket["id"]
        assert_silent(other_ws)


def test_scope_change_sends_redacted_hidden_notice(
    client, broker, agent, other_agent, admin, requester, create_ticket, ids
):
    ticket = create_ticket(requester, department=DEPARTMENT_TELECOM)
    with client.websocket_connect(ws_url(agent)) as telecom_ws, client.websocket_connect(
        ws_url(other_agent)
    ) as soc_ws:
        expect_ready(telecom_ws)
        expect_ready(soc_ws)
        response = client.patch(
            f"/tickets/{ticket['id']}",
            json={"department_id": ids["departments"][DEPARTMENT_SOC], "title": "Título confidencial"},
            headers=admin.headers,
        )
        assert response.status_code == 200, response.text

        hidden = recv_event(telecom_ws)
        assert hidden["type"] == "ticket.hidden"
        assert hidden["ticket_id"] == ticket["id"]
        assert hidden["department_id"] is None
        assert "Título confidencial" not in json.dumps(hidden)

        updated = recv_event(soc_ws)
        assert updated["type"] == "ticket.updated"
        assert updated["department_id"] == ids["departments"][DEPARTMENT_SOC]


# --------------------------------------------------------------------------- control


def test_ping_pong_and_server_ping(client, broker, agent, monkeypatch):
    monkeypatch.setattr(realtime_settings, "ping_interval", 0.2)
    with client.websocket_connect(ws_url(agent)) as ws:
        ready = expect_ready(ws)
        assert ready["ping_interval"] == 0.2
        assert recv(ws)["type"] == "ping"
        ws.send_text("ping")
        message = recv(ws)
        while message["type"] == "ping":
            message = recv(ws)
        assert message["type"] == "pong"
        ws.send_text(json.dumps({"type": "ping"}))
        message = recv(ws)
        while message["type"] == "ping":
            message = recv(ws)
        assert message["type"] == "pong"


def test_listener_recovers_and_requests_resync(client, broker, agent, requester, create_ticket):
    with client.websocket_connect(ws_url(agent)) as ws:
        expect_ready(ws)
        broker.disconnect_all()
        resync = recv_event(ws)
        assert resync["type"] == "resync"
        assert get_hub().stats()["failures"] == 1
        ticket = create_ticket(requester)
        event = recv_event(ws)
        assert event["ticket_id"] == ticket["id"]


def test_realtime_stats_requires_admin(client, broker, admin, agent):
    assert client.get("/realtime/stats", headers=agent.headers).status_code == 403
    response = client.get("/realtime/stats", headers=admin.headers)
    assert response.status_code == 200
    assert response.json()["connections"] == 0


# --------------------------------------------------------------------------- límites anti-abuso


def test_connections_per_user_are_limited(client, broker, agent, requester, monkeypatch):
    monkeypatch.setattr(realtime_settings, "max_connections_per_user", 2)
    with client.websocket_connect(ws_url(agent)) as first, client.websocket_connect(ws_url(agent)) as second:
        expect_ready(first)
        expect_ready(second)
        with client.websocket_connect(ws_url(agent)) as third:
            closed = recv_close(third)
        assert closed.code == 1013
        # Otro usuario no se ve afectado.
        with client.websocket_connect(ws_url(requester)) as other:
            expect_ready(other)


def test_client_message_flood_closes_connection(client, broker, agent, monkeypatch):
    monkeypatch.setattr(realtime_settings, "client_messages_per_minute", 5)
    with client.websocket_connect(ws_url(agent)) as ws:
        expect_ready(ws)
        for _ in range(10):
            ws.send_text("ping")
        closed = recv_close(ws)
    assert closed.code == 1008
