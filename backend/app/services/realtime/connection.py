"""Conexión WebSocket de un cliente: buzón de salida acotado, filtro, ping y expiración.

Una única tarea envía (los envíos concurrentes sobre un WebSocket no son seguros) y otra
lee los mensajes del cliente para detectar la desconexión y responder a ``ping``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.events import json_default
from app.core.security import Principal
from app.services.realtime.filtering import EventFilter

logger = logging.getLogger(__name__)

# Códigos de cierre (4000-4999 reservados para la aplicación).
CLOSE_NORMAL = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_POLICY_VIOLATION = 1008
CLOSE_INTERNAL_ERROR = 1011
CLOSE_TRY_AGAIN_LATER = 1013
CLOSE_SESSION_EXPIRED = 4000
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403

CONTROL_PING = "ping"
CONTROL_PONG = "pong"
CONTROL_READY = "ready"
CONTROL_RESYNC = "resync"


class WebSocketLike(Protocol):
    async def send_text(self, data: str) -> None: ...

    async def receive(self) -> dict[str, Any]: ...

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


def truncate_reason(reason: str, limit: int = 120) -> str:
    """El motivo de cierre admite como máximo 123 bytes UTF-8."""
    encoded = reason.encode("utf-8")
    if len(encoded) <= limit:
        return reason
    return encoded[:limit].decode("utf-8", errors="ignore")


def dumps(message: dict[str, Any]) -> str:
    return json.dumps(message, default=json_default, separators=(",", ":"), ensure_ascii=False)


@dataclass
class OutboundEvent:
    payload: dict[str, Any]
    raw: str


@dataclass
class OutboundControl:
    message: dict[str, Any]


class CloseRequested(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass
class ConnectionStats:
    delivered: int = 0
    filtered: int = 0
    connected_at: float = field(default_factory=time.time)


class ClientConnection:
    """Conexión de un usuario. ``offer`` se llama desde el event loop (no bloquea)."""

    def __init__(
        self,
        websocket: WebSocketLike,
        principal: Principal,
        event_filter: EventFilter,
        *,
        expires_at: float | None = None,
        queue_size: int = 500,
        ping_interval: float = 25.0,
        send_timeout: float = 10.0,
        max_client_messages_per_minute: int = 60,
    ) -> None:
        self.websocket = websocket
        self.max_client_messages_per_minute = max(1, max_client_messages_per_minute)
        self._client_window_start = time.monotonic()
        self._client_messages = 0
        self.principal = principal
        self.filter = event_filter
        self.expires_at = expires_at
        self.queue_size = max(1, queue_size)
        self.ping_interval = ping_interval
        self.send_timeout = send_timeout
        self.stats = ConnectionStats()
        self._buffer: deque[OutboundEvent | OutboundControl] = deque()
        self._signal = asyncio.Event()
        self._close_request: CloseRequested | None = None
        self._timers: list[asyncio.TimerHandle] = []

    # ------------------------------------------------------------------ productor

    @property
    def closing(self) -> bool:
        return self._close_request is not None

    @property
    def pending(self) -> int:
        return len(self._buffer)

    def offer(self, item: OutboundEvent | OutboundControl, *, front: bool = False) -> bool:
        """Encola un mensaje; si el buzón está lleno, solicita cerrar (cliente lento)."""
        if self._close_request is not None:
            return False
        if len(self._buffer) >= self.queue_size:
            logger.warning(
                "Conexión WebSocket de %s saturada (%s mensajes pendientes); se cierra",
                self.principal.username,
                len(self._buffer),
            )
            self.request_close(CLOSE_TRY_AGAIN_LATER, "Cliente demasiado lento; reconecte")
            return False
        if front:
            self._buffer.appendleft(item)
        else:
            self._buffer.append(item)
        self._signal.set()
        return True

    def offer_event(self, payload: dict[str, Any], raw: str) -> bool:
        return self.offer(OutboundEvent(payload, raw))

    def offer_control(self, message: dict[str, Any], *, front: bool = False) -> bool:
        return self.offer(OutboundControl(message), front=front)

    def request_close(self, code: int, reason: str) -> None:
        if self._close_request is None:
            self._close_request = CloseRequested(code, truncate_reason(reason))
            self._signal.set()

    # ------------------------------------------------------------------ consumidor

    async def _next(self) -> OutboundEvent | OutboundControl:
        while True:
            if self._close_request is not None:
                raise self._close_request
            if self._buffer:
                return self._buffer.popleft()
            self._signal.clear()
            await self._signal.wait()

    async def _send(self, text: str) -> None:
        await asyncio.wait_for(self.websocket.send_text(text), timeout=self.send_timeout)

    async def _send_loop(self) -> None:
        while True:
            item = await self._next()
            if isinstance(item, OutboundControl):
                await self._send(dumps(item.message))
                continue
            decided = await self.filter.decide(item.payload)
            if decided is None:
                self.stats.filtered += 1
                continue
            await self._send(item.raw if decided is item.payload else dumps(decided))
            self.stats.delivered += 1

    async def _receive_loop(self) -> None:
        while True:
            message = await self.websocket.receive()
            kind = message.get("type")
            if kind == "websocket.disconnect":
                return
            if kind != "websocket.receive":
                continue
            if self._client_flooding():
                logger.warning(
                    "WebSocket de %s cerrado: demasiados mensajes entrantes", self.principal.username
                )
                self.request_close(CLOSE_POLICY_VIOLATION, "Demasiados mensajes")
                raise CloseRequested(CLOSE_POLICY_VIOLATION, "Demasiados mensajes")
            text = message.get("text")
            if text is None:
                continue  # mensajes binarios: no se esperan
            if self._is_ping(text):
                self.offer_control({"type": CONTROL_PONG, "ts": time.time()})

    def _client_flooding(self) -> bool:
        """Límite de frecuencia de mensajes entrantes (ventana fija de 60 s)."""
        now = time.monotonic()
        if now - self._client_window_start >= 60.0:
            self._client_window_start = now
            self._client_messages = 0
        self._client_messages += 1
        return self._client_messages > self.max_client_messages_per_minute

    @staticmethod
    def _is_ping(text: str) -> bool:
        stripped = text.strip()
        if stripped == CONTROL_PING:
            return True
        if len(stripped) > 256 or not stripped.startswith("{"):
            return False
        try:
            data = json.loads(stripped)
        except ValueError:
            return False
        return isinstance(data, dict) and data.get("type") == CONTROL_PING

    def _schedule_ping(self) -> None:
        if self._close_request is not None or self.ping_interval <= 0:
            return
        loop = asyncio.get_running_loop()
        self._timers.append(loop.call_later(self.ping_interval, self._ping))

    def _ping(self) -> None:
        self.offer_control({"type": CONTROL_PING, "ts": time.time()})
        self._schedule_ping()

    def _start_timers(self) -> None:
        loop = asyncio.get_running_loop()
        if self.expires_at is not None:
            delay = max(0.0, self.expires_at - time.time())
            self._timers.append(
                loop.call_later(
                    delay,
                    self.request_close,
                    CLOSE_SESSION_EXPIRED,
                    "Sesión expirada; reconecte con un token nuevo",
                )
            )
        self._schedule_ping()

    def _cancel_timers(self) -> None:
        for handle in self._timers:
            handle.cancel()
        self._timers.clear()

    async def run(self) -> None:
        """Atiende la conexión hasta que el cliente se desconecta o se solicita el cierre."""
        self._start_timers()
        sender = asyncio.create_task(self._send_loop(), name="ws-sender")
        receiver = asyncio.create_task(self._receive_loop(), name="ws-receiver")
        close: CloseRequested | None = None
        client_gone = False
        try:
            done, _ = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                exc = None if receiver.cancelled() else receiver.exception()
                if isinstance(exc, CloseRequested):
                    close = exc  # el servidor cierra (p.ej. cliente que inunda de mensajes)
                else:
                    client_gone = True
                    if exc is not None:
                        logger.debug("Lectura WebSocket terminó con error: %r", exc)
            if sender in done:
                exc = None if sender.cancelled() else sender.exception()
                if isinstance(exc, CloseRequested):
                    close = close or exc
                elif isinstance(exc, asyncio.TimeoutError):
                    close = CloseRequested(CLOSE_TRY_AGAIN_LATER, "Tiempo de envío agotado")
                elif exc is not None:
                    # Error al enviar: normalmente el cliente ya se fue.
                    logger.debug("Envío WebSocket terminó con error: %r", exc)
                    client_gone = True
        finally:
            self._cancel_timers()
            pending = {task for task in (sender, receiver) if not task.done()}
            for task in pending:
                task.cancel()
            if pending:
                # asyncio.wait (no gather): si la tarea actual se cancela, la excepción
                # conserva el mensaje original y los cancel scopes de anyio la reconocen.
                await asyncio.wait(pending)
            for task in (sender, receiver):
                if task.done() and not task.cancelled():
                    task.exception()  # marca la excepción como recuperada (sin avisos)
        if not client_gone:
            close = close or CloseRequested(CLOSE_NORMAL, "")
            try:
                await self.websocket.close(code=close.code, reason=close.reason or None)
            except Exception:  # el cliente pudo cerrarse en paralelo
                logger.debug("No se pudo cerrar el WebSocket", exc_info=True)
