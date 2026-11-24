"""Ayudas para seeds de «una sola vez» (tabla ``seed_state``).

Uso en un seed::

    state = get_seed_state(session, "sla.defaults.v1")
    if state is None:
        ...crear datos iniciales...
        mark_seed_applied(session, "sla.defaults.v1", {"business_calendar_id": cal.id})

La marca se escribe en la misma transacción que los datos (el runner confirma cada seed),
así un fallo no deja la marca sin los datos ni al revés.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models.seed_state import SeedState


def get_seed_state(session: Session, key: str) -> SeedState | None:
    return session.get(SeedState, key)


def mark_seed_applied(session: Session, key: str, data: dict[str, Any] | None = None) -> SeedState:
    state = session.get(SeedState, key)
    if state is None:
        state = SeedState(key=key, applied_at=utcnow(), data=data)
        session.add(state)
    else:
        state.data = data
    session.flush()
    return state
