"""Tiempo real: hub de eventos por proceso, filtrado por permisos y conexiones WebSocket.

- ``hub``: listener compartido (Redis ``servicedesk.events``) con fan-out a conexiones locales.
- ``connection``: buzón acotado por conexión, ping, expiración y cierre limpio.
- ``filtering``: reglas de visibilidad (``can_view_event``, comentarios internos, avisos).
- ``auth``: Origin y autenticación del handshake (OIDC por ``?token=`` o identidad dev).
- ``sources``: fuente Redis (producción) y broker en memoria (pruebas).
"""

from app.services.realtime.connection import ClientConnection
from app.services.realtime.filtering import EventFilter
from app.services.realtime.hub import RealtimeHub, get_hub, set_hub
from app.services.realtime.sources import InMemoryBroker, RedisEventSource

__all__ = [
    "ClientConnection",
    "EventFilter",
    "InMemoryBroker",
    "RealtimeHub",
    "RedisEventSource",
    "get_hub",
    "set_hub",
]
