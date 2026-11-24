"""Parámetros del canal de tiempo real (WebSocket ``/ws``).

Todos tienen valores por defecto razonables para producción y pueden ajustarse por
variables de entorno opcionales (no forman parte de ``app.core.config``):

- ``WS_PING_INTERVAL_SECONDS`` (25): intervalo de los mensajes ``{"type": "ping"}``.
- ``WS_QUEUE_SIZE`` (500): eventos pendientes por conexión antes de cerrarla por lenta.
- ``WS_MAX_CONNECTION_SECONDS`` (43200): duración máxima de una conexión (también en
  modo dev, donde no hay expiración de token); el cliente reconecta con identidad fresca.
- ``WS_SEND_TIMEOUT_SECONDS`` (10): tiempo máximo para entregar un mensaje a un cliente.
- ``WS_VISIBILITY_CACHE_SECONDS`` (30): caché por conexión de la visibilidad de tickets
  calculada con BD (extensiones de visibilidad, p.ej. aprobadores).
- ``WS_REDIS_BACKOFF_MAX_SECONDS`` (30): espera máxima entre reintentos de conexión a Redis.
- ``WS_MAX_CONNECTIONS_PER_USER`` (8): conexiones simultáneas por usuario y proceso API; las
  siguientes se cierran con 1013 (pestañas de más, scripts abusivos).
- ``WS_CLIENT_MESSAGES_PER_MINUTE`` (60): mensajes entrantes por conexión y minuto (el cliente
  solo envía pings); al superarlo la conexión se cierra con 1008.
- ``WS_MAX_MESSAGE_BYTES`` (16384): tamaño máximo de trama entrante. Lo aplica uvicorn
  (``--ws-max-size`` en ``docker-entrypoint.sh``) antes de leer el mensaje completo.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Valor inválido para %s=%r; se usa %s", name, raw, default)
        return default
    return min(max(value, minimum), maximum)


@dataclass
class RealtimeSettings:
    ping_interval: float = 25.0
    queue_size: int = 500
    max_connection_seconds: float = 43_200.0
    send_timeout: float = 10.0
    visibility_cache_seconds: float = 30.0
    negative_cache_seconds: float = 5.0
    redis_backoff_initial: float = 0.5
    redis_backoff_max: float = 30.0
    subscribe_wait_seconds: float = 3.0
    max_token_length: int = 16_384
    max_connections_per_user: int = 8
    client_messages_per_minute: int = 60

    @classmethod
    def from_env(cls) -> "RealtimeSettings":
        return cls(
            ping_interval=_env_float("WS_PING_INTERVAL_SECONDS", 25.0, 1.0, 300.0),
            queue_size=int(_env_float("WS_QUEUE_SIZE", 500, 10, 100_000)),
            max_connection_seconds=_env_float("WS_MAX_CONNECTION_SECONDS", 43_200.0, 60.0, 7 * 86_400.0),
            send_timeout=_env_float("WS_SEND_TIMEOUT_SECONDS", 10.0, 1.0, 120.0),
            visibility_cache_seconds=_env_float("WS_VISIBILITY_CACHE_SECONDS", 30.0, 0.0, 3600.0),
            redis_backoff_max=_env_float("WS_REDIS_BACKOFF_MAX_SECONDS", 30.0, 1.0, 600.0),
            max_connections_per_user=int(_env_float("WS_MAX_CONNECTIONS_PER_USER", 8, 1, 1000)),
            client_messages_per_minute=int(_env_float("WS_CLIENT_MESSAGES_PER_MINUTE", 60, 5, 6000)),
        )


realtime_settings = RealtimeSettings.from_env()
