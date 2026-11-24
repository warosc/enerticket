"""WebSocket ``/ws``: eventos de tickets en tiempo real filtrados por permisos.

Handshake:
- ``AUTH_MODE=oidc``: ``/ws?token=<access_token>`` (JWT de Keycloak).
- ``AUTH_MODE=dev``: ``/ws?dev_user=&dev_roles=&dev_groups=`` (solo laboratorio).
- El encabezado Origin (si existe) debe estar en ``CORS_ORIGINS``; si no, se rechaza (403).

Mensajes servidor → cliente (JSON):
- Eventos del bus tal cual (``type``, ``ticket_id``, ``ticket_key``, ``actor_id``, ``data``,
  ``ts``, ``department_id``, ``team_id``, ``requester_id``, ``assignee_id``) solo de tickets
  visibles; los comentarios internos solo llegan a agentes.
- ``{"type": "ticket.hidden", "ticket_id", "ticket_key", ...}`` cuando el usuario deja de
  ver un ticket por un cambio de alcance (sin datos nuevos del ticket).
- Control: ``ready`` (primer mensaje), ``ping`` (cada 25 s), ``pong`` (respuesta a un
  ``ping`` del cliente) y ``resync`` (el servidor pudo perder eventos: recargar).

Códigos de cierre: 4000 sesión expirada (reconectar con token nuevo), 4401 no autenticado,
4403 prohibido, 1008 política (Origin), 1013 reintentar más tarde (cliente lento,
servicio de identidad caído), 1001 servidor reiniciándose.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, WebSocket
from starlette.concurrency import run_in_threadpool

from app.core.security import AuthError, require_roles
from app.services.realtime.auth import authenticate, close_code_for, origin_allowed
from app.services.realtime.config import realtime_settings
from app.services.realtime.connection import (
    CLOSE_INTERNAL_ERROR,
    CLOSE_POLICY_VIOLATION,
    CLOSE_TRY_AGAIN_LATER,
    CONTROL_READY,
    ClientConnection,
    truncate_reason,
)
from app.services.realtime.filtering import EventFilter
from app.services.realtime.hub import get_hub
from app.services.realtime.log_redaction import install_token_redaction
from app.services.realtime.visibility import BatchedVisibilityChecker

logger = logging.getLogger(__name__)

install_token_redaction()


@asynccontextmanager
async def _lifespan(_: Any) -> AsyncIterator[None]:
    """Al apagar el proceso: cierra las conexiones (1001) y detiene el listener de Redis."""
    yield
    await get_hub().shutdown()


router = APIRouter(tags=["tiempo real"], lifespan=_lifespan)


# Consultas de visibilidad con BD agrupadas por lotes y con su propio límite de hilos.
db_visibility_check = BatchedVisibilityChecker()


@router.websocket("/ws")
async def events_socket(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if not origin_allowed(origin):
        logger.warning("WebSocket rechazado: Origin no permitido (%r)", origin[:200] if origin else origin)
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return

    params = dict(websocket.query_params)
    try:
        auth = await run_in_threadpool(authenticate, params)
    except AuthError as exc:
        # Se acepta para poder comunicar el motivo con un código de cierre (el navegador no
        # expone el estado HTTP de un handshake rechazado).
        await websocket.accept()
        await websocket.close(code=close_code_for(exc), reason=truncate_reason(exc.detail))
        return
    except Exception:
        logger.exception("Error autenticando WebSocket")
        await websocket.accept()
        await websocket.close(code=CLOSE_INTERNAL_ERROR, reason="Error interno")
        return

    await websocket.accept()
    principal = auth.principal
    if get_hub().connections_for(principal.user_id) >= realtime_settings.max_connections_per_user:
        logger.warning(
            "WebSocket rechazado: %s supera %s conexiones simultáneas",
            principal.username,
            realtime_settings.max_connections_per_user,
        )
        await websocket.close(
            code=CLOSE_TRY_AGAIN_LATER, reason="Demasiadas conexiones abiertas; cierre otras pestañas"
        )
        return
    connection = ClientConnection(
        websocket,
        principal,
        EventFilter(
            principal,
            db_check=db_visibility_check,
            cache_seconds=realtime_settings.visibility_cache_seconds,
            negative_cache_seconds=realtime_settings.negative_cache_seconds,
        ),
        expires_at=auth.expires_at,
        queue_size=realtime_settings.queue_size,
        ping_interval=realtime_settings.ping_interval,
        send_timeout=realtime_settings.send_timeout,
        max_client_messages_per_minute=realtime_settings.client_messages_per_minute,
    )
    ready = {
        "type": CONTROL_READY,
        "user_id": principal.user_id,
        "expires_at": auth.expires_at,
        "ping_interval": realtime_settings.ping_interval,
    }
    logger.debug("WebSocket conectado: %s", principal.username)
    try:
        await get_hub().serve(connection, ready=ready)
    finally:
        logger.debug(
            "WebSocket desconectado: %s (entregados=%s, filtrados=%s)",
            principal.username,
            connection.stats.delivered,
            connection.stats.filtered,
        )


@router.get("/realtime/stats", dependencies=[Depends(require_roles("admin"))])
def realtime_stats() -> dict[str, Any]:
    """Estado del hub de este proceso (conexiones, suscripción a Redis, fallos)."""
    return get_hub().stats()
