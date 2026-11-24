"""Fuentes de eventos para el hub: Redis pub/sub (producción) y un broker en memoria (pruebas).

Una fuente implementa ``stream(on_subscribed)``: un generador asíncrono de mensajes JSON
(str) que llama a ``on_subscribed()`` cuando la suscripción está activa. Si la conexión se
pierde, el generador lanza una excepción y el hub reintenta con backoff exponencial.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable
from typing import Protocol

logger = logging.getLogger(__name__)


class EventSource(Protocol):
    def stream(self, on_subscribed: Callable[[], None]) -> AsyncIterator[str]: ...


class SourceClosed(ConnectionError):
    """La fuente terminó inesperadamente (se trata como desconexión)."""


class RedisEventSource:
    """Suscripción a un canal de Redis con ``redis.asyncio``."""

    def __init__(
        self,
        url: str,
        channel: str,
        *,
        poll_timeout: float = 5.0,
        health_check_interval: int = 25,
        connect_timeout: float = 5.0,
    ) -> None:
        self.url = url
        self.channel = channel
        self.poll_timeout = poll_timeout
        self.health_check_interval = health_check_interval
        self.connect_timeout = connect_timeout

    async def stream(self, on_subscribed: Callable[[], None]) -> AsyncIterator[str]:
        import redis.asyncio as aioredis  # import diferido: no se necesita en pruebas

        client = aioredis.Redis.from_url(
            self.url,
            socket_connect_timeout=self.connect_timeout,
            socket_keepalive=True,
            health_check_interval=self.health_check_interval,
        )
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.subscribe(self.channel)
            logger.info("Suscrito al canal Redis %s", self.channel)
            on_subscribed()
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=self.poll_timeout
                )
                if message is None or message.get("type") != "message":
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    try:
                        data = data.decode("utf-8")
                    except UnicodeDecodeError:
                        logger.warning("Mensaje no UTF-8 en %s; se descarta", self.channel)
                        continue
                if isinstance(data, str):
                    yield data
        finally:
            for closer in (pubsub.aclose, client.aclose):
                try:
                    await closer()
                except Exception:  # pragma: no cover - limpieza tolerante
                    logger.debug("Error cerrando la conexión Redis", exc_info=True)


class InMemoryBroker:
    """Pub/sub en memoria, seguro entre hilos (pruebas y entornos sin Redis).

    ``publish(channel, message)`` puede llamarse desde cualquier hilo (p.ej. el publicador
    de ``app.core.events`` tras el commit); los mensajes se entregan en el event loop de
    cada suscriptor.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[str | None]]]] = {}
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, message: str) -> int:
        with self._lock:
            self.published.append((channel, message))
            targets = list(self._subscribers.get(channel, ()))
        delivered = 0
        for loop, queue in targets:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, message)
                delivered += 1
            except RuntimeError:  # loop cerrado
                continue
        return delivered

    def disconnect_all(self) -> None:
        """Simula una caída: todos los suscriptores reciben un error de conexión."""
        with self._lock:
            targets = [item for items in self._subscribers.values() for item in items]
        for loop, queue in targets:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, None)
            except RuntimeError:
                continue

    def subscriber_count(self, channel: str) -> int:
        with self._lock:
            return len(self._subscribers.get(channel, ()))

    def source(self, channel: str) -> "InMemorySource":
        return InMemorySource(self, channel)

    def _add(self, channel: str, entry: tuple[asyncio.AbstractEventLoop, asyncio.Queue[str | None]]) -> None:
        with self._lock:
            self._subscribers.setdefault(channel, []).append(entry)

    def _remove(self, channel: str, entry: tuple[asyncio.AbstractEventLoop, asyncio.Queue[str | None]]) -> None:
        with self._lock:
            items = self._subscribers.get(channel, [])
            if entry in items:
                items.remove(entry)


class InMemorySource:
    def __init__(self, broker: InMemoryBroker, channel: str) -> None:
        self.broker = broker
        self.channel = channel

    async def stream(self, on_subscribed: Callable[[], None]) -> AsyncIterator[str]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        entry = (asyncio.get_running_loop(), queue)
        self.broker._add(self.channel, entry)
        try:
            on_subscribed()
            while True:
                message = await queue.get()
                if message is None:
                    raise SourceClosed("Broker en memoria desconectado")
                yield message
        finally:
            self.broker._remove(self.channel, entry)
