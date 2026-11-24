"""Bus de eventos de dominio: historial, suscriptores síncronos, guards y publicación a Redis.

Contrato::

    emit(session, event_type, ticket, actor_id, data) -> None
        1) inserta ``ticket_history`` (``data["before"]`` / ``data["after"]`` si aplica;
           si no hay "after", se guarda el resto de ``data`` como after_json)
        2) llama a los handlers ``@subscribe(event_type)`` en la misma transacción
           (cada uno dentro de un SAVEPOINT: si falla se registra y se descarta su trabajo,
           salvo con EVENTS_RAISE_ERRORS=true, que propaga la excepción)
        3) encola la publicación en Redis (canal ``servicedesk.events``) que se envía
           DESPUÉS del commit de la sesión; si la transacción se revierte, no se publica.

    @subscribe("ticket.created")            # o varios tipos, o "*" para todos
    def on_created(session, event: Event): ...
    # firma alternativa aceptada: def handler(session, ticket, actor_id, data)

    @guard("ticket.transition")
    def check(session, ticket, actor: Principal, data: dict) -> None:
        raise HTTPException(409, "...")    # para bloquear

    run_guards(session, name, ticket, actor, data)
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, SessionTransaction

from app.core.config import settings
from app.core.db import utcnow
from app.core.enrich import (  # noqa: F401  (re-exportado: contrato del plan)
    collect_enrichments,
    register_ticket_enricher,
    unregister_ticket_enricher,
)
from app.models.core import Ticket, TicketHistory

if TYPE_CHECKING:
    from app.core.security import Principal

__all__ = [
    "Event",
    "collect_enrichments",
    "emit",
    "flush_publications",
    "get_redis",
    "guard",
    "publish_payload",
    "register_ticket_enricher",
    "remove_guard",
    "run_guards",
    "set_publisher",
    "subscribe",
    "to_jsonable",
    "unregister_ticket_enricher",
    "unsubscribe",
]

logger = logging.getLogger(__name__)

PENDING_KEY = "servicedesk_pending_events"
WILDCARD = "*"


@dataclass
class Event:
    """Evento entregado a los suscriptores."""

    type: str
    ticket: Ticket
    actor_id: int | None
    data: dict[str, Any]
    ts: datetime
    history_id: int | None
    payload: dict[str, Any]


Handler = Callable[..., None]
GuardFn = Callable[[Session, Ticket, "Principal", dict[str, Any]], None]
Publisher = Callable[[str, str], None]

_subscribers: dict[str, list[tuple[Handler, int]]] = {}
_guards: dict[str, list[GuardFn]] = {}


# --------------------------------------------------------------------------- serialización


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if isinstance(value, Enum):
        return value.value
    return str(value)


def to_jsonable(value: Any) -> Any:
    """Convierte ``value`` a tipos JSON nativos (datetime → ISO 8601, set → lista...)."""
    return json.loads(json.dumps(value, default=json_default))


# --------------------------------------------------------------------------- suscriptores


def _handler_arity(fn: Handler) -> int:
    params = [
        p
        for p in inspect.signature(fn).parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    count = len(params)
    if count not in (2, 4):
        raise TypeError(
            f"El suscriptor {fn!r} debe aceptar (session, event) o (session, ticket, actor_id, data)"
        )
    return count


def subscribe(*event_types: str) -> Callable[[Handler], Handler]:
    """Decorador: registra un handler síncrono para uno o varios tipos de evento ("*" = todos)."""
    if not event_types:
        raise ValueError("subscribe necesita al menos un tipo de evento")

    def decorator(fn: Handler) -> Handler:
        arity = _handler_arity(fn)
        for event_type in event_types:
            handlers = _subscribers.setdefault(event_type, [])
            if all(existing is not fn for existing, _ in handlers):
                handlers.append((fn, arity))
        return fn

    return decorator


def unsubscribe(event_type: str, fn: Handler) -> None:
    handlers = _subscribers.get(event_type, [])
    _subscribers[event_type] = [(h, a) for h, a in handlers if h is not fn]


def _handlers_for(event_type: str) -> list[tuple[Handler, int]]:
    return list(_subscribers.get(event_type, [])) + list(_subscribers.get(WILDCARD, []))


def _call_handler(session: Session, handler: Handler, arity: int, evt: Event) -> None:
    if arity == 2:
        handler(session, evt)
    else:
        handler(session, evt.ticket, evt.actor_id, evt.data)


def _dispatch(session: Session, handler: Handler, arity: int, evt: Event) -> None:
    if settings.events_raise_errors:
        _call_handler(session, handler, arity, evt)
        return
    pending: list[dict[str, Any]] = session.info.setdefault(PENDING_KEY, [])
    mark = len(pending)
    try:
        with session.begin_nested():
            _call_handler(session, handler, arity, evt)
    except Exception:
        # Se descarta lo que hizo el handler (incluidos eventos que encoló) y se continúa.
        del pending[mark:]
        logger.exception(
            "Falló el suscriptor %s para el evento %s (ticket %s)",
            getattr(handler, "__qualname__", handler),
            evt.type,
            evt.ticket.id,
        )


# --------------------------------------------------------------------------- guards


def guard(name: str) -> Callable[[GuardFn], GuardFn]:
    """Decorador: registra ``fn(session, ticket, actor, data)`` que puede lanzar HTTPException."""

    def decorator(fn: GuardFn) -> GuardFn:
        guards = _guards.setdefault(name, [])
        if fn not in guards:
            guards.append(fn)
        return fn

    return decorator


def remove_guard(name: str, fn: GuardFn) -> None:
    if fn in _guards.get(name, []):
        _guards[name].remove(fn)


def run_guards(
    session: Session, name: str, ticket: Ticket, actor: "Principal", data: dict[str, Any]
) -> None:
    """Ejecuta los guards registrados con ``name``; cualquier excepción bloquea la operación."""
    for fn in list(_guards.get(name, [])):
        fn(session, ticket, actor, data)


# --------------------------------------------------------------------------- emit


def emit(
    session: Session,
    event_type: str,
    ticket: Ticket,
    actor_id: int | None,
    data: dict[str, Any] | None,
) -> None:
    """Registra el evento en el historial, notifica suscriptores y encola su publicación."""
    if ticket.id is None:
        session.flush()
    clean: dict[str, Any] = to_jsonable(data or {})
    before = clean.get("before")
    if "after" in clean:
        after = clean.get("after")
    else:
        after = {k: v for k, v in clean.items() if k != "before"} or None
    ts = utcnow()
    history = TicketHistory(
        ticket_id=ticket.id,
        actor_id=actor_id,
        event_type=event_type,
        before_json=before,
        after_json=after,
        timestamp=ts,
    )
    session.add(history)
    session.flush()

    payload = {
        "type": event_type,
        "ticket_id": ticket.id,
        "ticket_key": ticket.public_key or f"ESD-{ticket.id}",
        "actor_id": actor_id,
        "data": clean,
        "ts": ts.isoformat(),
        "department_id": ticket.department_id,
        "team_id": ticket.team_id,
        "requester_id": ticket.requester_id,
        "assignee_id": ticket.assignee_id,
    }
    session.info.setdefault(PENDING_KEY, []).append(payload)

    evt = Event(
        type=event_type,
        ticket=ticket,
        actor_id=actor_id,
        data=clean,
        ts=ts,
        history_id=history.id,
        payload=payload,
    )
    for handler, arity in _handlers_for(event_type):
        _dispatch(session, handler, arity, evt)


# --------------------------------------------------------------------------- publicación


_publisher: Publisher | None = None
_redis_client: Any = None
_redis_lock = threading.Lock()
_redis_down_until = 0.0


def get_redis() -> Any:
    """Cliente Redis compartido (timeouts cortos). Lanza si la librería no está disponible."""
    global _redis_client
    if _redis_client is None:
        with _redis_lock:
            if _redis_client is None:
                import redis  # import diferido: no es necesario en pruebas

                _redis_client = redis.Redis.from_url(
                    settings.redis_url,
                    socket_connect_timeout=1,
                    socket_timeout=2,
                    health_check_interval=30,
                )
    return _redis_client


def _redis_publish(channel: str, message: str) -> None:
    global _redis_down_until, _redis_client
    now = time.monotonic()
    if now < _redis_down_until:
        logger.debug("Redis marcado como caído; evento no publicado")
        return
    try:
        get_redis().publish(channel, message)
    except Exception as exc:
        _redis_down_until = now + settings.events_redis_retry_seconds
        _redis_client = None
        logger.warning(
            "No se pudo publicar en Redis (%s); se reintentará en %ss",
            exc,
            settings.events_redis_retry_seconds,
        )


_QUEUE_MAXSIZE = 10_000
_publish_queue: "queue.Queue[tuple[str, str]]" = queue.Queue(maxsize=_QUEUE_MAXSIZE)
_publish_thread: threading.Thread | None = None
_publish_thread_pid: int | None = None
_publish_thread_lock = threading.Lock()


def _publish_worker() -> None:
    while True:
        channel, message = _publish_queue.get()
        try:
            _redis_publish(channel, message)
        except Exception:  # pragma: no cover - _redis_publish ya captura
            logger.exception("Error inesperado publicando en Redis")
        finally:
            _publish_queue.task_done()


def _ensure_publish_thread() -> None:
    global _publish_thread, _publish_thread_pid
    pid = os.getpid()
    if _publish_thread is not None and _publish_thread.is_alive() and _publish_thread_pid == pid:
        return
    with _publish_thread_lock:
        if _publish_thread is None or not _publish_thread.is_alive() or _publish_thread_pid != pid:
            _publish_thread = threading.Thread(
                target=_publish_worker, name="servicedesk-events-publisher", daemon=True
            )
            _publish_thread.start()
            _publish_thread_pid = pid


def _enqueue_redis_publish(channel: str, message: str) -> None:
    """Publicador por defecto: envía a Redis en un hilo de fondo para no bloquear la petición."""
    _ensure_publish_thread()
    try:
        _publish_queue.put_nowait((channel, message))
    except queue.Full:
        logger.warning("Cola de publicación de eventos llena; se descarta el evento")


def flush_publications(timeout: float = 5.0) -> bool:
    """Espera a que la cola de publicación se vacíe (útil al apagar o en pruebas)."""
    deadline = time.monotonic() + timeout
    while _publish_queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    return not _publish_queue.unfinished_tasks


def set_publisher(fn: Publisher | None) -> None:
    """Reemplaza el publicador (``fn(channel, message_json)``, síncrono); ``None`` restaura Redis."""
    global _publisher
    _publisher = fn


def publish_payload(payload: dict[str, Any]) -> None:
    """Publica un payload en el canal de eventos (tolerante a fallos; nunca lanza)."""
    message = json.dumps(payload, default=json_default)
    publisher = _publisher or _enqueue_redis_publish
    try:
        publisher(settings.events_channel, message)
    except Exception:
        logger.exception("Falló la publicación del evento %s", payload.get("type"))


def _is_root(session: Session) -> bool:
    # Durante after_commit, la transacción actual de la sesión es la que se confirma.
    return not session.in_nested_transaction()


@sa_event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    if not _is_root(session):
        return  # liberación de SAVEPOINT: esperar al commit real
    pending = session.info.pop(PENDING_KEY, None)
    for payload in pending or ():
        publish_payload(payload)


@sa_event.listens_for(Session, "after_transaction_end")
def _discard_on_end(session: Session, transaction: SessionTransaction) -> None:
    # Al terminar la transacción raíz (rollback o cierre) se descartan los no publicados.
    if transaction.parent is None and not transaction.nested:
        session.info.pop(PENDING_KEY, None)
