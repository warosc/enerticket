"""Hub de tiempo real: un único listener de eventos por proceso con fan-out a las conexiones.

- El listener (Redis ``servicedesk.events``) se inicia con la primera conexión y se detiene
  cuando no quedan conexiones.
- Si la fuente falla, reintenta con backoff exponencial con jitter; al recuperarse envía
  ``{"type": "resync"}`` a los clientes para que recarguen (pudieron perder eventos).
- ``dispatch`` decodifica cada mensaje una sola vez y lo encola en cada conexión (no
  bloquea); el filtrado por permisos ocurre en la tarea de envío de cada conexión.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Callable
from typing import Any

from app.core.config import settings
from app.services.realtime.config import realtime_settings
from app.services.realtime.connection import (
    CLOSE_GOING_AWAY,
    CONTROL_RESYNC,
    ClientConnection,
)
from app.services.realtime.sources import EventSource, RedisEventSource, SourceClosed

logger = logging.getLogger(__name__)

SourceFactory = Callable[[], EventSource]


def default_source_factory() -> EventSource:
    return RedisEventSource(settings.redis_url, settings.events_channel)


class RealtimeHub:
    def __init__(
        self,
        source_factory: SourceFactory | None = None,
        *,
        backoff_initial: float | None = None,
        backoff_max: float | None = None,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.source_factory = source_factory or default_source_factory
        self.backoff_initial = backoff_initial if backoff_initial is not None else realtime_settings.redis_backoff_initial
        self.backoff_max = backoff_max if backoff_max is not None else realtime_settings.redis_backoff_max
        self._jitter = jitter
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connections: set[ClientConnection] = set()
        self._listener: asyncio.Task[None] | None = None
        self._subscribed: asyncio.Event | None = None
        self.dispatched = 0
        self.failures = 0
        self.last_error: str | None = None

    # ------------------------------------------------------------------ estado

    @property
    def connections(self) -> frozenset[ClientConnection]:
        return frozenset(self._connections)

    @property
    def subscribed(self) -> bool:
        return self._subscribed is not None and self._subscribed.is_set()

    def stats(self) -> dict[str, Any]:
        return {
            "connections": len(self._connections),
            "subscribed": self.subscribed,
            "dispatched": self.dispatched,
            "failures": self.failures,
            "last_error": self.last_error,
        }

    def _bind_loop(self) -> None:
        """Asocia el hub al event loop actual (si cambió, descarta el estado anterior)."""
        loop = asyncio.get_running_loop()
        if self._loop is loop:
            return
        if self._loop is not None:
            logger.debug("El hub de tiempo real cambia de event loop; se reinicia su estado")
        self._loop = loop
        self._connections = set()
        self._listener = None
        self._subscribed = asyncio.Event()

    # ------------------------------------------------------------------ conexiones

    def connections_for(self, user_id: int) -> int:
        """Conexiones abiertas de un usuario en este proceso."""
        return sum(1 for c in self._connections if c.principal.user_id == user_id and not c.closing)

    async def register(self, connection: ClientConnection) -> None:
        self._bind_loop()
        self._connections.add(connection)
        self._ensure_listener()

    async def unregister(self, connection: ClientConnection) -> None:
        self._connections.discard(connection)
        if not self._connections and self._listener is not None:
            listener, self._listener = self._listener, None
            if self._subscribed is not None:
                self._subscribed.clear()
            await self._stop_task(listener)

    async def wait_subscribed(self, timeout: float) -> bool:
        """Espera (como máximo ``timeout``) a que la suscripción esté activa."""
        self._bind_loop()
        assert self._subscribed is not None
        if self._subscribed.is_set():
            return True
        try:
            await asyncio.wait_for(self._subscribed.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return True

    async def serve(
        self,
        connection: ClientConnection,
        *,
        ready: dict[str, Any] | None = None,
        subscribe_wait: float | None = None,
    ) -> None:
        """Registra la conexión, la atiende hasta que termina y la desregistra.

        Si se indica ``ready``, se envía como primer mensaje (con ``subscribed``) cuando la
        suscripción al bus está activa o tras ``subscribe_wait`` segundos.
        """
        await self.register(connection)
        try:
            if ready is not None:
                wait = realtime_settings.subscribe_wait_seconds if subscribe_wait is None else subscribe_wait
                subscribed = await self.wait_subscribed(wait)
                connection.offer_control({**ready, "subscribed": subscribed}, front=True)
            await connection.run()
        finally:
            await self.unregister(connection)

    async def shutdown(self) -> None:
        """Cierra las conexiones (1001) y detiene el listener (apagado del proceso)."""
        if self._loop is not asyncio.get_running_loop():
            return
        for connection in list(self._connections):
            connection.request_close(CLOSE_GOING_AWAY, "Servidor reiniciándose")
        if self._listener is not None:
            listener, self._listener = self._listener, None
            await self._stop_task(listener)

    @staticmethod
    async def _stop_task(task: asyncio.Task[None]) -> None:
        """Cancela ``task`` y espera a que termine sin tragarse una cancelación propia."""
        task.cancel()
        # asyncio.wait no propaga el resultado de la tarea, pero sí la cancelación de la
        # tarea actual (necesario para que los cancel scopes de anyio funcionen).
        await asyncio.wait({task})

    # ------------------------------------------------------------------ fan-out

    def dispatch(self, message: str | bytes) -> int:
        """Entrega un mensaje JSON del bus a todas las conexiones locales. Devuelve cuántas."""
        try:
            raw = message.decode("utf-8") if isinstance(message, bytes) else message
            payload = json.loads(raw)
        except (UnicodeDecodeError, ValueError):
            logger.warning("Mensaje de evento inválido; se descarta")
            return 0
        if not isinstance(payload, dict) or not payload.get("type"):
            logger.warning("Evento sin tipo; se descarta")
            return 0
        self.dispatched += 1
        delivered = 0
        for connection in list(self._connections):
            if connection.offer_event(payload, raw):
                delivered += 1
        return delivered

    def broadcast_control(self, message: dict[str, Any]) -> None:
        for connection in list(self._connections):
            connection.offer_control(message)

    # ------------------------------------------------------------------ listener

    def _ensure_listener(self) -> None:
        if self._listener is None or self._listener.done():
            self._listener = asyncio.create_task(self._run_listener(), name="realtime-listener")

    def _backoff(self, attempt: int) -> float:
        ceiling = min(self.backoff_max, self.backoff_initial * (2 ** min(attempt, 16)))
        # Jitter "equal": entre la mitad y el total del techo.
        return ceiling / 2 + ceiling / 2 * self._jitter()

    async def _run_listener(self) -> None:
        assert self._subscribed is not None
        subscribed = self._subscribed
        attempt = 0
        had_failure = False

        def on_subscribed() -> None:
            nonlocal attempt, had_failure
            attempt = 0
            subscribed.set()
            if had_failure:
                had_failure = False
                logger.info("Suscripción de eventos restablecida; se solicita resincronizar")
                self.broadcast_control({"type": CONTROL_RESYNC, "ts": time.time()})

        while True:
            try:
                source = self.source_factory()
                async for message in source.stream(on_subscribed):
                    self.dispatch(message)
                raise SourceClosed("La fuente de eventos terminó")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                subscribed.clear()
                had_failure = True
                self.failures += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                delay = self._backoff(attempt)
                attempt += 1
                log = logger.warning if attempt <= 3 or attempt % 10 == 0 else logger.debug
                log(
                    "Fuente de eventos no disponible (%s); reintento %s en %.1fs",
                    self.last_error,
                    attempt,
                    delay,
                )
                await asyncio.sleep(delay)


_hub: RealtimeHub | None = None


def get_hub() -> RealtimeHub:
    global _hub
    if _hub is None:
        _hub = RealtimeHub()
    return _hub


def set_hub(hub: RealtimeHub | None) -> None:
    """Reemplaza el hub del proceso (pruebas). ``None`` vuelve al hub con Redis."""
    global _hub
    _hub = hub
