"""Celery: ``app.tasks.celery_app``.

Autodescubre los módulos ``app/tasks/*.py``. Cada módulo define sus tareas con
``@celery_app.task(name="modulo.tarea")`` y puede exponer un diccionario
``beat_schedule`` que se fusiona en la planificación de Celery beat::

    # app/tasks/sla.py
    from app.tasks import celery_app

    @celery_app.task(name="sla.check_deadlines")
    def check_deadlines() -> int: ...

    beat_schedule = {
        "sla-check-deadlines": {"task": "sla.check_deadlines", "schedule": 60.0},
    }

Worker: ``celery -A app.tasks.celery_app worker``; beat: ``celery -A app.tasks.celery_app beat``.
Los modelos se importan al cargar este paquete y los suscriptores de eventos al iniciar
worker/beat (señales), para que las tareas que emiten eventos disparen sus handlers.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import Celery
from celery.signals import beat_init, worker_init

from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery("servicedesk", broker=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    timezone=settings.tz_business,
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule_filename=settings.celery_beat_schedule_file,
    beat_schedule={},
)


def _register_task_modules() -> None:
    # Import diferido: los módulos de tareas hacen ``from app.tasks import celery_app``.
    import app.models  # noqa: F401  (registra todos los modelos)
    from app.core.discovery import import_submodules

    for module in import_submodules(__name__):
        schedule: dict[str, Any] | None = getattr(module, "beat_schedule", None)
        if not schedule:
            continue
        duplicated = set(schedule).intersection(celery_app.conf.beat_schedule)
        if duplicated:
            raise RuntimeError(
                f"Entradas de beat_schedule duplicadas en {module.__name__}: {sorted(duplicated)}"
            )
        celery_app.conf.beat_schedule.update(schedule)


def _load_plugins(**_: Any) -> None:
    from app.core.discovery import load_plugins

    load_plugins()


worker_init.connect(_load_plugins, weak=False)
beat_init.connect(_load_plugins, weak=False)

_register_task_modules()
