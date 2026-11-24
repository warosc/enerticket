"""Verificación de visibilidad con BD (extensiones de visibilidad) agrupada por lotes.

Cuando un evento no es visible por la regla base, cada conexión con extensiones
aplicables (p.ej. aprobadores) necesita consultar la BD. En lugar de una tarea de
threadpool por conexión, las consultas que llegan en la misma vuelta del event loop se
agrupan y se resuelven en un único hilo con una sola sesión. Un ``CapacityLimiter`` propio
evita que estas consultas compitan con los endpoints síncronos de la API por el
threadpool compartido.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Any

import anyio
import anyio.to_thread

from app.core.security import Principal

logger = logging.getLogger(__name__)

CheckItem = tuple[Principal, dict[str, Any]]
CheckMany = Callable[[Sequence[CheckItem]], list[bool]]


def check_many_with_session(items: Sequence[CheckItem]) -> list[bool]:
    """Evalúa ``can_view_event`` para varios (principal, payload) con una sola sesión."""
    from app.core.db import SessionLocal
    from app.core.permissions import can_view_event

    results: list[bool] = []
    cache: dict[tuple[int, Any], bool] = {}
    with SessionLocal() as session:
        for principal, payload in items:
            key = (principal.user_id, payload.get("ticket_id"))
            if key not in cache:
                cache[key] = bool(can_view_event(principal, payload, session))
            results.append(cache[key])
    return results


class BatchedVisibilityChecker:
    def __init__(
        self,
        check_many: CheckMany = check_many_with_session,
        *,
        max_threads: int = 4,
        max_batch: int = 500,
    ) -> None:
        self._check_many = check_many
        self._max_threads = max(1, max_threads)
        self._max_batch = max(1, max_batch)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._limiter: anyio.CapacityLimiter | None = None
        self._pending: list[tuple[Principal, dict[str, Any], asyncio.Future[bool]]] = []
        self._scheduled = False
        self._tasks: set[asyncio.Future[None]] = set()
        self.batches = 0

    def _bind_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop = loop
            self._limiter = anyio.CapacityLimiter(self._max_threads)
            self._pending = []
            self._scheduled = False
        return loop

    async def __call__(self, principal: Principal, payload: dict[str, Any]) -> bool:
        loop = self._bind_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending.append((principal, payload, future))
        if not self._scheduled:
            self._scheduled = True
            # call_soon: las conexiones que despertaron con el mismo evento se suman al lote.
            loop.call_soon(self._flush)
        return await future

    def _flush(self) -> None:
        self._scheduled = False
        while self._pending:
            batch, self._pending = self._pending[: self._max_batch], self._pending[self._max_batch :]
            task = asyncio.ensure_future(self._run(batch))
            self._tasks.add(task)  # referencia fuerte hasta que termine
            task.add_done_callback(self._tasks.discard)

    async def _run(self, batch: list[tuple[Principal, dict[str, Any], asyncio.Future[bool]]]) -> None:
        live = [(p, payload, f) for p, payload, f in batch if not f.done()]
        if not live:
            return
        self.batches += 1
        try:
            results = await anyio.to_thread.run_sync(
                self._check_many, [(p, payload) for p, payload, _ in live], limiter=self._limiter
            )
            if len(results) != len(live):
                raise RuntimeError("check_many devolvió un número de resultados distinto")
        except BaseException as exc:  # noqa: BLE001 - se propaga a cada solicitante
            for _, _, future in live:
                if not future.done():
                    future.set_exception(exc if isinstance(exc, Exception) else RuntimeError(str(exc)))
            if not isinstance(exc, Exception):
                raise
            return
        for (_, _, future), visible in zip(live, results):
            if not future.done():
                future.set_result(bool(visible))
