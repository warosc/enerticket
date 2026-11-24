"""Utilidades compartidas por las pruebas del módulo SLA (no contiene pruebas)."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.sla import BusinessCalendar, SlaInstance, SlaPolicy
from app.services.sla.engine import SpecCache, recompute_targets

SEED_MODULE = "app.seeds.10_sla"


def run_sla_seed() -> None:
    seed = importlib.import_module(SEED_MODULE)
    with SessionLocal() as session:
        seed.run(session)
        session.commit()


@pytest.fixture
def sla_seed() -> Iterator[dict[str, Any]]:
    """Aplica el seed SLA (calendarios, feriados y políticas por prioridad)."""
    run_sla_seed()
    with SessionLocal() as session:
        calendars = {c.name: c.id for c in session.scalars(select(BusinessCalendar)).all()}
        policies = {p.name: p.id for p in session.scalars(select(SlaPolicy)).all()}
    yield {"calendars": calendars, "policies": policies}


def instances_for(ticket_id: int, *, include_cancelled: bool = True) -> list[SlaInstance]:
    with SessionLocal() as session:
        stmt = select(SlaInstance).where(SlaInstance.ticket_id == ticket_id).order_by(SlaInstance.id)
        rows = list(session.scalars(stmt).all())
    if not include_cancelled:
        rows = [r for r in rows if r.status != "cancelled"]
    return rows


def current(ticket_id: int, metric: str) -> SlaInstance:
    rows = [r for r in instances_for(ticket_id, include_cancelled=False) if r.metric == metric]
    assert rows, f"El ticket {ticket_id} no tiene instancia {metric}"
    return rows[-1]


def shift_instance(instance_id: int, delta: timedelta, *, paused: bool = False) -> None:
    """Desplaza en el tiempo el inicio (y la pausa abierta) de una instancia y recalcula su objetivo."""
    with SessionLocal() as session:
        instance = session.get(SlaInstance, instance_id)
        assert instance is not None
        instance.started_at = instance.started_at + delta
        if paused and instance.paused_at is not None:
            instance.paused_at = instance.paused_at + delta
        recompute_targets(SpecCache(session).get(instance.calendar_id), instance)
        session.commit()


def parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
