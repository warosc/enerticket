"""Suscriptor ``"*"`` que evalúa las reglas de automatización activas para cada evento."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.events import Event, subscribe
from app.services.automation import engine


@subscribe("*")
def run_automation_rules(session: Session, event: Event) -> None:
    engine.process_event(session, event)
