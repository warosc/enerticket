"""Decide qué eventos del bus recibe cada conexión WebSocket.

Reglas (mismas que ``app.core.permissions``):

1. Comentarios ``internal`` (``ticket.commented`` con ``data.visibility == "internal"``)
   solo para admin y agentes que atienden el ticket (área del payload o asignado).
2. Notificaciones personales (``notification.*`` con ``data.recipient_id`` o
   ``data.user_id``) solo para su destinatario.
3. Regla base sin BD: ``can_view_event(principal, payload)`` con los campos de alcance
   del payload (department_id, team_id, requester_id, assignee_id).
4. Si la regla base no lo permite y existen extensiones de visibilidad aplicables al
   usuario (p.ej. aprobadores), se consulta la BD fuera del event loop (threadpool) con
   ``can_view_event(principal, payload, session)`` (ver ``visibility``: lotes en un hilo
   con límite propio). El resultado se cachea por ticket.
5. Si el usuario dejó de ver el ticket por este mismo evento (p.ej. cambio de
   departamento), recibe un aviso redactado ``ticket.hidden`` sin datos del ticket para
   que lo retire de su vista.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.permissions import can_view_event, can_view_internal_scope, can_view_scope
from app.core.security import Principal

logger = logging.getLogger(__name__)

HIDDEN_EVENT = "ticket.hidden"
SCOPE_FIELDS = ("department_id", "team_id", "requester_id", "assignee_id")
# Eventos que pueden cambiar la visibilidad por extensiones: no se usa caché negativa.
VISIBILITY_CHANGING_PREFIXES = ("approval.",)
_CACHE_MAX_ENTRIES = 2048

DbVisibilityCheck = Callable[[Principal, dict[str, Any]], Awaitable[bool]]


def principal_has_extensions(principal: Principal) -> bool:
    """¿Alguna extensión de visibilidad aplica a este usuario? (sin BD).

    Si no hay extensiones aplicables, la regla base es definitiva y se evita abrir
    sesiones de BD por cada evento ajeno.
    """
    if principal.is_admin:
        return False
    try:
        from app.core import permissions

        return bool(permissions._extension_clauses(principal))  # noqa: SLF001 - solo lectura
    except Exception:  # pragma: no cover - si cambia la API interna, se consulta la BD
        logger.debug("No se pudieron evaluar las extensiones de visibilidad", exc_info=True)
        return True


def is_internal_comment(payload: dict[str, Any]) -> bool:
    data = payload.get("data")
    return (
        payload.get("type") == "ticket.commented"
        and isinstance(data, dict)
        and data.get("visibility") == "internal"
    )


def targeted_recipient(payload: dict[str, Any]) -> int | None:
    """Destinatario de una notificación personal, o None si el evento no es personal."""
    event_type = str(payload.get("type") or "")
    if not event_type.startswith("notification."):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    for key in ("recipient_id", "user_id"):
        value = data.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def previous_scope(payload: dict[str, Any]) -> dict[str, int | None] | None:
    """Alcance del ticket ANTES del evento (si ``data.before`` trae campos de alcance)."""
    data = payload.get("data")
    before = data.get("before") if isinstance(data, dict) else None
    if not isinstance(before, dict) or not any(field in before for field in SCOPE_FIELDS):
        return None
    scope = {field: _int_or_none(payload.get(field)) for field in SCOPE_FIELDS}
    for field in SCOPE_FIELDS:
        if field in before:
            scope[field] = _int_or_none(before.get(field))
    return scope


def hidden_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Aviso redactado: el usuario ya no puede ver el ticket (no incluye datos nuevos)."""
    return {
        "type": HIDDEN_EVENT,
        "ticket_id": payload.get("ticket_id"),
        "ticket_key": payload.get("ticket_key"),
        "actor_id": None,
        "data": {"reason": "scope_changed", "source_type": payload.get("type")},
        "ts": payload.get("ts"),
        "department_id": None,
        "team_id": None,
        "requester_id": None,
        "assignee_id": None,
    }


class EventFilter:
    """Filtro de eventos por conexión (con caché de visibilidad calculada en BD)."""

    def __init__(
        self,
        principal: Principal,
        *,
        db_check: DbVisibilityCheck | None = None,
        cache_seconds: float = 30.0,
        negative_cache_seconds: float = 5.0,
        has_extensions: bool | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.principal = principal
        self._db_check = db_check
        self._cache_seconds = cache_seconds
        self._negative_cache_seconds = negative_cache_seconds
        self._clock = clock
        self.has_extensions = (
            principal_has_extensions(principal) if has_extensions is None else has_extensions
        )
        self._cache: OrderedDict[int, tuple[bool, float]] = OrderedDict()

    # ------------------------------------------------------------------ caché

    def _cached(self, ticket_id: int) -> bool | None:
        entry = self._cache.get(ticket_id)
        if entry is None:
            return None
        visible, expires = entry
        if self._clock() >= expires:
            self._cache.pop(ticket_id, None)
            return None
        self._cache.move_to_end(ticket_id)
        return visible

    def _remember(self, ticket_id: int, visible: bool) -> None:
        ttl = self._cache_seconds if visible else min(self._negative_cache_seconds, self._cache_seconds)
        if ttl <= 0:
            return
        self._cache[ticket_id] = (visible, self._clock() + ttl)
        self._cache.move_to_end(ticket_id)
        while len(self._cache) > _CACHE_MAX_ENTRIES:
            self._cache.popitem(last=False)

    def invalidate(self, ticket_id: int | None = None) -> None:
        if ticket_id is None:
            self._cache.clear()
        else:
            self._cache.pop(ticket_id, None)

    # ------------------------------------------------------------------ decisión

    async def _visible_by_extensions(self, payload: dict[str, Any]) -> bool:
        ticket_id = _int_or_none(payload.get("ticket_id"))
        if ticket_id is None or self._db_check is None or not self.has_extensions:
            return False
        event_type = str(payload.get("type") or "")
        use_cache = not event_type.startswith(VISIBILITY_CHANGING_PREFIXES)
        if use_cache:
            cached = self._cached(ticket_id)
            if cached is not None:
                return cached
        try:
            visible = await self._db_check(self.principal, payload)
        except Exception:
            # Ante un error de BD no se filtra información: el evento no se entrega.
            logger.warning(
                "No se pudo verificar la visibilidad del ticket %s para %s",
                ticket_id,
                self.principal.username,
                exc_info=True,
            )
            return False
        self._remember(ticket_id, visible)
        return visible

    async def decide(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Devuelve el payload a enviar (el original o uno redactado) o None para omitirlo."""
        if not isinstance(payload, dict) or not payload.get("type"):
            return None
        principal = self.principal

        if is_internal_comment(payload) and not can_view_internal_scope(
            principal,
            department_id=_int_or_none(payload.get("department_id")),
            team_id=_int_or_none(payload.get("team_id")),
            assignee_id=_int_or_none(payload.get("assignee_id")),
        ):
            return None

        recipient = targeted_recipient(payload)
        if recipient is not None:
            return payload if recipient == principal.user_id else None

        if can_view_event(principal, payload):
            return payload

        if await self._visible_by_extensions(payload):
            return payload

        before = previous_scope(payload)
        if before is not None and can_view_scope(principal, **before):
            ticket_id = _int_or_none(payload.get("ticket_id"))
            if ticket_id is not None:
                self.invalidate(ticket_id)
            return hidden_payload(payload)
        return None
